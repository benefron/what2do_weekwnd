"""Guards on the Wikipedia image fallback.

Wikipedia's search generator *always* answers. Ask it for "Kasteel van Beersel"
and it returns the municipality of Beersel, illustrated with a flag SVG; ask for
"Provinciedomein Huizingen" and you get a location map of Belgium. Both guards
below exist to throw those away, so both are pinned here — loosen either and
coats of arms start appearing on cards.
"""
import pytest

import wikiimage


# ── _title_matches ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("place,title", [
    ("Bellewaerde", "Bellewaerde Park"),              # article is more specific
    ("Planckendael", "ZOO Planckendael"),
    ("Bokrijk", "Bokrijk (domein)"),                  # parenthetical is stripped
    ("Museum Plantin-Moretus", "Museum Plantin-Moretus"),
    ("Boudewijn Seapark", "Boudewijn Seapark"),
    ("Technopolis", "Technopolis"),
])
def test_accepts_the_right_article(place, title):
    assert wikiimage._title_matches(place, title)


@pytest.mark.parametrize("place,title", [
    # The article is the generic thing the place is named after. Its lead photo
    # will be of the village/municipality, not of the venue.
    ("Provinciedomein Kessel-Lo", "Kessel-Lo"),
    ("Domein Kiewit", "Kiewit (gehucht)"),
    ("Kasteel van Beersel", "Beersel"),
    ("Provinciedomein Huizingen", "Lijst van dierentuinen in België"),
    ("Speelbos De Hoge Mouw", "Kasterlee"),
])
def test_rejects_the_generic_article(place, title):
    assert not wikiimage._title_matches(place, title)


def test_containment_is_one_directional():
    """Place inside title is fine; title inside place is the trap."""
    assert wikiimage._title_matches("Bellewaerde", "Bellewaerde Park")
    assert not wikiimage._title_matches("Provinciedomein Kessel-Lo", "Kessel-Lo")


def test_type_words_are_not_stripped():
    """Stripping "domein"/"kasteel" would make a domain indistinguishable from
    the village it sits in — which is exactly how Kessel-Lo slipped through."""
    assert not wikiimage._title_matches("Provinciedomein Kessel-Lo", "Kessel-Lo")


@pytest.mark.parametrize("place,title", [("", "Something"), ("Something", ""), ("", "")])
def test_empty_names_never_match(place, title):
    assert not wikiimage._title_matches(place, title)


# ── _looks_like_photo ───────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "https://upload.wikimedia.org/wikipedia/commons/5/5c/Kasteel_van_Gaasbeek_106.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/8/8b/Bokrijk3.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/d/d5/Entrance_Boudewjinpark.JPG",
])
def test_accepts_photographs(url):
    assert wikiimage._looks_like_photo(url)


@pytest.mark.parametrize("url", [
    "https://upload.wikimedia.org/wikipedia/commons/a/ac/Vlag_Beersel.svg",
    "https://upload.wikimedia.org/wikipedia/commons/0/0a/Belgium_adm_location_map.svg",
    "https://upload.wikimedia.org/wikipedia/commons/1/11/Wapen_van_Leuven.png",
    "https://upload.wikimedia.org/wikipedia/commons/2/22/Blason_ville_be.svg",
    "https://upload.wikimedia.org/wikipedia/commons/3/33/Kaart_Vlaanderen.png",
    "https://upload.wikimedia.org/wikipedia/commons/4/44/Flag_of_Belgium.svg",
    "https://upload.wikimedia.org/wikipedia/commons/5/55/Commons-logo.svg",
    "https://upload.wikimedia.org/wikipedia/commons/6/66/Some_diagram.png",
])
def test_rejects_symbols_and_cartography(url):
    assert not wikiimage._looks_like_photo(url)


def test_svg_is_rejected_even_with_an_innocent_name():
    assert not wikiimage._looks_like_photo("https://example.org/Nice_Castle.svg")


def test_query_string_does_not_defeat_the_svg_check():
    assert not wikiimage._looks_like_photo("https://example.org/Nice.svg?width=300")


# ── lookup(), against a stubbed API ─────────────────────────────────────────
def _api_response(pages):
    return {"query": {"pages": {str(i): p for i, p in enumerate(pages)}}}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_lookup_returns_image_and_article(monkeypatch):
    payload = _api_response([{
        "index": 1, "title": "Kasteel van Gaasbeek",
        "original": {"source": "https://upload.wikimedia.org/x/Kasteel_van_Gaasbeek_106.jpg"},
    }])
    monkeypatch.setattr(wikiimage.httpx, "get", lambda *a, **k: _Resp(payload))

    img, article = wikiimage.lookup("Kasteel van Gaasbeek")
    assert img.endswith("Kasteel_van_Gaasbeek_106.jpg")
    assert article == "https://nl.wikipedia.org/wiki/Kasteel_van_Gaasbeek"


def test_lookup_skips_a_wrong_article_and_takes_the_next(monkeypatch):
    payload = _api_response([
        {"index": 1, "title": "Beersel",
         "original": {"source": "https://upload.wikimedia.org/x/Vlag_Beersel.svg"}},
        {"index": 2, "title": "Kasteel van Beersel",
         "original": {"source": "https://upload.wikimedia.org/x/Kasteel_Beersel.jpg"}},
    ])
    monkeypatch.setattr(wikiimage.httpx, "get", lambda *a, **k: _Resp(payload))

    img, _ = wikiimage.lookup("Kasteel van Beersel")
    assert img.endswith("Kasteel_Beersel.jpg")


def test_lookup_returns_none_when_every_candidate_is_rejected(monkeypatch):
    payload = _api_response([{
        "index": 1, "title": "Lijst van dierentuinen in België",
        "original": {"source": "https://upload.wikimedia.org/x/Belgium_adm_location_map.svg"},
    }])
    monkeypatch.setattr(wikiimage.httpx, "get", lambda *a, **k: _Resp(payload))
    assert wikiimage.lookup("Provinciedomein Huizingen") is None


def test_lookup_handles_pages_without_an_image(monkeypatch):
    payload = _api_response([{"index": 1, "title": "Bokrijk (domein)"}])
    monkeypatch.setattr(wikiimage.httpx, "get", lambda *a, **k: _Resp(payload))
    assert wikiimage.lookup("Bokrijk") is None


def test_lookup_swallows_api_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("wikipedia down")
    monkeypatch.setattr(wikiimage.httpx, "get", boom)
    assert wikiimage.lookup("Bokrijk") is None


def test_lookup_strips_the_thumbnail_query(monkeypatch):
    payload = _api_response([{
        "index": 1, "title": "Bokrijk",
        "original": {"source": "https://upload.wikimedia.org/x/Bokrijk3.jpg?utm_source=x"},
    }])
    monkeypatch.setattr(wikiimage.httpx, "get", lambda *a, **k: _Resp(payload))
    img, _ = wikiimage.lookup("Bokrijk")
    assert img == "https://upload.wikimedia.org/x/Bokrijk3.jpg"


# ── backfill() ──────────────────────────────────────────────────────────────
def test_backfill_prefers_french_for_walloon_places(monkeypatch):
    langs = []

    def fake_lookup(name, lang="nl", **kw):
        langs.append(lang)
        return ("https://x/photo.jpg", "https://x/article")
    monkeypatch.setattr(wikiimage, "lookup", fake_lookup)
    monkeypatch.setattr(wikiimage.time, "sleep", lambda s: None)

    places = [{"name": "Château de Modave", "province": "Luik"}]
    assert wikiimage.backfill(places) == 1
    assert langs[0] == "fr"
    assert places[0]["image_credit"] == "Wikipedia (fr)"


def test_backfill_prefers_dutch_for_flemish_places(monkeypatch):
    langs = []
    monkeypatch.setattr(wikiimage, "lookup",
                        lambda name, lang="nl", **kw: (langs.append(lang), None)[1])
    monkeypatch.setattr(wikiimage.time, "sleep", lambda s: None)

    wikiimage.backfill([{"name": "Bokrijk", "province": "Limburg"}])
    assert langs[0] == "nl"


def test_backfill_skips_places_that_already_have_an_image(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not look up a place that already has a photo")
    monkeypatch.setattr(wikiimage, "lookup", boom)

    places = [{"name": "Bokrijk", "image_url": "https://x/existing.jpg"}]
    assert wikiimage.backfill(places) == 0
    assert places[0]["image_url"] == "https://x/existing.jpg"
