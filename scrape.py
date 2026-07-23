"""Generic HTML scraper for opportunity boards that publish no feed.

Everything is driven by CSS selectors in sources.yaml, so adapting to a site
redesign means editing config, not code. Be a good citizen: the aggregator
runs twice a day and this module sleeps between requests.
"""

import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "creative-opps-aggregator/1.0 (+contact: you@example.com)"}


def _text(node, selector):
    if not selector:
        return ""
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else ""


def scrape(site):
    """site keys:
        name, url, type
        item        selector for each result row
        title       selector for the title text, relative to item
        link        selector for the <a>, defaults to the first <a> in item
        summary     optional selector for description text
        deadline    optional selector for a deadline string
        pages       optional {param: "page", start: 0, count: 3}
    """
    name = site.get("name", site["url"])
    results = []
    urls = _page_urls(site)

    for url in urls:
        try:
            resp = requests.get(url, headers=UA, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  skip {name} <{url}>: {exc}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select(site["item"])
        if not rows:
            print(f"  {name}: selector '{site['item']}' matched nothing, "
                  "the site layout probably changed")

        for row in rows:
            title = _text(row, site.get("title")) or row.get_text(" ", strip=True)[:120]
            anchor = row.select_one(site.get("link") or "a[href]")
            href = anchor.get("href") if anchor else None
            if not title or not href:
                continue
            results.append({
                "title": re.sub(r"\s+", " ", title).strip(),
                "link": urljoin(url, href),
                "summary": _text(row, site.get("summary"))[:600],
                "deadline_text": _text(row, site.get("deadline")),
                "source": name,
                "type": site.get("type"),
            })
        time.sleep(site.get("delay", 2))

    print(f"  {name}: {len(results)}")
    return results


def _page_urls(site):
    pages = site.get("pages")
    if not pages:
        return [site["url"]]
    param = pages.get("param", "page")
    start = pages.get("start", 0)
    count = pages.get("count", 3)
    joiner = "&" if "?" in site["url"] else "?"
    return [f"{site['url']}{joiner}{param}={n}" for n in range(start, start + count)]
