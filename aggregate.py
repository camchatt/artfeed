#!/usr/bin/env python3
"""Aggregate artist and creative opportunities into feed.json and feed.xml.

Run: python aggregate.py
Output lands in ./docs so GitHub Pages can serve it.
"""

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import feedparser
import requests
import yaml

from scrape import scrape
from socrata import pull_dataset
from geo import region_of

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "docs")
SITE_TITLE = "Creative Opportunities"
SITE_LINK = "https://example.com"
MAX_ITEMS = 400
STALE_AFTER_DAYS = 120
UA = {"User-Agent": "creative-opps-aggregator/1.0 (contact: you@example.com)"}

# ---------------------------------------------------------------- tagging

TAG_RULES = [
    ("grant", r"\bgrant|fellowship|stipend|bursary|funding|award\b"),
    ("rfp", r"\brfp\b|request for (proposal|qualification)|\brfq\b|\bcall for (artist|entries|proposal|submission)"),
    ("residency", r"\bresidenc(y|ies)\b|artist in residence"),
    ("job", r"\bjob|hiring|full[- ]time|part[- ]time|position|employment|salary\b"),
    ("commission", r"\bcommission|public art|mural\b"),
]

REJECT = re.compile(r"\b(nsfw|onlyfans|crypto giveaway|nft airdrop)\b", re.I)

# Keyword APIs like Grants.gov match "art" inside unrelated science grants,
# so anything from a broad source must also look genuinely creative. We grade
# in two tiers: a STRONG term confirms arts on its own; a BROAD term only
# counts when no science / defense / health REJECT term is also present. That
# keeps "Creative Industries Film Lab" while dropping "High-performance
# Recycled Nuclear Isotopes" (which matched only the broad word "performance").
ARTS_STRONG = re.compile(
    r"\b(artist|artists|artistic|arts council|arts education|fine arts?|"
    r"visual arts?|media arts|public art|performing arts|performance art|"
    r"gallery|galleries|museum|museums|exhibition|exhibitions|"
    r"curator|curatorial|sculpture|sculptor|mural|printmaking|ceramics|"
    r"painting|painter|folklife|folk art|choreograph|playwright|"
    r"poet|poetry|filmmaker|film festival|literary|creative writing|"
    r"theatre|theater|humanities|cultural heritage|cultural preservation|"
    r"cultural exchange|arts and)\b", re.I)

ARTS_BROAD = re.compile(
    r"\b(art|arts|craft|music|musician|dance|dancer|film|cinema|"
    r"literature|writer|writing|design|designer|photograph|creative|"
    r"cultural|heritage)\b", re.I)

REJECT_NONARTS = re.compile(
    r"\b(nuclear|reactor|isotope|paleontolog\w*|semiconductor|missile|"
    r"spacecraft|satellite|genom\w*|molecular|clinical|vaccine|disease|"
    r"agricultur\w*|petroleum|wildfire|cybersecurity|quantum|"
    r"pharmaceutical|combustion|aerospace)\b", re.I)


def is_arts(text):
    """True when text looks genuinely arts-related. Strong terms pass on their
    own; broad terms pass only if no science/defense/health term is present."""
    if REJECT_NONARTS.search(text or ""):
        return bool(ARTS_STRONG.search(text or ""))
    return bool(ARTS_STRONG.search(text or "") or ARTS_BROAD.search(text or ""))

DEADLINE_PATTERNS = [
    r"deadline[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4})",
    r"(?:due|closes|apply by)[:\s]+([A-Z][a-z]+ \d{1,2},? \d{4})",
    r"deadline[:\s]+(\d{4}-\d{2}-\d{2})",
]


def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tag_item(title, summary, default_type):
    blob = f"{title} {summary}".lower()
    tags = {t for t, pat in TAG_RULES if re.search(pat, blob)}
    if default_type:
        tags.add(default_type)
    return sorted(tags) or ["announcement"]


def find_deadline(text):
    for pat in DEADLINE_PATTERNS:
        m = re.search(pat, text or "", re.I)
        if not m:
            continue
        raw = m.group(1).replace(",", "")
        for fmt in ("%B %d %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def norm_key(title, link):
    base = re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:80]
    host = urlparse(link or "").netloc.lower()
    return hashlib.sha1(f"{base}|{host}".encode()).hexdigest()


def to_iso(entry):
    for field in ("published_parsed", "updated_parsed"):
        st = entry.get(field)
        if st:
            return datetime.fromtimestamp(time.mktime(st), tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- sources

def pull_rss(source):
    url, name = source["url"], source.get("name", source["url"])
    default_type = source.get("type")
    items = []
    try:
        raw = requests.get(url, headers=UA, timeout=25)
        raw.raise_for_status()
        parsed = feedparser.parse(raw.content)
    except Exception as exc:
        print(f"  skip {name}: {exc}", file=sys.stderr)
        return items

    for entry in parsed.entries:
        title = clean(entry.get("title"))
        link = entry.get("link") or ""
        if not title or not link:
            continue
        summary = clean(entry.get("summary") or entry.get("description"))[:600]
        blob = f"{title} {summary}"
        if REJECT.search(blob):
            continue
        if source.get("require_arts") and not is_arts(blob):
            continue
        must = source.get("require")
        if must and not re.search("|".join(must), blob, re.I):
            continue
        items.append({
            "id": norm_key(title, link),
            "title": title,
            "link": link,
            "summary": summary,
            "source": name,
            "published": to_iso(entry),
            "deadline": find_deadline(f"{title} {summary}"),
            "tags": tag_item(title, summary, default_type),
            "region": region_of(f"{title} {summary}"),
        })
    print(f"  {name}: {len(items)}")
    return items


def pull_grants_gov(cfg):
    """Grants.gov Search2 endpoint. Public, no key."""
    endpoint = "https://api.grants.gov/v1/api/search2"
    items = []
    for kw in cfg.get("keywords", []):
        body = {"keyword": kw, "oppStatuses": "forecasted|posted",
                "rows": cfg.get("rows", 25), "startRecordNum": 0}
        try:
            r = requests.post(endpoint, json=body, headers=UA, timeout=30)
            r.raise_for_status()
            hits = r.json().get("data", {}).get("oppHits", [])
        except Exception as exc:
            print(f"  skip grants.gov '{kw}': {exc}", file=sys.stderr)
            continue
        for h in hits:
            title = clean(h.get("title"))
            num = h.get("number", "")
            link = f"https://www.grants.gov/search-results-detail/{h.get('id')}"
            agency = h.get("agency", "")
            summary = f"{agency} | opportunity {num}".strip(" |")
            if not is_arts(f"{title} {agency}"):
                continue
            close = h.get("closeDate") or None
            if close:
                try:
                    d = datetime.strptime(close, "%m/%d/%Y").date()
                    # Grants.gov uses far-future dates (e.g. 2099) as a "rolling,
                    # no fixed deadline" sentinel. Treat those as no deadline
                    # rather than showing an absurd countdown.
                    close = None if d.year >= 2090 else d.isoformat()
                except ValueError:
                    close = None
            items.append({
                "id": norm_key(title, link),
                "title": title,
                "link": link,
                "summary": summary,
                "source": "Grants.gov",
                "published": datetime.now(timezone.utc).isoformat(),
                "deadline": close,
                "tags": tag_item(title, summary, "grant"),
                "region": region_of(f"{title} {agency}"),
            })
        time.sleep(0.5)
    print(f"  Grants.gov: {len(items)}")
    return items


def pull_usajobs(cfg):
    """USAJOBS Search API. Free key from developer.usajobs.gov; set the
    USAJOBS_API_KEY env var (a GitHub secret in CI). Covers both fine-arts
    roles and skilled trades, with structured locations that feed the region
    filter. Each keyword is intentional, so results are trusted as-is rather
    than run through the arts-relevance filter."""
    key = os.environ.get("USAJOBS_API_KEY")
    if not key:
        print("  USAJOBS: skipped (set USAJOBS_API_KEY to enable)")
        return []
    endpoint = "https://data.usajobs.gov/api/search"
    headers = {
        "User-Agent": cfg.get("contact", "you@example.com"),
        "Authorization-Key": key,
    }
    items, seen = [], set()
    for kw in cfg.get("keywords", []):
        params = {"Keyword": kw, "ResultsPerPage": cfg.get("rows", 25)}
        try:
            r = requests.get(endpoint, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            hits = r.json().get("SearchResult", {}).get("SearchResultItems", [])
        except Exception as exc:
            print(f"  skip usajobs '{kw}': {exc}", file=sys.stderr)
            continue
        for h in hits:
            d = h.get("MatchedObjectDescriptor", {})
            title = clean(d.get("PositionTitle"))
            link = d.get("PositionURI") or ""
            if not title or not link or link in seen:
                continue
            seen.add(link)
            org = clean(d.get("OrganizationName"))
            loc = clean(d.get("PositionLocationDisplay"))
            summary = f"{org} | {loc}".strip(" |")
            close = d.get("ApplicationCloseDate")
            if close:
                close = close[:10]  # ISO date already
            pub = d.get("PublicationStartDate")
            published = (pub[:10] + "T00:00:00+00:00") if pub else \
                datetime.now(timezone.utc).isoformat()
            items.append({
                "id": norm_key(title, link),
                "title": title,
                "link": link,
                "summary": summary,
                "source": "USAJOBS",
                "published": published,
                "deadline": close,
                "tags": tag_item(title, summary, "job"),
                "region": region_of(loc),
            })
        time.sleep(0.4)
    print(f"  USAJOBS: {len(items)}")
    return items


def pull_scraped(site):
    items = []
    for row in scrape(site):
        blob = f"{row['title']} {row['summary']}"
        if REJECT.search(blob):
            continue
        items.append({
            "id": norm_key(row["title"], row["link"]),
            "title": row["title"],
            "link": row["link"],
            "summary": row["summary"],
            "source": row["source"],
            "published": datetime.now(timezone.utc).isoformat(),
            "deadline": find_deadline(row.get("deadline_text") or blob),
            "tags": tag_item(row["title"], row["summary"], row.get("type")),
            "region": region_of(blob),
        })
    return items


def pull_public_art(datasets):
    """Municipal public-art archives via Socrata. Records of existing works,
    so they carry kind='archive', no deadline, and an old publish date derived
    from the install year (keeps them below fresh opportunities in the feed)."""
    items = []
    for cfg in datasets:
        page = f"https://{cfg['domain']}/d/{cfg['id']}"
        city = cfg.get("city", "")
        for row in pull_dataset(cfg):
            where = ", ".join(p for p in (row["location"], city) if p)
            detail = row["description"] or row["medium"]
            summary = " · ".join(p for p in (
                f"by {row['artist']}" if row["artist"] else "",
                detail[:180] if detail else "",
                where,
                row["year"],
            ) if p)
            year = re.search(r"(19|20)\d{2}", row["year"])
            published = f"{year.group(0)}-01-01T00:00:00+00:00" if year \
                else "2000-01-01T00:00:00+00:00"
            link = row["link"] or page
            items.append({
                "id": norm_key(row["title"], page),
                "title": row["title"],
                "link": link,
                "summary": summary,
                "source": cfg["name"],
                "published": published,
                "deadline": None,
                "tags": ["public art"],
                # City is authoritative; only fall back to the free-text
                # location, where a street name like "Washington St" could
                # otherwise be misread as a state.
                "region": region_of(city) or region_of(where),
                "kind": "archive",
                "image": row["image"] or None,
            })
    return items


# ---------------------------------------------------------------- output

def write_json(items, path):
    payload = {
        "title": SITE_TITLE,
        "updated": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def write_rss(items, path):
    now = format_datetime(datetime.now(timezone.utc))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>{escape(SITE_TITLE)}</title>",
        f"<link>{escape(SITE_LINK)}</link>",
        "<description>Jobs, grants, RFPs, residencies and calls for artists</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]
    for it in items[:100]:
        desc = it["summary"]
        if it["deadline"]:
            desc = f"Deadline {it['deadline']}. {desc}"
        parts += [
            "<item>",
            f"<title>{escape(it['title'])}</title>",
            f"<link>{escape(it['link'])}</link>",
            f"<guid isPermaLink=\"false\">{it['id']}</guid>",
            f"<description>{escape(desc)}</description>",
            f"<category>{escape(', '.join(it['tags']))}</category>",
            "</item>",
        ]
    parts.append("</channel></rss>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


def main():
    with open(os.path.join(HERE, "sources.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    print("Fetching sources")
    items = []
    for src in cfg.get("sources", []):
        items += pull_rss(src)
    for site in cfg.get("scrape", []):
        items += pull_scraped(site)
    if cfg.get("grants_gov", {}).get("enabled"):
        items += pull_grants_gov(cfg["grants_gov"])
    if cfg.get("usajobs", {}).get("enabled"):
        items += pull_usajobs(cfg["usajobs"])
    if cfg.get("public_art"):
        items += pull_public_art(cfg["public_art"])

    # opportunity is the default; archive items set their own kind
    for it in items:
        it.setdefault("kind", "opportunity")

    # dedupe, newest wins
    seen, unique = set(), []
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        unique.append(it)

    # drop expired and very old. Archive records never expire and carry old
    # install dates, so they are exempt from the staleness cutoff.
    today = datetime.now(timezone.utc).date().isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_AFTER_DAYS)).isoformat()
    live = [it for it in unique
            if (not it["deadline"] or it["deadline"] >= today)
            and (it["kind"] == "archive" or it["published"] >= cutoff)]

    live = live[:MAX_ITEMS]
    os.makedirs(OUT_DIR, exist_ok=True)
    write_json(live, os.path.join(OUT_DIR, "feed.json"))
    # RSS stays an open-calls subscription: opportunities only.
    write_rss([it for it in live if it["kind"] == "opportunity"],
              os.path.join(OUT_DIR, "feed.xml"))
    opp = sum(1 for it in live if it["kind"] == "opportunity")
    print(f"\n{len(live)} items written to docs/ ({opp} opportunities, "
          f"{len(live) - opp} public-art records)")


if __name__ == "__main__":
    main()
