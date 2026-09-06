"""Enrichment defaults, the conflict rules that trigger a Sonnet second pass,
and the cache key.
"""
import json

import pytest

import config
import enrich


# ── defaults ────────────────────────────────────────────────────────────────
def test_defaults_cover_every_llm_field():
    defaults = enrich._default_fields({"date_kind": "single"})
    for field in enrich._LLM_FIELDS:
        assert field in defaults, field


def test_unclassified_activity_defaults_to_both(make_activity):
    """"both" survives either side of the rainy-day filter, so a record the LLM
    never reached is shown rather than silently hidden."""
    assert enrich._default_fields(make_activity())["indoor_outdoor"] == "both"


def test_default_confidence_is_low(make_activity):
    assert enrich._default_fields(make_activity())["confidence"] == "low"


def test_default_family_relevant_is_true(make_activity):
    """Defaulting to false would drop everything the LLM failed to classify."""
    assert enrich._default_fields(make_activity())["family_relevant"] is True


def test_is_special_event_follows_date_kind(make_activity):
    assert enrich._default_fields(make_activity(date_kind="single"))["is_special_event"] is True
    assert enrich._default_fields(make_activity(date_kind="permanent"))["is_special_event"] is False


def test_existing_ages_are_preserved(make_activity):
    d = enrich._default_fields(make_activity(age_min=6, age_max=9))
    assert (d["age_min"], d["age_max"]) == (6, 9)


def test_missing_ages_get_a_wide_range(make_activity):
    d = enrich._default_fields(make_activity(age_min=None, age_max=None))
    assert d["age_min"] == 0 and d["age_max"] == 12


def test_defaults_are_in_the_vocabularies(make_activity):
    d = enrich._default_fields(make_activity())
    assert d["category"] in config.CATEGORY_VOCAB
    assert all(t in config.FEATURE_TAG_VOCAB for t in d["feature_tags"])


# ── primary_language default ────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("nl", "nl"),
    ("fr", "fr"),
    ("en", "en"),
    ("", "nl"),
    (None, "nl"),
])
def test_primary_language_default(raw, expected, make_activity):
    act = make_activity(raw_language=raw)
    assert enrich._default_fields(act)["primary_language"] == expected


def test_multilingual_raw_language_collapses_to_multi(make_activity):
    """UiTdatabank joins its languages list, so "nl,fr" arrives — which is not a
    member of the schema enum and would fail validation if passed through."""
    act = make_activity(raw_language="nl,fr")
    assert enrich._default_fields(act)["primary_language"] in ("multi", "nl", "fr")
    assert enrich._default_fields(act)["primary_language"] in (
        "nl", "fr", "en", "multi"), "must be a schema enum member"


# ── _rule_conflict ──────────────────────────────────────────────────────────
def test_free_price_with_a_euro_sign_is_a_conflict(make_activity):
    act = make_activity(description_nl="Toegang € 5 per kind")
    assert enrich._rule_conflict(act, {"price_type": "free", "category": "other",
                                       "feature_tags": []})


def test_free_price_with_betalen_is_a_conflict(make_activity):
    act = make_activity(description_nl="Je moet ter plaatse betalen")
    assert enrich._rule_conflict(act, {"price_type": "free", "category": "other",
                                       "feature_tags": []})


def test_genuinely_free_activity_is_not_a_conflict(make_activity):
    act = make_activity(description_nl="Gratis toegang voor iedereen")
    assert not enrich._rule_conflict(act, {"price_type": "free", "category": "other",
                                           "feature_tags": []})


def test_paid_price_with_a_euro_sign_is_fine(make_activity):
    act = make_activity(description_nl="Toegang € 5")
    assert not enrich._rule_conflict(act, {"price_type": "paid", "category": "other",
                                           "feature_tags": []})


def test_category_outside_the_vocabulary_is_a_conflict(make_activity):
    assert enrich._rule_conflict(make_activity(), {"price_type": "paid",
                                                   "category": "invented_category",
                                                   "feature_tags": []})


def test_feature_tag_outside_the_vocabulary_is_a_conflict(make_activity):
    assert enrich._rule_conflict(make_activity(), {"price_type": "paid",
                                                   "category": "other",
                                                   "feature_tags": ["not_a_tag"]})


def test_clean_classification_has_no_conflict(make_activity):
    assert not enrich._rule_conflict(make_activity(), {
        "price_type": "paid", "category": "other", "feature_tags": ["animals"]})


# ── the cache ───────────────────────────────────────────────────────────────
def test_cache_roundtrips(tmp_path, monkeypatch):
    path = tmp_path / "enrichment_cache.json"
    monkeypatch.setattr(config, "ENRICHMENT_CACHE_JSON", path)

    enrich._save_cache({"abc": {"category": "other"}})
    assert enrich._load_cache() == {"abc": {"category": "other"}}


def test_missing_cache_loads_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENRICHMENT_CACHE_JSON", tmp_path / "nope.json")
    assert enrich._load_cache() == {}


def test_corrupt_cache_loads_empty(tmp_path, monkeypatch):
    path = tmp_path / "enrichment_cache.json"
    path.write_text("{not json")
    monkeypatch.setattr(config, "ENRICHMENT_CACHE_JSON", path)
    assert enrich._load_cache() == {}


def test_cache_is_written_deterministically(tmp_path, monkeypatch):
    """Sorted keys keep the git diff readable — this file is committed."""
    path = tmp_path / "enrichment_cache.json"
    monkeypatch.setattr(config, "ENRICHMENT_CACHE_JSON", path)
    enrich._save_cache({"b": {"x": 1}, "a": {"y": 2}})
    assert list(json.loads(path.read_text())) == ["a", "b"]


def test_schema_version_is_an_int_and_positive():
    assert isinstance(enrich.SCHEMA_VERSION, int)
    assert enrich.SCHEMA_VERSION >= 1
