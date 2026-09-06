"""Payload construction: field slimming, filter counts, and the metadata the
frontend reads.
"""
import pytest

import config
import publish


def build(activities, **kw):
    return publish.build_payload(
        activities,
        kw.get("run_id", "2026-09-06_1200"),
        kw.get("fetched", ["uitinleuven"]),
        kw.get("failed", []),
        kw.get("degraded", False),
    )


@pytest.fixture
def event(make_activity):
    def _make(**over):
        act = make_activity(
            category="museum_exhibition", feature_tags=["animals"],
            weekend_bucket=["this_weekend"], kind=None, indoor_outdoor="indoor",
            price_type="free", primary_language="nl",
        )
        act.update(over)
        return act
    return _make


@pytest.fixture
def place(make_activity):
    def _make(**over):
        act = make_activity(
            date_kind="permanent", category="zoo_animal_park", feature_tags=["animals"],
            weekend_bucket=["later"], kind="zoo", indoor_outdoor="both",
        )
        act.update(over)
        return act
    return _make


# ── slimming ────────────────────────────────────────────────────────────────
def test_only_published_fields_survive(event):
    payload = build([event(secret_internal_field="leaked")])
    assert "secret_internal_field" not in payload["activities"][0]


def test_every_published_field_is_present_even_when_unset(event):
    act = payload_first = build([event()])["activities"][0]
    for field in publish._PUBLISHED_FIELDS:
        assert field in act, f"{field} missing from the published record"
    assert payload_first is act


def test_new_fields_reach_the_payload(event):
    act = build([event(indoor_outdoor="indoor", link_ok=True,
                       school_holiday_nl="herfstvakantie",
                       school_holiday_fr=None)])["activities"][0]
    assert act["indoor_outdoor"] == "indoor"
    assert act["link_ok"] is True
    assert act["school_holiday_nl"] == "herfstvakantie"
    assert act["school_holiday_fr"] is None


# ── counts ──────────────────────────────────────────────────────────────────
def test_categories_are_counted_from_events_only(event, place):
    payload = build([event(category="museum_exhibition"),
                     place(category="zoo_animal_park")])
    keys = {c["key"] for c in payload["categories"]}
    assert "museum_exhibition" in keys
    assert "zoo_animal_park" not in keys, "place categories belong to place_kinds"


def test_place_kinds_are_counted_from_places_only(event, place):
    payload = build([event(), place(kind="zoo")])
    keys = {k["key"] for k in payload["place_kinds"]}
    assert keys == {"zoo"}


def test_feature_tags_are_counted_across_everything(event, place):
    payload = build([event(feature_tags=["animals"]), place(feature_tags=["animals", "water_play"])])
    counts = {t["key"]: t["count"] for t in payload["feature_tags"]}
    assert counts["animals"] == 2
    assert counts["water_play"] == 1


def test_counts_are_ordered_by_frequency(event):
    payload = build([event(id="1", category="museum_exhibition"),
                     event(id="2", category="museum_exhibition"),
                     event(id="3", category="film")])
    assert payload["categories"][0]["key"] == "museum_exhibition"
    assert payload["categories"][0]["count"] == 2


# ── metadata ────────────────────────────────────────────────────────────────
def test_both_school_calendars_are_shipped(event):
    payload = build([event()])
    assert payload["school_holidays_nl"] == config.SCHOOL_HOLIDAYS_NL
    assert payload["school_holidays_fr"] == config.SCHOOL_HOLIDAYS_FR


def test_legacy_school_holidays_key_is_kept(event):
    """Older cached clients still read `school_holidays`."""
    payload = build([event()])
    assert payload["school_holidays"] == config.SCHOOL_HOLIDAYS_NL


def test_run_metadata_is_recorded(event):
    payload = build([event()], run_id="run-42", fetched=["a"], failed=["b"], degraded=True)
    assert payload["run_id"] == "run-42"
    assert payload["sources_fetched"] == ["a"]
    assert payload["sources_failed"] == ["b"]
    assert payload["degraded"] is True


def test_window_spans_the_configured_horizon(event):
    from datetime import date
    payload = build([event()])
    start = date.fromisoformat(payload["window"]["start"])
    end = date.fromisoformat(payload["window"]["end"])
    assert (end - start).days == config.WINDOW_WEEKS * 7


def test_leuven_centre_is_shipped_for_the_client_side_fallback(event):
    payload = build([event()])
    assert payload["leuven_center"] == list(config.LEUVEN_CENTER)


def test_empty_input_still_builds_a_valid_payload():
    payload = build([])
    assert payload["activities"] == []
    assert payload["categories"] == []
    assert payload["place_kinds"] == []
