"""Best-effort region tagging for opportunity items.

Coarse and reliable beats granular and wrong: we bucket into a handful of
regions the viewer can filter on, and return None when a location can't be
read confidently (those items still show under "All regions"). We deliberately
do NOT match bare two-letter state abbreviations in free text ("OR", "IN",
"ME" are common words); abbreviations only count in a "City, ST" context.
"""

import re

# US Census regions.
_STATE_REGION = {
    # Northeast
    "connecticut": "Northeast", "maine": "Northeast", "massachusetts": "Northeast",
    "new hampshire": "Northeast", "rhode island": "Northeast", "vermont": "Northeast",
    "new jersey": "Northeast", "new york": "Northeast", "pennsylvania": "Northeast",
    # Midwest
    "illinois": "Midwest", "indiana": "Midwest", "michigan": "Midwest",
    "ohio": "Midwest", "wisconsin": "Midwest", "iowa": "Midwest", "kansas": "Midwest",
    "minnesota": "Midwest", "missouri": "Midwest", "nebraska": "Midwest",
    "north dakota": "Midwest", "south dakota": "Midwest",
    # South
    "delaware": "South", "florida": "South", "georgia": "South", "maryland": "South",
    "north carolina": "South", "south carolina": "South", "virginia": "South",
    "west virginia": "South", "district of columbia": "South", "washington dc": "South",
    "alabama": "South", "kentucky": "South", "mississippi": "South", "tennessee": "South",
    "arkansas": "South", "louisiana": "South", "oklahoma": "South", "texas": "South",
    # West
    "arizona": "West", "colorado": "West", "idaho": "West", "montana": "West",
    "nevada": "West", "new mexico": "West", "utah": "West", "wyoming": "West",
    "alaska": "West", "california": "West", "hawaii": "West", "oregon": "West",
    "washington": "West",
}

# Two-letter abbreviation -> region, only used in a "City, ST" match.
_ABBR_REGION = {
    "ct": "Northeast", "me": "Northeast", "ma": "Northeast", "nh": "Northeast",
    "ri": "Northeast", "vt": "Northeast", "nj": "Northeast", "ny": "Northeast",
    "pa": "Northeast",
    "il": "Midwest", "in": "Midwest", "mi": "Midwest", "oh": "Midwest",
    "wi": "Midwest", "ia": "Midwest", "ks": "Midwest", "mn": "Midwest",
    "mo": "Midwest", "ne": "Midwest", "nd": "Midwest", "sd": "Midwest",
    "de": "South", "fl": "South", "ga": "South", "md": "South", "nc": "South",
    "sc": "South", "va": "South", "wv": "South", "dc": "South", "al": "South",
    "ky": "South", "ms": "South", "tn": "South", "ar": "South", "la": "South",
    "ok": "South", "tx": "South",
    "az": "West", "co": "West", "id": "West", "mt": "West", "nv": "West",
    "nm": "West", "ut": "West", "wy": "West", "ak": "West", "ca": "West",
    "hi": "West", "or": "West", "wa": "West",
}

# Major arts cities -> region, to catch locations given without a state.
_CITY_REGION = {
    "new york": "Northeast", "brooklyn": "Northeast", "manhattan": "Northeast",
    "boston": "Northeast", "philadelphia": "Northeast", "pittsburgh": "Northeast",
    "providence": "Northeast", "buffalo": "Northeast", "hudson": "Northeast",
    "beacon": "Northeast", "newark": "Northeast",
    "chicago": "Midwest", "detroit": "Midwest", "minneapolis": "Midwest",
    "cleveland": "Midwest", "columbus": "Midwest", "cincinnati": "Midwest",
    "kansas city": "Midwest", "milwaukee": "Midwest", "st. louis": "Midwest",
    "st louis": "Midwest", "indianapolis": "Midwest",
    "washington": "South", "atlanta": "South", "miami": "South", "houston": "South",
    "dallas": "South", "austin": "South", "new orleans": "South", "nashville": "South",
    "baltimore": "South", "richmond": "South", "charlotte": "South", "raleigh": "South",
    "los angeles": "West", "san francisco": "West", "oakland": "West",
    "seattle": "West", "portland": "West", "denver": "West", "santa fe": "West",
    "san diego": "West", "phoenix": "West", "las vegas": "West", "sacramento": "West",
    "marfa": "West", "san jose": "West", "honolulu": "West",
}

# A country name (and no US signal) means International. "Georgia" is omitted
# here on purpose — it resolves to the US state above.
_COUNTRIES = [
    "united kingdom", "england", "scotland", "wales", "ireland", "france",
    "germany", "italy", "spain", "portugal", "netherlands", "belgium",
    "switzerland", "austria", "sweden", "norway", "denmark", "finland",
    "iceland", "poland", "greece", "czech", "hungary", "romania", "croatia",
    "estonia", "latvia", "lithuania", "ukraine", "canada", "mexico", "brazil",
    "argentina", "chile", "colombia", "peru", "cuba", "japan", "china",
    "korea", "india", "australia", "new zealand", "nigeria", "kenya", "ghana",
    "south africa", "egypt", "morocco", "indonesia", "vietnam", "thailand",
    "philippines", "turkey", "israel", "singapore", "taiwan",
]

_REMOTE = re.compile(r"\b(remote|online|virtual|work from home|telework|anywhere)\b", re.I)
_ABBR_CTX = re.compile(r",\s*([A-Za-z]{2})\b")

REGIONS = ["Remote", "Northeast", "South", "Midwest", "West", "International"]


def region_of(text):
    """Return one of REGIONS, or None when no location reads confidently."""
    if not text:
        return None
    low = text.lower()

    if _REMOTE.search(low):
        return "Remote"

    # "City, ST" abbreviation context (reliable; ignores bare two-letter words).
    for m in _ABBR_CTX.finditer(text):
        r = _ABBR_REGION.get(m.group(1).lower())
        if r:
            return r

    for name, region in _STATE_REGION.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return region

    for city, region in _CITY_REGION.items():
        if re.search(rf"\b{re.escape(city)}\b", low):
            return region

    for country in _COUNTRIES:
        if re.search(rf"\b{re.escape(country)}\b", low):
            return "International"

    return None
