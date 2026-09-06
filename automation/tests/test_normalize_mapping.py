"""UiTdatabank mapping, the kid-relevance prefilter, and cross-source dedupe."""
import pytest

import config
import normalize
from sources import activity_id, canonicalize_url


# ── ids ─────────────────────────────────────────────────────────────────────
def test_id_is_stable_for_the_same_url():
    url = "https://www.uitinleuven.be/agenda/e/x/abc"
    assert activity_id(url) == activity_id(url)
    assert len(activity_id(url)) == 10


def test_id_differs_for_different_urls():
    assert activity_id("https://a.be/x") != activity_id("https://a.be/y")


@pytest.mark.parametrize("tracked", [
    "https://a.be/x?utm_source=nl",
    "https://a.be/x?utm_medium=email&utm_campaign=y",
    "https://a.be/x#section",
])
def test_tracking_params_and_fragments_do_not_change_the_id(tracked):
    """The same event shared with campaign tags must not enter the feed twice."""
    assert activity_id(tracked) == activity_id("https://a.be/x")


def test_meaningful_query_params_are_kept():
    assert activity_id("https://a.be/x?id=7") != activity_id("https://a.be/x")


@pytest.mark.parametrize("a,b", [
    ("https://a.be/x", "https://a.be/x/"),      # trailing slash
    ("http://a.be/x", "https://a.be/x"),        # scheme
])
def test_trailing_slash_and_scheme_are_NOT_normalised(a, b):
    """Documents a real limitation rather than an intended behaviour: these
    produce different ids, so a feed that changes scheme or slash style would
    duplicate. The fuzzy title+date+city rule in _dedupe is what actually
    catches that today."""
    assert canonicalize_url(a) != canonicalize_url(b)
    assert activity_id(a) != activity_id(b)


# ── kid-relevance prefilter ─────────────────────────────────────────────────
def test_low_age_minimum_survives(make_activity):
    assert normalize._is_kid_relevant(make_activity(age_min=6))


def test_high_age_minimum_needs_a_keyword(make_activity):
    act = make_activity(age_min=18, title_nl="Lezing over fiscaliteit",
                        description_nl="Voor volwassenen")
    assert not normalize._is_kid_relevant(act)


def test_curated_sources_always_survive(make_activity):
    """manual and claude_search records arrive pre-classified and must not be
    second-guessed by a keyword match."""
    for source in ("manual", "claude_search"):
        act = make_activity(source=source, age_min=40, title_nl="Symfonieorkest")
        assert normalize._is_kid_relevant(act)


def test_dutch_kid_keyword_survives(make_activity):
    act = make_activity(age_min=None, title_nl="Kindervoorstelling in de bib",
                        description_nl="")
    assert normalize._is_kid_relevant(act)


def test_french_kid_keyword_survives(make_activity):
    """Francophone sources would otherwise be filtered out before enrichment."""
    act = make_activity(age_min=None, title_nl="Spectacle pour enfants",
                        description_nl="Atelier famille")
    assert normalize._is_kid_relevant(act)


def test_keyword_match_is_case_insensitive(make_activity):
    act = make_activity(age_min=None, title_nl="KINDERATELIER", description_nl="")
    assert normalize._is_kid_relevant(act)


def test_terms_and_labels_are_searched(make_activity):
    act = make_activity(age_min=None, title_nl="Iets", description_nl="niets",
                        _terms=["Kinderen"], _labels=[])
    assert normalize._is_kid_relevant(act)


def test_unrelated_adult_event_is_filtered(make_activity):
    act = make_activity(age_min=None, title_nl="Wijnproeverij",
                        description_nl="Een avond voor liefhebbers", _terms=[], _labels=[])
    assert not normalize._is_kid_relevant(act)


def test_prefilter_threshold_matches_config(make_activity):
    at_limit = make_activity(age_min=config.PREFILTER_MAX_AGE_MIN,
                             title_nl="x", description_nl="y", _terms=[], _labels=[])
    assert normalize._is_kid_relevant(at_limit)


# ── dedupe ──────────────────────────────────────────────────────────────────
def test_identical_ids_are_deduped(make_activity):
    a = make_activity(id="same123456", title_nl="Concert")
    b = make_activity(id="same123456", title_nl="Concert")
    assert len(normalize._dedupe([a, b])) == 1


def test_fuzzy_title_same_date_same_city_is_deduped(make_activity):
    a = make_activity(id="a1", title_nl="Kindervoorstelling De Kleine Prins",
                      date_start="2026-10-20T10:00:00", city="Leuven")
    b = make_activity(id="b2", title_nl="Kindervoorstelling de kleine prins",
                      date_start="2026-10-20T10:00:00", city="Leuven")
    assert len(normalize._dedupe([a, b])) == 1


def test_same_title_different_city_is_kept(make_activity):
    a = make_activity(id="a1", title_nl="Kindervoorstelling", city="Leuven")
    b = make_activity(id="b2", title_nl="Kindervoorstelling", city="Gent")
    assert len(normalize._dedupe([a, b])) == 2


def test_same_title_different_date_is_kept(make_activity):
    """A touring show really does play the same venue on two dates."""
    a = make_activity(id="a1", title_nl="Kindervoorstelling", date_start="2026-10-20T10:00:00")
    b = make_activity(id="b2", title_nl="Kindervoorstelling", date_start="2026-10-27T10:00:00")
    assert len(normalize._dedupe([a, b])) == 2


def test_unrelated_titles_are_kept(make_activity):
    a = make_activity(id="a1", title_nl="Kindervoorstelling")
    b = make_activity(id="b2", title_nl="Rondleiding in de abdij")
    assert len(normalize._dedupe([a, b])) == 2


def test_dedupe_keeps_the_richer_record(make_activity):
    """The narrow region feeds are listed first so they keep their own label;
    dedupe must not throw away the more structured of two copies."""
    thin = make_activity(id="a1", title_nl="Concert", description_nl="",
                         occurrences=[], venue_name=None)
    rich = make_activity(id="b2", title_nl="Concert",
                         description_nl="Een uitgebreide beschrijving",
                         occurrences=[{"start": "2026-10-20T10:00:00"}],
                         venue_name="Zaal")
    kept = normalize._dedupe([thin, rich])
    assert len(kept) == 1
    assert kept[0]["description_nl"]


def test_dedupe_preserves_order_of_survivors(make_activity):
    acts = [make_activity(id=f"id{i}", title_nl=f"Event {i}") for i in range(5)]
    kept = normalize._dedupe(acts)
    assert [a["id"] for a in kept] == [a["id"] for a in acts]


def test_empty_input(make_activity):
    assert normalize._dedupe([]) == []
