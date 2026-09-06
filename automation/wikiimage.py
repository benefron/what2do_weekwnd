"""Wikipedia/Wikimedia fallback for place photos.

Most Belgian venue sites carry no `og:image` at all — scraping them tops out
around a third of the guide — but the castles, domains, museums and parks in it
are exactly what Wikipedia photographs well.

The catch is that the search generator always answers. Ask it for
"Kasteel van Beersel" and it happily returns the *municipality* of Beersel and
its flag SVG; ask for "Provinciedomein Huizingen" and you get a location map of
Belgium. An image like that on a card is worse than the emoji placeholder, so
this module is mostly two guards:

  title similarity  the article it found must actually be about this place
  file blocklist    no flags, coats of arms, maps, diagrams or SVGs

Images are CC-licensed, so each hit also records the article URL as credit.
Read-only, no API key, no LLM.
"""
import difflib
import logging
import re
import time
import urllib.parse

import httpx

log = logging.getLogger(__name__)

_API = "https://{lang}.wikipedia.org/w/api.php"
_UA = {"User-Agent": "what2do-weekwnd/1.0 (family activity guide; +https://github.com/benefron/what2do_weekwnd)"}

# Below this, the article we found is about something else — a village that
# shares the castle's name, a "list of..." page, the wrong museum.
_MIN_TITLE_RATIO = 0.72

# Symbols and cartography, not photographs.
_BAD_FILE = (
    "vlag", "flag", "wapen", "coat_of_arms", "blason", "locati", "location_map",
    "_map", "map_", "kaart", "carte", "logo", "icon", "seal", "diagram",
    "belgium_adm", "wikidata", "commons-logo",
)


def _looks_like_photo(url: str) -> bool:
    low = url.lower()
    if low.split("?")[0].endswith(".svg"):
        return False  # flags, arms, schematic maps
    return not any(bad in low for bad in _BAD_FILE)


def _norm(s: str) -> str:
    # Only case and punctuation. Deliberately does NOT strip type words like
    # "domein" or "kasteel": drop those and "Provinciedomein Kessel-Lo" becomes
    # indistinguishable from the village of Kessel-Lo, whose article is
    # illustrated with the parish church.
    s = re.sub(r"\(.*?\)", " ", s.lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _title_matches(place: str, title: str) -> bool:
    """The article must be about this place, or about something more specific.

    Containment is only allowed one way. An article whose title is *contained
    in* the place name is the generic thing the place is named after — the
    village, the municipality, the river — and its lead photo will be of that,
    not of the place we want.
    """
    a, b = _norm(place), _norm(title)
    if not a or not b:
        return False
    if a in b:
        return True  # "Bellewaerde" -> "Bellewaerde Park": more specific, fine
    return difflib.SequenceMatcher(None, a, b).ratio() >= _MIN_TITLE_RATIO


def lookup(name: str, lang: str = "nl", timeout: float = 15.0) -> tuple[str, str] | None:
    """-> (image_url, article_url) for `name`, or None if nothing trustworthy."""
    params = {
        "action": "query", "format": "json", "prop": "pageimages",
        "piprop": "original", "generator": "search",
        "gsrsearch": name, "gsrlimit": "3",
    }
    try:
        resp = httpx.get(_API.format(lang=lang) + "?" + urllib.parse.urlencode(params),
                         headers=_UA, timeout=timeout)
        resp.raise_for_status()
        pages = (resp.json().get("query") or {}).get("pages") or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("wikiimage %s (%s): %s", name, lang, exc)
        return None

    # generator=search returns an unordered dict; index is the ranking
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        title = page.get("title") or ""
        img = (page.get("original") or {}).get("source")
        if not img or not title:
            continue
        if not _title_matches(name, title):
            continue
        if not _looks_like_photo(img):
            continue
        article = f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
        return img.split("?")[0], article
    return None


def backfill(places: list[dict], throttle: float = 0.3) -> int:
    """Fill image_url from Wikipedia for places that still have none."""
    added = 0
    for p in places:
        if p.get("image_url") or not p.get("name"):
            continue
        # A francophone place is far likelier to have a French article.
        langs = ("fr", "nl") if p.get("province") in (
            "Luik", "Henegouwen", "Namen", "Luxemburg", "Waals-Brabant") else ("nl", "fr")
        for lang in langs:
            hit = lookup(p["name"], lang)
            time.sleep(throttle)
            if hit:
                p["image_url"], p["image_credit_url"] = hit
                p["image_credit"] = f"Wikipedia ({lang})"
                added += 1
                break
    log.info("wikiimage: +%d images from Wikipedia", added)
    return added
