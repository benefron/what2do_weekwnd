"""Deterministic builder for the `multimove` place kind.

A Multimovepad is a permanent movement trail — wooden obstacles and natural
challenges teaching the 12 MultiMove movement skills, ages ~3-12, free, almost
always inside a forest or park. Natuur en Bos publishes a complete index of
them, and every detail page carries an explicit `Locatie <lat>, <lng>`.

That makes this kind a scrape, not a search: `build_places.build_kind` sends
"multimove" here instead of to the LLM, which found exactly one of the 19 and
gave it a URL that now 404s.

Read-only. Returns place dicts in the same shape `build_places` writes.
"""
import html as _html
import logging
import re
import time

import geo
import sources

log = logging.getLogger(__name__)

INDEX_URL = "https://natuurenbos.be/activiteiten/multimovepad"
_BASE = "https://natuurenbos.be"

_LINK_RE = re.compile(r'href="(/activiteiten/multimovepad/[^"?#]+)"')
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
# "Locatie 51.03041, 4.97503" — the pad's own start point, already geocoded.
_LOC_RE = re.compile(r"Locatie\s+(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)")
_PC_RE = re.compile(r"Postcode\s+(\d{4})")
_VERTREK_RE = re.compile(r"Vertrekpunt\s+([^|]{4,80}?)\s+(?:Straat|Postcode|Afstand|Locatie)\b")
_STREET_RE = re.compile(r"Straat\s+(.+?)\s+Stad\s+")
_CITY_RE = re.compile(r"\bStad\s+([A-Za-zÀ-ÿ'’\-\. ]{2,40}?)\s+Adres\b")
_DIST_RE = re.compile(r"Afstand\s+([\d,\.]+)\s*km")


def _text(raw: str) -> str:
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))


def _first(pattern: re.Pattern, text: str, group: int = 1):
    m = pattern.search(text)
    return m.group(group).strip() if m else None


def _parse_detail(slug: str, raw: str) -> dict | None:
    text = _text(raw)
    h1 = _H1_RE.search(raw)
    name = _html.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h1.group(1)))
    ).strip() if h1 else None
    if not name:
        # slug is "multimovepad-vrijbroekpark-mechelen"
        name = slug.replace("-", " ").title()

    lat = lng = None
    loc = _LOC_RE.search(text)
    if loc:
        lat, lng = float(loc.group(1)), float(loc.group(2))

    postal = _first(_PC_RE, text)
    if not postal:
        # A few pages omit the Postcode field. Fall back to the first Belgian
        # postcode in the article body — everything after the nav boilerplate
        # starts is links and footers, so cut there before searching.
        body = text.split("ANB Main navigation", 1)[0]
        postal = _first(re.compile(r"\b([1-9]\d{3})\b"), body)
    city = _first(_CITY_RE, text)
    street = _first(_STREET_RE, text)
    vertrek = _first(_VERTREK_RE, text)

    # The "Straat" field is inconsistent: on some pages it holds a real street,
    # on others just the locality. Prefer the explicit Vertrekpunt address.
    address = vertrek or (f"{street}, {postal} {city}" if street and city else None)
    if not city and vertrek:
        m = re.search(r"\d{4}\s+([A-Za-zÀ-ÿ'’\-\. ]{2,40})$", vertrek)
        city = m.group(1).strip() if m else None
    if not city:
        city = name.replace("Multimovepad", "").strip().split()[-1]

    dist = _first(_DIST_RE, text)
    blurb = (
        f"Free waymarked movement trail in the woods — climbing, balancing and "
        f"scrambling obstacles built for kids roughly 3-12"
        + (f", {dist.replace(',', '.')} km long" if dist else "")
        + "."
    )

    return {
        "name": name,
        "kind": "multimove",
        "city": city,
        "province": geo.province_for_postcode(postal),
        "postal_code": postal,
        "address": address,
        "website": f"{_BASE}/activiteiten/multimovepad/{slug}",
        "lat": lat,
        "lng": lng,
        "geocode_source": "natuurenbos" if lat is not None else None,
        "indoor": False,
        "indoor_outdoor": "outdoor",
        "price_type": "free",
        "price_min_eur": 0,
        "price_max_eur": 0,
        "age_min": 3,
        "age_max": 12,
        "fits_4yo": True,
        "fits_8yo": True,
        "blurb_en": blurb,
        "description_nl": None,
        "tags": ["nature_play"],
        "seasonal": None,
        "source": "natuurenbos",
    }


def fetch_places() -> list[dict]:
    """Every Multimovepad in the Natuur en Bos index, with coordinates."""
    try:
        index = sources.http_get(INDEX_URL).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        log.warning("multimove: index fetch failed: %s", exc)
        return []

    slugs = sorted({p.rsplit("/", 1)[-1] for p in _LINK_RE.findall(index)})
    log.info("multimove: %d pads in the index", len(slugs))

    out: list[dict] = []
    for slug in slugs:
        url = f"{_BASE}/activiteiten/multimovepad/{slug}"
        try:
            page = sources.http_get(url).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            log.warning("multimove: %s failed: %s", slug, exc)
            continue
        rec = _parse_detail(slug, page)
        if rec:
            out.append(rec)
        time.sleep(0.3)

    located = sum(1 for r in out if r.get("lat") is not None)
    log.info("multimove: %d pads parsed, %d with coordinates", len(out), located)
    return out
