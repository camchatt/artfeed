"""Unit tests for enrich.py (no network)."""

from enrich import (
    classify_location_mode,
    classify_opportunity_type,
    enrich_item,
    match_materials,
    match_project_types,
    match_roles,
)


def test_classify_types_and_location():
    assert classify_opportunity_type({"title": "Artist fellowship", "summary": "", "tags": []}) == "fellowship"
    assert classify_opportunity_type({"title": "Open call for artists", "summary": "", "tags": []}) == "call_for_artists"
    assert classify_location_mode({"region": "Remote"}, "") == "remote"
    assert classify_location_mode({}, "This is a hybrid role with some remote days") == "hybrid"


def test_vocab_matching():
    blob = "Seeking a sculptor for public art fabrication in steel and aluminum"
    assert "Artist" in match_roles(blob)
    assert "Fabricator" in match_roles(blob)
    assert "Public Art" in match_project_types(blob)
    assert "Steel" in match_materials(blob)
    assert "Aluminum" in match_materials(blob)


def test_enrich_without_fetch_uses_summary():
    item = {
        "kind": "opportunity",
        "title": "Museum fabricator position",
        "summary": "Example Museum | New York, NY",
        "description": (
            "Full-time fabricator building public art and sculpture exhibitions "
            "in metal and wood. On-site shop work in New York."
        ),
        "tags": ["job"],
        "region": "Northeast",
        "link": "https://example.org/job",
    }
    out = enrich_item(item, allow_fetch=False, fetch_budget=[0])
    assert out["opportunity_type"] == "job"
    assert out["location_mode"] == "onsite"
    assert "Fabricator" in out.get("required_roles", [])
    assert len(out["description"]) > 40


if __name__ == "__main__":
    test_classify_types_and_location()
    test_vocab_matching()
    test_enrich_without_fetch_uses_summary()
    print("ok")
