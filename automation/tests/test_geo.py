"""Geocoding, the cache contract, and the postcode->province table.

The cache never expires, so *what gets written into it* is the whole ballgame:
caching a rate-limited request as a miss marks a place unlocatable forever, and
because distance_km derives from lat/lng, that place then silently vanishes from
every distance-filtered search. 132 of 414 entries were in that state.
"""
import json

import pytest

import config
import geo

LEUVEN = (50.8798, 4.7005)


# ── haversine ───────────────────────────────────────────────────────────────
def test_haversine_zero_distance():
    assert geo.haversine_km(LEUVEN, LEUVEN) == 0.0


def test_haversine_known_distance():
    brussels = (50.8467, 4.3525)          # ~25 km west of Leuven
    assert 23 < geo.haversine_km(LEUVEN, brussels) < 28


def test_haversine_is_symmetric():
    antwerp = (51.2194, 4.4025)
    assert geo.haversine_km(LEUVEN, antwerp) == geo.haversine_km(antwerp, LEUVEN)


# ── postcode -> province ────────────────────────────────────────────────────
@pytest.mark.parametrize("postcode,province", [
    (1000, "Brussel"),            # lower bound of the capital block
    (1210, "Brussel"),
    (1299, "Brussel"),            # upper bound
    (1300, "Waals-Brabant"),      # first code of the next block
    (1499, "Waals-Brabant"),
    (1500, "Vlaams-Brabant"),
    (1999, "Vlaams-Brabant"),
    (2000, "Antwerpen"),
    (2999, "Antwerpen"),
    (3000, "Vlaams-Brabant"),     # Leuven — the split Vlaams-Brabant block
    (3499, "Vlaams-Brabant"),
    (3500, "Limburg"),
    (3999, "Limburg"),
    (4000, "Luik"),
    (4700, "Luik"),               # Eupen
    (5000, "Namen"),
    (6000, "Henegouwen"),
    (6599, "Henegouwen"),
    (6600, "Luxemburg"),          # the second Henegouwen/Luxemburg boundary
    (6830, "Luxemburg"),          # Bouillon
    (6999, "Luxemburg"),
    (7000, "Henegouwen"),         # Henegouwen resumes above the Luxembourg block
    (8000, "West-Vlaanderen"),
    (9000, "Oost-Vlaanderen"),
    (9999, "Oost-Vlaanderen"),
])
def test_province_for_postcode(postcode, province):
    assert geo.province_for_postcode(postcode) == province


@pytest.mark.parametrize("value", [None, "", "abc", 0, 999, "  ", []])
def test_province_for_postcode_rejects_junk(value):
    assert geo.province_for_postcode(value) is None


def test_province_for_postcode_accepts_strings_and_prefixes():
    assert geo.province_for_postcode("3000") == "Vlaams-Brabant"
    assert geo.province_for_postcode(" 3000 Leuven") == "Vlaams-Brabant"


# ── the cache contract ──────────────────────────────────────────────────────
@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "geocode.json"
    monkeypatch.setattr(config, "GEOCODE_CACHE_JSON", path)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return path


def read_cache(path):
    return json.loads(path.read_text()) if path.exists() else {}


def test_payload_coordinates_win_without_any_lookup(cache_file, monkeypatch):
    def boom(query):
        raise AssertionError("must not geocode a record that already has coords")
    monkeypatch.setattr(geo, "_nominatim", boom)

    acts = [{"lat": 50.9, "lng": 4.7, "city": "Leuven"}]
    geo.geocode_activities(acts)
    assert acts[0]["geocode_source"] == "payload"
    assert acts[0]["distance_km"] is not None


def test_successful_lookup_is_cached(cache_file, monkeypatch):
    monkeypatch.setattr(geo, "_nominatim", lambda q: (51.0, 4.5))
    acts = [{"lat": None, "lng": None, "address": "Teststraat 1, 3000 Leuven"}]
    geo.geocode_activities(acts)

    assert acts[0]["geocode_source"] == "nominatim"
    assert read_cache(cache_file)["Teststraat 1, 3000 Leuven"] == {"lat": 51.0, "lng": 4.5}


def test_definitive_miss_is_cached_as_null(cache_file, monkeypatch):
    """Nominatim answered and has no such place — worth remembering."""
    monkeypatch.setattr(geo, "_nominatim", lambda q: None)
    acts = [{"lat": None, "lng": None, "address": "Nowhere 1, 0000 Nolandia"}]
    geo.geocode_activities(acts)

    cached = read_cache(cache_file)
    assert cached["Nowhere 1, 0000 Nolandia"] == {"lat": None, "lng": None}


def test_transport_failure_is_NOT_cached(cache_file, monkeypatch):
    """The regression that matters: a rate-limited request must stay retryable.

    Caching it as a miss is what marked 132 places permanently unlocatable.
    """
    def rate_limited(query):
        raise geo.LookupFailed(query)
    monkeypatch.setattr(geo, "_nominatim", rate_limited)

    acts = [{"lat": None, "lng": None, "address": "Teststraat 1, 3000 Leuven"}]
    geo.geocode_activities(acts)

    assert acts[0]["lat"] is None
    assert acts[0]["geocode_source"] == "none"
    assert read_cache(cache_file) == {}, "a failed lookup must leave no cache entry"


def test_a_retry_after_failure_succeeds(cache_file, monkeypatch):
    calls = {"n": 0}

    def flaky(query):
        calls["n"] += 1
        if calls["n"] == 1:
            raise geo.LookupFailed(query)
        return (51.0, 4.5)
    monkeypatch.setattr(geo, "_nominatim", flaky)

    acts = [{"lat": None, "lng": None, "address": "Teststraat 1, 3000 Leuven"}]
    geo.geocode_activities(acts)
    assert acts[0]["lat"] is None

    acts = [{"lat": None, "lng": None, "address": "Teststraat 1, 3000 Leuven"}]
    geo.geocode_activities(acts)
    assert acts[0]["lat"] == 51.0


def test_cache_hit_avoids_a_lookup(cache_file, monkeypatch):
    cache_file.write_text(json.dumps({"Teststraat 1, 3000 Leuven": {"lat": 51.0, "lng": 4.5}}))

    def boom(query):
        raise AssertionError("should have been served from cache")
    monkeypatch.setattr(geo, "_nominatim", boom)

    acts = [{"lat": None, "lng": None, "address": "Teststraat 1, 3000 Leuven"}]
    geo.geocode_activities(acts)
    assert acts[0]["geocode_source"] == "nominatim_cache"
    assert acts[0]["lat"] == 51.0


# ── the address -> city fallback ────────────────────────────────────────────
def test_falls_back_to_city_when_the_address_is_unknown(cache_file, monkeypatch):
    """OSM often lacks a specific address ("Am Stadtpark, 4700 Eupen" returns
    nothing) while the town resolves fine. A town-centre point beats null,
    because null drops the place out of every distance filter."""
    seen = []

    def only_city_resolves(query):
        seen.append(query)
        return (50.63, 6.03) if query == "Eupen België" else None
    monkeypatch.setattr(geo, "_nominatim", only_city_resolves)

    acts = [{"lat": None, "lng": None, "name": "Tierpark Eupen",
             "venue_name": "Tierpark Eupen", "city": "Eupen",
             "address": "Am Stadtpark, 4700 Eupen"}]
    geo.geocode_activities(acts)

    assert acts[0]["lat"] == 50.63
    assert acts[0]["geocode_source"] == "nominatim_city"
    assert acts[0]["distance_km"] is not None
    assert seen[0] == "Am Stadtpark, 4700 Eupen", "the precise query must be tried first"


def test_precise_address_short_circuits_the_fallback(cache_file, monkeypatch):
    seen = []

    def everything_resolves(query):
        seen.append(query)
        return (51.0, 4.5)
    monkeypatch.setattr(geo, "_nominatim", everything_resolves)

    acts = [{"lat": None, "lng": None, "venue_name": "Ergens", "city": "Leuven",
             "address": "Teststraat 1, 3000 Leuven"}]
    geo.geocode_activities(acts)

    assert acts[0]["geocode_source"] == "nominatim"
    assert len(seen) == 1, "must not keep querying once the address resolved"


def test_cached_miss_still_falls_through_to_the_city(cache_file, monkeypatch):
    cache_file.write_text(json.dumps({"Am Stadtpark, 4700 Eupen": {"lat": None, "lng": None}}))
    monkeypatch.setattr(geo, "_nominatim",
                        lambda q: (50.63, 6.03) if q == "Eupen België" else None)

    acts = [{"lat": None, "lng": None, "name": "Tierpark Eupen",
             "venue_name": "Tierpark Eupen", "city": "Eupen",
             "address": "Am Stadtpark, 4700 Eupen"}]
    geo.geocode_activities(acts)
    assert acts[0]["lat"] == 50.63


def test_no_address_and_no_city_yields_no_coordinates(cache_file, monkeypatch):
    monkeypatch.setattr(geo, "_nominatim", lambda q: None)
    acts = [{"lat": None, "lng": None}]
    geo.geocode_activities(acts)
    assert acts[0]["geocode_source"] == "none"
    assert acts[0]["distance_km"] is None


def test_distance_is_measured_from_leuven_centre(cache_file, monkeypatch):
    monkeypatch.setattr(geo, "_nominatim", lambda q: None)
    acts = [{"lat": config.LEUVEN_CENTER[0], "lng": config.LEUVEN_CENTER[1]}]
    geo.geocode_activities(acts)
    assert acts[0]["distance_km"] == 0.0
