"""Generic Socrata open-data puller for municipal public-art datasets.

Cities publish public-art registries (murals, percent-for-art, temporary
installations) as Socrata datasets with a uniform JSON API:

    https://<domain>/resource/<dataset-id>.json?$limit=N

Field names differ per city, so each dataset maps its columns to our common
keys in sources.yaml (like scrape.py does for selectors). These are archives
of existing works, not opportunities — aggregate.py tags them accordingly.
"""

import time
from urllib.parse import urljoin

import requests

UA = {"User-Agent": "creative-opps-aggregator/1.0 (+contact: you@example.com)"}


def _val(row, col):
    if not col:
        return ""
    v = row.get(col)
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict) and isinstance(v.get("url"), str):
        return v["url"].strip()  # Socrata URL type
    return ""  # skip other structured Socrata types (e.g. point geometry)


def pull_dataset(cfg):
    """Return a list of normalized rows for one dataset config.

    cfg keys: name, domain, id, city, limit, and a `fields` map whose keys are
    any of: title, artist, medium, description, location, year, image, link.
    """
    domain, ds = cfg["domain"], cfg["id"]
    fields = cfg.get("fields", {})
    name = cfg.get("name", ds)
    url = f"https://{domain}/resource/{ds}.json"
    try:
        r = requests.get(url, params={"$limit": cfg.get("limit", 30)},
                         headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"  skip {name}: {exc}")
        return []

    rows = []
    for row in data:
        title = _val(row, fields.get("title"))
        if not title:
            continue
        rows.append({
            "title": title,
            "artist": _val(row, fields.get("artist")),
            "medium": _val(row, fields.get("medium")),
            "description": _val(row, fields.get("description")),
            "location": _val(row, fields.get("location")),
            "year": _val(row, fields.get("year")),
            "image": _val(row, fields.get("image")),
            "link": _val(row, fields.get("link")),
        })
    time.sleep(cfg.get("delay", 0.4))
    print(f"  {name}: {len(rows)}")
    return rows
