"""The permanent guide: language labelling, the indoor/outdoor tri-state, and
the shape places.json is turned into.
"""
import json

import pytest

import config
import places


# ── _province_language ──────────────────────────────────────────────────────
@pytest.mark.parametrize("province", ["Antwerpen", "Limburg", "Oost-Vlaanderen",
                                      "Vlaams-Brabant", "West-Vlaanderen"])
def test_flemish_provinces_are_dutch(province):
    assert places._province_language(province) == "nl"


@pytest.mark.parametrize("province", ["Waals-Brabant", "Henegouwen", "Luik",
                                      "Luxemburg", "Namen"])
def test_walloon_provinces_are_french(province):
    """Regression: every Walloon museum used to be published as Dutch."""
    assert places._province_language(province) == "fr"


def test_brussels_is_multilingual():
    assert places._province_language("Brussel") == "multi"


def test_unknown_province_falls_back_to_multi():
    assert places._province_language(None) == "multi"
    assert places._province_language("Atlantis") == "multi"


def test_every_province_in_places_json_is_mapped():
    data = json.loads((places.PLACES_JSON).read_text())
    for p in data["places"]:
        assert places._province_language(p.get("province")) in ("nl", "fr", "multi")


# ── _indoor_outdoor ─────────────────────────────────────────────────────────
def test_explicit_value_wins(make_place):
    assert places._indoor_outdoor(make_place(indoor_outdoor="both", kind="museum")) == "both"


def test_invalid_explicit_value_is_ignored(make_place):
    p = make_place(indoor_outdoor="damp", kind="museum", indoor=True)
    assert places._indoor_outdoor(p) == "indoor"


@pytest.mark.parametrize("kind", ["zoo", "castle", "playground_restaurant",
                                  "provincial_domain", "attraction_park", "farm"])
def test_kinds_that_are_always_both(kind, make_place):
    """A zoo has pavilions, a castle has grounds — the old boolean forced these
    to pick a side, which made them wrong for half of any rainy-day search."""
    assert places._indoor_outdoor(make_place(kind=kind, indoor=False)) == "both"


def test_falls_back_to_the_legacy_boolean(make_place):
    assert places._indoor_outdoor(make_place(kind="museum", indoor=True)) == "indoor"
    assert places._indoor_outdoor(make_place(kind="speelbos", indoor=False)) == "outdoor"


def test_missing_boolean_is_treated_as_outdoor(make_place):
    p = make_place(kind="speelbos")
    p.pop("indoor")
    assert places._indoor_outdoor(p) == "outdoor"


# ── load_places_as_activities ───────────────────────────────────────────────
@pytest.fixture
def loaded(tmp_path, monkeypatch, make_place):
    def _load(*place_overrides):
        payload = {"places": [make_place(**o) for o in (place_overrides or [{}])]}
        path = tmp_path / "places.json"
        path.write_text(json.dumps(payload))
        monkeypatch.setattr(places, "PLACES_JSON", path)
        return places.load_places_as_activities("run-1")
    return _load


def test_places_become_permanent_activities(loaded):
    act = loaded({})[0]
    assert act["date_kind"] == "permanent"
    assert act["weekend_bucket"] == ["later"]
    assert act["is_special_event"] is False
    assert act["date_start"] is None


def test_places_carry_no_school_holiday(loaded):
    act = loaded({})[0]
    assert act["in_school_holiday"] is False
    assert act["school_holiday_nl"] is None
    assert act["school_holiday_fr"] is None


def test_distance_is_computed_from_coordinates(loaded):
    act = loaded({"lat": 50.879, "lng": 4.701})[0]
    assert act["distance_km"] == pytest.approx(0, abs=1)


def test_missing_coordinates_give_null_distance(loaded):
    act = loaded({"lat": None, "lng": None})[0]
    assert act["distance_km"] is None


def test_language_free_kinds_are_flagged(loaded):
    playground = loaded({"kind": "speelbos", "id": "p1"})[0]
    museum = loaded({"kind": "museum", "id": "p2"})[0]
    assert playground["language_free"] is True
    assert museum["language_free"] is False, "a museum is carried by words"


def test_link_ok_defaults_to_true(loaded):
    assert loaded({})[0]["link_ok"] is True


def test_link_ok_is_carried_through(loaded):
    assert loaded({"link_ok": False})[0]["link_ok"] is False


def test_image_credit_is_carried_through(loaded):
    act = loaded({"image_url": "https://x/a.jpg", "image_credit": "Wikipedia (nl)",
                  "image_credit_url": "https://nl.wikipedia.org/wiki/X"})[0]
    assert act["image_credit"] == "Wikipedia (nl)"
    assert act["image_credit_url"].startswith("https://nl.wikipedia.org/")


def test_unknown_feature_tags_are_dropped(loaded):
    act = loaded({"tags": ["animals", "not_a_real_tag"]})[0]
    assert act["feature_tags"] == ["animals"]
    assert set(act["feature_tags"]) <= set(config.FEATURE_TAG_VOCAB)


def test_url_falls_back_to_a_placeholder_when_there_is_no_website(loaded):
    act = loaded({"website": None})[0]
    assert act["url"].startswith("place://")


def test_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(places, "PLACES_JSON", tmp_path / "nope.json")
    assert places.load_places_as_activities("run-1") == []


def test_invalid_json_returns_empty(tmp_path, monkeypatch):
    path = tmp_path / "places.json"
    path.write_text("{not json")
    monkeypatch.setattr(places, "PLACES_JSON", path)
    assert places.load_places_as_activities("run-1") == []


def test_real_places_json_loads(monkeypatch):
    """Smoke test against the checked-in file, so a hand edit that breaks the
    schema fails here rather than mid-pipeline."""
    acts = places.load_places_as_activities("run-1")
    assert len(acts) > 100
    assert all(a["date_kind"] == "permanent" for a in acts)
    assert all(a["indoor_outdoor"] in ("indoor", "outdoor", "both") for a in acts)
    assert all(a["primary_language"] in ("nl", "fr", "en", "multi") for a in acts)
