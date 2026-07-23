# Creative Opportunities Feed

Aggregates jobs, grants, RFPs, residencies and open calls for artists into
`docs/feed.json` and `docs/feed.xml`. Runs free on GitHub Actions, serves free
on GitHub Pages.

`feed.json` declares `contract_version: artelier_opportunities_feed_v1` so
Artelier can reject incompatible feed shapes instead of silently misreading
them. Additive item fields are allowed; existing field meanings remain stable
within v1.

### Additive fields for Artelier

After aggregation, `enrich.py` fills optional fields Artelier maps into richer
opportunity detail + matching:

| Field | Purpose |
|-------|---------|
| `description` | Longer “what” (eligibility / duties / deliverables) |
| `location_mode` | `onsite` \| `remote` \| `hybrid` \| `flexible` \| `not_specified` |
| `required_roles` | Guided-vocab roles when detectable (e.g. Artist, Fabricator) |
| `project_types` | Guided-vocab project types (e.g. Public Art, Sculpture) |
| `materials` | Guided-vocab materials when mentioned |
| `opportunity_type` | Artelier type enum when classifiable |

`summary` stays short for the Artfeed viewer. Thin API rows (USAJOBS /
Grants.gov) prefer structured API text first, then a capped detail-page fetch
(≤80 per run) so GitHub Actions stays bounded.

## Setup

1. Create a repo, drop these files in, push to `main`.
2. Settings > Pages > Source: Deploy from branch, branch `main`, folder `/docs`.
3. Settings > Actions > General > Workflow permissions: Read and write.
4. Actions tab > Build feed > Run workflow.

Your feed is then at:
- `https://USERNAME.github.io/REPO/` viewer
- `https://USERNAME.github.io/REPO/feed.json`
- `https://USERNAME.github.io/REPO/feed.xml`

## Local test

    pip install -r requirements.txt
    python aggregate.py
    python -m http.server -d docs 8000    # open http://localhost:8000

## Job postings (USAJOBS)

Fine-arts and skilled-trades job listings come from the official USAJOBS open
API — it's the one free source that covers both, with real "City, State"
locations that also drive the region filter. It needs a free API key:

1. Register at <https://developer.usajobs.gov> (email confirmation, ~2 min).
2. Local test: `export USAJOBS_API_KEY=...` before `python aggregate.py`.
3. In CI: repo Settings > Secrets and variables > Actions > New secret,
   name `USAJOBS_API_KEY`. The workflow already passes it through.

Without a key the source self-skips cleanly (the `job` filter just stays
empty). Tune the roles it searches for in the `usajobs.keywords` list in
`sources.yaml`.

## Public-art archive

Beyond forward-looking opportunities, the feed also carries a **Public Art**
stream: records of existing works and their creators (murals, percent-for-art,
temporary installations) pulled from municipal open-data (Socrata) portals —
Chicago, New York, and Cambridge to start. These are free, open-licensed, and
need no API key or scraping; `socrata.py` fetches them and each dataset maps
its columns in the `public_art` block of `sources.yaml`. They carry
`kind: "archive"`, never expire, and the viewer shows them under a separate
"Public Art" feed toggle so the deadline-driven opportunities stream stays
clean. San Francisco / Philadelphia / Tempe are left as config templates whose
Socrata IDs need confirming from their portals.

## Region filter

Every item is tagged best-effort with a region (Remote, Northeast, South,
Midwest, West, International) by reading locations out of its text; see
`geo.py`. Items with no readable location are left untagged and show only
under "everywhere". The viewer's Region dropdown lists only the regions
actually present in the current feed.

## Viewer

`docs/index.html` is a static viewer: a **Feed / Category / Region** dropdown
toolbar plus free-text search, newest-first. The **Public Art** feed toggle
switches between the two streams. An **Add opportunity** button opens a form
whose submissions are saved in the visitor's browser (`localStorage`) and
pinned to the top of their feed — a no-backend way to demo user contributions.

## Adding sources

Edit `sources.yaml`. Any RSS or Atom URL works. Set `type` to seed a tag;
keyword rules in `aggregate.py` add the rest.

## Notes

- The viewer sorts newest first and filters by tag, region, and free text.
- Reddit and Craigslist block many datacenter IPs. If a source 403s in Actions,
  either drop it or replace it with a Google Alerts RSS URL on the same topic.
- Grants.gov results pass a two-tier arts-relevance filter (`is_arts` in
  `aggregate.py`), since keyword search alone returns unrelated science grants.
- Items with a parsed deadline in the past are dropped automatically.
