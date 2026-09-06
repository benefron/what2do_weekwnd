"""og:image selection and cross-kind dedupe.

Dedupe is the subtle one. A search pass restates the same place two ways
("Monde Sauvage Safari" / "Monde Sauvage Safari Parc"), but one site genuinely
can host two venues ("Bellewaerde Park" / "Bellewaerde Aquapark"). Those score
0.90 and 0.88 on string similarity, so no threshold separates them — hence the
prefix + generic-suffix rule, pinned below in both directions.
"""
import pytest

import build_places


# ── _slug ───────────────────────────────────────────────────────────────────
def test_slug_is_url_safe():
    assert build_places._slug("Kasteel van Gaasbeek") == "kasteel-van-gaasbeek"


def test_slug_handles_accents_and_punctuation():
    assert " " not in build_places._slug("Château de Corroy-le-Château")
    assert build_places._slug("A/B & C").strip("-")


def test_slug_is_truncated():
    assert len(build_places._slug("x" * 200)) <= 48


# ── _og_image ───────────────────────────────────────────────────────────────
def html_with(meta):
    return f"<html><head>{meta}</head><body></body></html>"


def stub_fetch(monkeypatch, body):
    monkeypatch.setattr(build_places, "http_get", lambda url: body.encode("utf-8"))


def test_finds_og_image(monkeypatch):
    stub_fetch(monkeypatch, html_with(
        '<meta property="og:image" content="https://example.be/photo.jpg"/>'))
    assert build_places._og_image("https://example.be") == "https://example.be/photo.jpg"


def test_finds_twitter_image(monkeypatch):
    stub_fetch(monkeypatch, html_with(
        '<meta name="twitter:image" content="https://example.be/photo.jpg"/>'))
    assert build_places._og_image("https://example.be") == "https://example.be/photo.jpg"


def test_handles_content_before_property(monkeypatch):
    stub_fetch(monkeypatch, html_with(
        '<meta content="https://example.be/photo.jpg" property="og:image"/>'))
    assert build_places._og_image("https://example.be") == "https://example.be/photo.jpg"


def test_protocol_relative_url_is_upgraded(monkeypatch):
    stub_fetch(monkeypatch, html_with(
        '<meta property="og:image" content="//example.be/photo.jpg"/>'))
    assert build_places._og_image("https://example.be") == "https://example.be/photo.jpg"


def test_relative_url_is_rejected(monkeypatch):
    stub_fetch(monkeypatch, html_with(
        '<meta property="og:image" content="/img/photo.jpg"/>'))
    assert build_places._og_image("https://example.be") is None


@pytest.mark.parametrize("url", [
    "https://example.be/favicon.png",
    "https://example.be/logo.png",
    "https://example.be/assets/-logo.png",
    "https://example.be/content/dam/bjl/logos/BJL_Logo.png",
    "https://example.be/placeholder.jpg",
    "https://example.be/cropped-favicon.png",
    "https://example.be/sprite.png",
    "https://example.be/icon-192.png",
    "https://example.be/icons/x.png",
    "https://example.be/apple-touch-icon.png",
])
def test_rejects_logos_and_icons(monkeypatch, url):
    """A logo on a card is worse than the emoji tile the frontend falls back to."""
    stub_fetch(monkeypatch, html_with(f'<meta property="og:image" content="{url}"/>'))
    assert build_places._og_image("https://example.be") is None


def test_accepts_purpose_built_og_default(monkeypatch):
    """default.png under /og/ is the site's real 1200x630 share art (the
    Walibi/Bellewaerde pattern), not a generic placeholder."""
    stub_fetch(monkeypatch, html_with(
        '<meta property="og:image" content="https://www.walibi.be/content/dam/aql/refdata/og/default.png"/>'))
    assert build_places._og_image("https://www.walibi.be") is not None


def test_rejects_generic_default_outside_og(monkeypatch):
    stub_fetch(monkeypatch, html_with(
        '<meta property="og:image" content="https://example.be/images/default.png"/>'))
    assert build_places._og_image("https://example.be") is None


def test_missing_tag_returns_none(monkeypatch):
    stub_fetch(monkeypatch, html_with("<title>No image here</title>"))
    assert build_places._og_image("https://example.be") is None


def test_fetch_failure_returns_none(monkeypatch):
    def boom(url):
        raise RuntimeError("blocked")
    monkeypatch.setattr(build_places, "http_get", boom)
    assert build_places._og_image("https://example.be") is None


@pytest.mark.parametrize("url", ["", None, "ftp://example.be", "not-a-url"])
def test_non_http_urls_are_skipped(url):
    assert build_places._og_image(url) is None


# ── backfill_images ─────────────────────────────────────────────────────────
def test_backfill_skips_places_that_have_an_image(monkeypatch):
    monkeypatch.setattr(build_places.time, "sleep", lambda s: None)
    monkeypatch.setattr(build_places, "_og_image",
                        lambda url: (_ for _ in ()).throw(AssertionError("should not fetch")))
    places = [{"website": "https://example.be", "image_url": "https://x/a.jpg"}]
    assert build_places.backfill_images(places) == 0


def test_backfill_fills_missing_images(monkeypatch):
    monkeypatch.setattr(build_places.time, "sleep", lambda s: None)
    monkeypatch.setattr(build_places, "_og_image", lambda url: "https://x/found.jpg")
    places = [{"website": "https://example.be"}]
    assert build_places.backfill_images(places) == 1
    assert places[0]["image_url"] == "https://x/found.jpg"


# ── cross-kind dedupe ───────────────────────────────────────────────────────
# _dedupe_cross_kind is defined inside main(); exercise the rule it implements
# through the module-level pieces it depends on.
def _norm(name):
    import re
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _would_merge(name_a, name_b):
    short, long = sorted((_norm(name_a), _norm(name_b)), key=len)
    return bool(short) and long.startswith(short) and long[len(short):] in build_places._GENERIC_SUFFIXES


@pytest.mark.parametrize("a,b", [
    ("Monde Sauvage Safari", "Monde Sauvage Safari Parc"),
    ("Bellewaerde", "Bellewaerde Park"),
    ("Boudewijn Seapark", "Boudewijn Seapark Belgium"),
    ("Technopolis", "Technopolis vzw"),
])
def test_generic_suffix_variants_are_the_same_place(a, b):
    assert _would_merge(a, b)


@pytest.mark.parametrize("a,b", [
    # One site, two genuinely separate venues — must never collapse.
    ("Bellewaerde Park", "Bellewaerde Aquapark"),
    ("Bellewaerde", "Bellewaerde Aquapark"),
    ("Plopsaland De Panne", "Plopsaqua De Panne"),
    ("Plopsa Coo", "Plopsa Station Antwerp"),
    ("Zoo Antwerpen", "Zoo Planckendael"),
])
def test_distinct_venues_are_kept_apart(a, b):
    assert not _would_merge(a, b)


def test_similarity_alone_could_not_separate_these():
    """Documents why the rule is prefix-based rather than a ratio threshold."""
    import difflib
    same = difflib.SequenceMatcher(
        None, _norm("Monde Sauvage Safari"), _norm("Monde Sauvage Safari Parc")).ratio()
    different = difflib.SequenceMatcher(
        None, _norm("Bellewaerde Park"), _norm("Bellewaerde Aquapark")).ratio()
    assert different > 0.85 and same > 0.85, (
        "both score high, so no single cutoff can separate them"
    )


def test_generic_suffixes_do_not_include_meaningful_words():
    for meaningful in ("aqua", "aquapark", "station", "coo", "planckendael"):
        assert meaningful not in build_places._GENERIC_SUFFIXES


def test_kind_rank_covers_every_kind():
    """A kind missing from KIND_RANK silently sorts last in dedupe."""
    for kind in build_places.KINDS:
        assert kind in build_places.KIND_RANK, f"{kind} has no dedupe rank"


def test_scraped_kinds_are_real_kinds():
    for kind in build_places._SCRAPED_KINDS:
        assert kind in build_places.KINDS
