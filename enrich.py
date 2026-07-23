"""Enrich opportunity items with longer description + structured Artelier fields.

Additive artelier_opportunities_feed_v1 fields:
  description, location_mode, required_roles, project_types, materials,
  opportunity_type

Keeps summary short for the viewer; fills description from API text or a
bounded detail-page fetch when the summary is thin.
"""

from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "creative-opps-aggregator/1.0 (+contact: you@example.com)"}

# Cap detail fetches so Actions stays bounded (2×/day).
MAX_DETAIL_FETCHES = 80
DETAIL_TIMEOUT = 18
MIN_SUMMARY_FOR_SKIP = 160
MAX_DESCRIPTION = 8000

LOCATION_MODES = ("onsite", "remote", "hybrid", "flexible", "not_specified")

TYPE_RULES = [
    ("job", re.compile(r"\b(job|hiring|position|employment|vacancy)\b", re.I)),
    ("fellowship", re.compile(r"\bfellowship\b", re.I)),
    ("residency", re.compile(r"\bresiden(?:cy|tial)\b", re.I)),
    ("grant_or_funding", re.compile(r"\b(grant|funding|fund|award|stipend)\b", re.I)),
    ("exhibition_opportunity", re.compile(r"\b(exhibition|exhibit|gallery show)\b", re.I)),
    (
        "call_for_artists",
        re.compile(
            r"\b(open call|call for artists?|rfq|rfp|request for qualifications|"
            r"request for proposals)\b",
            re.I,
        ),
    ),
    ("commission", re.compile(r"\b(commission|public art call|percent for art)\b", re.I)),
]

# Canonical label -> aliases (aligned with Artelier guided vocab).
ROLE_VOCAB = {
    "Artist": ["sculptor", "sculptural artist", "visual artist", "fine artist", "maker artist", "artist"],
    "Designer": ["design lead", "industrial designer", "spatial designer", "designer"],
    "Fabricator": [
        "metal fabricator", "metal fabrication", "fabrication", "shop fabricator",
        "metalwork", "metal worker", "fabricator",
    ],
    "Project Manager": ["pm", "producer", "production manager", "project manager"],
    "Engineer": ["structural engineer", "mechanical engineer", "engineer"],
    "Installer": ["installation lead", "installation tech", "installer"],
    "Technical Designer": ["tech designer", "design technologist", "technical designer"],
    "Digital Fabrication Specialist": ["digital fab", "cnc specialist", "cam specialist"],
    "Material Specialist": ["materials specialist", "materials lead"],
    "Studio Assistant": ["assistant", "studio tech", "studio assistant"],
    "Photographer / Documentarian": ["photographer", "documentarian"],
    "Coordinator": ["project coordinator", "logistics coordinator", "coordinator"],
    "Consultant": ["consulting", "advisor", "consultant"],
}

PROJECT_TYPE_VOCAB = {
    "Public Art": ["public-art", "publicart", "civic art", "public artwork", "public art", "mural"],
    "Sculpture": ["sculptural", "sculptural work", "sculpture"],
    "Installation": ["install", "site specific installation", "site-specific", "installation"],
    "Interactive Installation": ["interactive", "interactive art", "interactive installation"],
    "Fabrication Project": ["fab project", "fabrication project"],
    "Architectural Feature": ["architectural", "architecture feature"],
    "Exhibition": ["gallery show", "exhibit", "exhibition"],
    "Museum / Cultural Project": ["museum project", "cultural project"],
    "Experiential Environment": ["experiential", "immersive environment"],
    "Event / Temporary Activation": ["activation", "temporary activation", "event activation"],
    "Art Vehicle / Art Car": ["art car", "art vehicle", "mutant vehicle"],
    "Product / Object": ["product design", "designed object"],
    "Digital / Physical Hybrid": ["phygital", "digital hybrid"],
    "Research / Prototype": ["prototype", "r&d", "research"],
}

MATERIAL_VOCAB = {
    "Wood": ["wood", "timber"],
    "Metal": ["metalwork", "metals", "metal"],
    "Steel": ["mild steel", "carbon steel", "steel"],
    "Stainless Steel": ["stainless", "ss", "stainless steel"],
    "Aluminum": ["aluminium", "aluminum"],
    "Bronze": ["bronze"],
    "Brass": ["brass"],
    "Copper": ["copper"],
    "Glass": ["glass"],
    "Acrylic": ["acrylic"],
    "Plastic": ["plastic"],
    "Resin": ["resin"],
    "Ceramic": ["ceramic", "ceramics"],
    "Concrete": ["concrete"],
    "Stone": ["stone"],
    "Fabric": ["cloth", "fabric"],
    "Textile": ["textiles", "fiber", "textile"],
    "LED": ["leds", "led lighting", "led"],
    "Neon": ["neon"],
    "Reclaimed Material": ["reclaimed", "upcycled material"],
    "Found Material": ["found object", "found materials"],
}


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _build_index(vocab: dict[str, list[str]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for label, aliases in vocab.items():
        index[_norm_key(label)] = label
        for alias in aliases:
            key = _norm_key(alias)
            if key and key not in index:
                index[key] = label
    return index


ROLE_INDEX = _build_index(ROLE_VOCAB)
PROJECT_TYPE_INDEX = _build_index(PROJECT_TYPE_VOCAB)
MATERIAL_INDEX = _build_index(MATERIAL_VOCAB)


def _clean_text(text: str, max_len: int = MAX_DESCRIPTION) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def classify_opportunity_type(item: dict) -> str | None:
    tags = " ".join(item.get("tags") or [])
    blob = f"{tags} {item.get('title', '')} {item.get('summary', '')} {item.get('description', '')}"
    for type_name, pattern in TYPE_RULES:
        if pattern.search(blob):
            return type_name
    return None


def classify_location_mode(item: dict, blob: str) -> str:
    region = (item.get("region") or "").strip().lower()
    if region == "remote":
        return "remote"
    if re.search(r"\b(fully remote|remote work|work from home|telework)\b", blob, re.I):
        return "remote"
    if re.search(r"\bhybrid\b", blob, re.I):
        return "hybrid"
    if re.search(r"\b(onsite|on-site|in[- ]person|must be located)\b", blob, re.I):
        return "onsite"
    if re.search(r"\bflexible (location|schedule|work)\b", blob, re.I):
        return "flexible"
    return "not_specified"


def _match_vocab(blob: str, index: dict[str, str], max_items: int = 8) -> list[str]:
    """Match aliases against a space-stripped blob; longer aliases win first."""
    compact = _norm_key(blob)
    found: list[str] = []
    seen: set[str] = set()
    for key in sorted(index.keys(), key=len, reverse=True):
        # Skip tiny keys ("ss", "pm") — too many false positives in compact text.
        if len(key) < 4:
            continue
        if key not in compact:
            continue
        label = index[key]
        if label in seen:
            continue
        seen.add(label)
        found.append(label)
        if len(found) >= max_items:
            break
    return found


def match_roles(blob: str) -> list[str]:
    return _match_vocab(blob, ROLE_INDEX)


def match_project_types(blob: str) -> list[str]:
    return _match_vocab(blob, PROJECT_TYPE_INDEX)


def match_materials(blob: str) -> list[str]:
    return _match_vocab(blob, MATERIAL_INDEX)


def fetch_detail_text(url: str) -> str:
    """Fetch a detail page and return cleaned visible text (bounded)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ""
        resp = requests.get(url, headers=UA, timeout=DETAIL_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        main = soup.select_one("main, article, #content, .content, .job-description") or soup.body
        if not main:
            return ""
        return _clean_text(main.get_text(" ", strip=True), MAX_DESCRIPTION)
    except Exception:
        return ""


def needs_detail_fetch(item: dict) -> bool:
    if item.get("kind") != "opportunity":
        return False
    if item.get("description") and len(item["description"]) >= MIN_SUMMARY_FOR_SKIP:
        return False
    summary = item.get("summary") or ""
    # Thin pipe summaries from APIs, or short RSS blurbs.
    if " | " in summary and len(summary) < 220:
        return True
    return len(summary) < MIN_SUMMARY_FOR_SKIP


def enrich_item(item: dict, *, allow_fetch: bool, fetch_budget: list[int]) -> dict:
    """Mutate/return item with additive enrichment fields."""
    if item.get("kind") != "opportunity":
        return item

    description = _clean_text(item.get("description") or "")
    if allow_fetch and needs_detail_fetch(item) and fetch_budget[0] > 0:
        fetched = fetch_detail_text(item.get("link") or "")
        fetch_budget[0] -= 1
        time.sleep(0.35)
        if len(fetched) > len(description):
            description = fetched

    if not description:
        description = _clean_text(item.get("summary") or "")

    if description:
        item["description"] = description[:MAX_DESCRIPTION]

    blob = " ".join(
        p for p in (
            item.get("title") or "",
            item.get("summary") or "",
            item.get("description") or "",
            " ".join(item.get("tags") or []),
        ) if p
    )

    opp_type = classify_opportunity_type(item)
    if opp_type:
        item["opportunity_type"] = opp_type

    item["location_mode"] = classify_location_mode(item, blob)

    roles = match_roles(blob)
    if roles:
        item["required_roles"] = roles
    project_types = match_project_types(blob)
    if project_types:
        item["project_types"] = project_types
    materials = match_materials(blob)
    if materials:
        item["materials"] = materials

    return item


def enrich_items(items: list[dict], max_fetches: int = MAX_DETAIL_FETCHES) -> list[dict]:
    budget = [max_fetches]
    # Prefer enriching thin API rows first.
    ordered = sorted(
        enumerate(items),
        key=lambda pair: (0 if needs_detail_fetch(pair[1]) else 1, pair[0]),
    )
    enriched_by_index: dict[int, dict] = {}
    for idx, item in ordered:
        enriched_by_index[idx] = enrich_item(item, allow_fetch=True, fetch_budget=budget)
    print(f"  enrich: detail fetches remaining budget {budget[0]}/{max_fetches}")
    return [enriched_by_index[i] for i in range(len(items))]
