"""Parsing the Natuur en Bos Multimovepad pages.

Fixtures are trimmed captures of three real detail pages, chosen for their
differences: Meerdaalwoud has the full structured address block, Liezele omits
the Postcode field entirely (the fallback path), and Gerheserbos has an HTML
entity in its title.
"""
import pytest

import multimove


@pytest.fixture
def meerdaalwoud(fixture_text):
    return multimove._parse_detail("multimovepad-meerdaalwoud",
                                   fixture_text("multimovepad_meerdaalwoud.html"))


@pytest.fixture
def liezele(fixture_text):
    return multimove._parse_detail("multimovepad-park-fort-liezele",
                                   fixture_text("multimovepad_liezele.html"))


@pytest.fixture
def gerheserbos(fixture_text):
    return multimove._parse_detail("multimovepad-gerheserbos-heide",
                                   fixture_text("multimovepad_gerheserbos.html"))


# ── coordinates ─────────────────────────────────────────────────────────────
def test_reads_coordinates_from_the_page(meerdaalwoud):
    """Every detail page states its own start point, so these need no geocoding
    at all — which is the whole reason this kind is scraped rather than searched."""
    assert meerdaalwoud["lat"] == pytest.approx(50.8237, abs=0.01)
    assert meerdaalwoud["lng"] == pytest.approx(4.7012, abs=0.01)
    assert meerdaalwoud["geocode_source"] == "natuurenbos"


def test_coordinates_are_plausible_for_belgium(meerdaalwoud, liezele, gerheserbos):
    for rec in (meerdaalwoud, liezele, gerheserbos):
        assert 49.4 < rec["lat"] < 51.6, rec["name"]
        assert 2.5 < rec["lng"] < 6.5, rec["name"]


# ── names ───────────────────────────────────────────────────────────────────
def test_name_comes_from_the_heading(meerdaalwoud):
    assert meerdaalwoud["name"] == "Multimovepad Meerdaalwoud"


def test_html_entities_are_unescaped(gerheserbos):
    """The raw heading contains &amp;, which must not reach the JSON."""
    assert "&amp;" not in gerheserbos["name"]
    assert "&" in gerheserbos["name"] or "heide" in gerheserbos["name"].lower()


# ── address / province ──────────────────────────────────────────────────────
def test_province_is_derived_from_the_postcode(meerdaalwoud):
    assert meerdaalwoud["province"] == "Vlaams-Brabant"
    assert meerdaalwoud["city"] == "Oud-Heverlee"


def test_missing_postcode_field_falls_back_to_the_body(liezele):
    """This page has no "Postcode" label at all; 2870 appears only in prose.
    Without the fallback it was the one pad in nineteen with no province."""
    assert liezele["province"] == "Antwerpen"


def test_every_record_gets_a_city(meerdaalwoud, liezele, gerheserbos):
    for rec in (meerdaalwoud, liezele, gerheserbos):
        assert rec["city"], rec["name"]


# ── the fixed fields ────────────────────────────────────────────────────────
def test_records_are_shaped_for_places_json(meerdaalwoud):
    assert meerdaalwoud["kind"] == "multimove"
    assert meerdaalwoud["source"] == "natuurenbos"
    assert meerdaalwoud["website"].startswith("https://natuurenbos.be/activiteiten/multimovepad/")


def test_a_multimovepad_is_free_and_outdoor(meerdaalwoud):
    assert meerdaalwoud["price_type"] == "free"
    assert meerdaalwoud["price_min_eur"] == 0
    assert meerdaalwoud["indoor_outdoor"] == "outdoor"
    assert meerdaalwoud["indoor"] is False


def test_age_range_matches_the_multimove_programme(meerdaalwoud):
    assert meerdaalwoud["age_min"] == 3
    assert meerdaalwoud["age_max"] == 12
    assert meerdaalwoud["fits_4yo"] and meerdaalwoud["fits_8yo"]


def test_blurb_mentions_the_distance_when_the_page_gives_one(meerdaalwoud):
    assert "km" in meerdaalwoud["blurb_en"]


def test_blurb_uses_a_decimal_point_not_a_comma(meerdaalwoud):
    """The pages write "2,02 km"; a comma there reads as a list in English."""
    assert ",0" not in meerdaalwoud["blurb_en"]


# ── the index ───────────────────────────────────────────────────────────────
def test_index_links_are_extracted(monkeypatch):
    index = """
      <a href="/activiteiten/multimovepad/multimovepad-brasschaat">x</a>
      <a href="/activiteiten/multimovepad/multimovepad-meerdaalwoud">y</a>
      <a href="/activiteiten/multimovepad/multimovepad-meerdaalwoud">dup</a>
      <a href="/activiteiten/iets-anders">no</a>
    """
    monkeypatch.setattr(multimove.time, "sleep", lambda s: None)
    monkeypatch.setattr(multimove.sources, "http_get",
                        lambda url, lang=None: index.encode() if url == multimove.INDEX_URL
                        else b"<h1>Multimovepad X</h1> Locatie 51.0, 4.5 Postcode 2000")

    places = multimove.fetch_places()
    assert len(places) == 2, "duplicate hrefs and unrelated links must be filtered"


def test_index_fetch_failure_returns_empty(monkeypatch):
    def boom(url, lang=None):
        raise RuntimeError("down")
    monkeypatch.setattr(multimove.sources, "http_get", boom)
    assert multimove.fetch_places() == []


def test_one_bad_detail_page_does_not_kill_the_run(monkeypatch):
    index = '<a href="/activiteiten/multimovepad/good">a</a><a href="/activiteiten/multimovepad/bad">b</a>'

    def fetch(url, lang=None):
        if url == multimove.INDEX_URL:
            return index.encode()
        if url.endswith("/bad"):
            raise RuntimeError("500")
        return b"<h1>Multimovepad Good</h1> Locatie 51.0, 4.5 Postcode 2000"
    monkeypatch.setattr(multimove.sources, "http_get", fetch)
    monkeypatch.setattr(multimove.time, "sleep", lambda s: None)

    places = multimove.fetch_places()
    assert [p["name"] for p in places] == ["Multimovepad Good"]
