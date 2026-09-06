"""Build / expand data/places.json — the permanent kids guide to Belgium.

Run MANUALLY (not part of the weekly cron):

    scripts/build_places.sh                                  # all kinds
    scripts/build_places.sh --kinds speelbos,zomerbar,playground_restaurant

Uses Claude web search to compile Belgium-wide lists per kind, geocodes the new
entries, and merges into data/places.json. Entries with source == "curated" are
never overwritten. Does not commit — review the diff, then commit yourself.
"""
import argparse
import difflib
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import geo
import linkcheck
import llm_runner
import multimove
import wikiimage
from sources import http_get

log = logging.getLogger("build_places")

# When the same place turns up under several kinds (Claude over-uses the
# catch-all kinds), keep the most specific one. Lower rank wins.
KIND_RANK = {
    "zomerbar": 0, "playground_restaurant": 1, "multimove": 2,
    "zoo": 3, "castle": 4, "provincial_domain": 5, "farm": 6, "museum": 7,
    "speelbos": 8, "playground_indoor": 9, "playground_outdoor": 10,
    "attraction_park": 11, "other": 12,
}

# Trailing words that add no identity — "X" and "X Parc" are one place, while
# "X Park" and "X Aquapark" are two, because "aqua" is not in here.
_GENERIC_SUFFIXES = {
    "", "park", "parc", "zoo", "safari", "safaripark", "safariparc",
    "domein", "domaine", "centrum", "center", "centre", "belgie", "belgium",
    "vzw", "bv", "nv", "themepark", "pretpark",
}

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _og_image(url: str) -> str | None:
    if not url or not url.startswith("http"):
        return None
    try:
        html = http_get(url).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    m = _OG_IMAGE_RE.search(html) or re.search(
        r'content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']', html, re.IGNORECASE
    )
    if not m:
        return None
    img = m.group(1).strip()
    if img.startswith("//"):
        img = "https:" + img
    if not img.startswith("http"):
        return None
    low = img.lower()
    # Skip logos, favicons and true placeholders — a logo on a card is worse
    # than the emoji tile the frontend falls back to. But a file called
    # default.png sitting under an /og/ path is the site's purpose-built
    # 1200x630 share image (the Walibi/Bellewaerde pattern), which is exactly
    # what we want; only treat "default" as generic when it is not OG art.
    generic_default = ("default.png" in low or "default.jpg" in low) and "/og/" not in low
    if generic_default or any(bad in low for bad in (
        "favicon", "/logo", "logo.", "-logo",
        "placeholder", "cropped-", "sprite", "icon-", "/icons/", "apple-touch",
    )):
        return None
    return img


def backfill_images(places: list[dict]) -> int:
    n = 0
    for p in places:
        if p.get("image_url") or not p.get("website"):
            continue
        img = _og_image(p["website"])
        time.sleep(0.3)
        if img:
            p["image_url"] = img
            n += 1
    return n

KINDS = {
    "museum": "child-friendly museums (science, history, transport, nature, art with a family offer)",
    "zoo": "zoos, animal parks and aquariums",
    "provincial_domain": "provincial and public recreation domains with playgrounds / lakes / animals (all provinces, incl. Wallonia's domaines provinciaux)",
    "speelbos": "designated play forests / nature play zones (speelbos, speelzone, zone de jeu en forêt)",
    "playground_indoor": "indoor playgrounds and soft-play centres",
    "playground_outdoor": "large or notable outdoor adventure playgrounds",
    "multimove": "permanent MultiMove / beweegroute / movement-skills trails for young children",
    "zomerbar": "seasonal summer bars / pop-up terraces that are family- and child-friendly",
    "playground_restaurant": "cafes/restaurants/farm-cafes with a large playground right next to the terrace (speeltuinrestaurant, hoevecafe met speeltuin)",
    "farm": "kinderboerderijen / petting farms / pick-your-own farms open to visitors",
    "attraction_park": "theme parks and family amusement parks",
    "castle": "castles / chateaux that are a good visit with children",
}

_SCHEMA = {
    "type": "object",
    "required": ["places"],
    "properties": {
        "places": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "city", "province", "description_en", "website"],
                "properties": {
                    "name": {"type": "string"},
                    "city": {"type": "string"},
                    "province": {
                        "type": "string",
                        "enum": [
                            "Antwerpen", "Vlaams-Brabant", "Limburg", "Oost-Vlaanderen",
                            "West-Vlaanderen", "Brussel", "Waals-Brabant", "Henegouwen",
                            "Luik", "Luxemburg", "Namen",
                        ],
                    },
                    "address": {"type": ["string", "null"]},
                    "description_en": {"type": "string", "maxLength": 320},
                    "website": {"type": "string"},
                    "price_type": {"type": "string", "enum": ["free", "paid", "unknown"]},
                    "price_min_eur": {"type": ["number", "null"]},
                    "price_max_eur": {"type": ["number", "null"]},
                    "age_min": {"type": ["integer", "null"]},
                    "age_max": {"type": ["integer", "null"]},
                    "indoor": {"type": "boolean"},
                    "indoor_outdoor": {"type": "string", "enum": ["indoor", "outdoor", "both"]},
                    "seasonal": {"type": ["string", "null"], "enum": ["summer", "winter", None]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def _prompt(kind: str, desc: str) -> str:
    return f"""Use web search to compile as COMPLETE a list as you can of {desc}
across ALL of Belgium — every province, Flanders AND Wallonia AND Brussels.

This is for a permanent "things to do with the kids" guide used by families all
over Belgium with children of any age from toddler to early teens, speaking
Dutch, French or English. So do not filter to one age or one language region:
include what is worth a family visit anywhere in the country, and give each
entry its real age range rather than assuming one. Only permanently open places
(not one-off events). Aim for 20-60 entries.

Only list places that genuinely fit "{desc}". Do NOT pad the list with places
that really belong to another category (e.g. a museum does not belong in a
theme-parks list, a zoo does not belong in a castles list).

For each: the real name, city, province (one of the enum values), a one-sentence
factual English description, the official website (homepage URL), rough price,
typical age range, and whether it is seasonal (summer/winter/null). For
indoor_outdoor answer "indoor", "outdoor", or "both" — "both" when a visit
genuinely has an indoor and an outdoor part, or there is a real wet-weather
fallback (a zoo with pavilions, a castle with grounds, a farm with a barn).

Do not invent places. If unsure a place exists or is still open, leave it out.
Return JSON matching the schema exactly."""


# Kinds with a canonical public index, which we scrape instead of paying for a
# web search. The LLM pass found 1 of the 19 Multimovepaden and gave it a URL
# that 404s; the index gives all 19 with exact coordinates, for free.
_SCRAPED_KINDS = {"multimove": multimove.fetch_places}


def build_kind(kind: str) -> list[dict]:
    if kind in _SCRAPED_KINDS:
        out = []
        for p in _SCRAPED_KINDS[kind]():
            rec = {"id": f"place-{kind}-{_slug(p['name'])}", "province": None,
                   "price_note_nl": None}
            rec.update(p)
            out.append(rec)
        log.info("build_places[%s]: %d places (scraped)", kind, len(out))
        return out

    desc = KINDS[kind]
    try:
        structured = llm_runner.run_search_with_schema(
            _prompt(kind, desc),
            _SCHEMA,
            config.CLAUDE_SEARCH_MODEL,
            "1.50",
            "medium",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("build_places[%s] failed: %s", kind, exc)
        return []
    out = []
    for p in structured.get("places", []):
        out.append({
            "id": f"place-{kind}-{_slug(p['name'])}",
            "kind": kind,
            "source": "claude_build",
            "name": p["name"],
            "city": p.get("city"),
            "province": p.get("province"),
            "address": p.get("address"),
            "lat": None,
            "lng": None,
            "website": p.get("website"),
            "description_nl": p.get("description_en", ""),
            "blurb_en": p.get("description_en", "")[:160],
            "price_type": p.get("price_type", "unknown"),
            "price_min_eur": p.get("price_min_eur"),
            "price_max_eur": p.get("price_max_eur"),
            "price_note_nl": None,
            "age_min": p.get("age_min"),
            "age_max": p.get("age_max"),
            "fits_4yo": (p.get("age_min") or 0) <= 4 <= (p.get("age_max") or 99),
            "fits_8yo": (p.get("age_min") or 0) <= 8 <= (p.get("age_max") or 99),
            "indoor": p.get("indoor", False),
            "indoor_outdoor": p.get("indoor_outdoor"),
            "seasonal": p.get("seasonal"),
            "tags": p.get("tags", []),
        })
    log.info("build_places[%s]: %d places", kind, len(out))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", help="comma-separated subset of: " + ",".join(KINDS))
    ap.add_argument("--images", action="store_true",
                    help="only backfill og:image for places missing a photo, then exit")
    ap.add_argument("--dedupe", action="store_true",
                    help="only re-run cross-kind dedupe + geocode, then exit")
    ap.add_argument("--links", action="store_true",
                    help="only check every website, repair 404s to their site root, "
                         "set link_ok, then exit")
    args = ap.parse_args()
    # priority order — the kinds the guide is thinnest on come first
    default_order = [
        "provincial_domain", "speelbos", "playground_indoor", "playground_outdoor",
        "zomerbar", "playground_restaurant", "multimove", "farm", "castle",
        "attraction_park", "zoo", "museum",
    ]
    kinds = args.kinds.split(",") if args.kinds else default_order

    places_path = config.DATA_DIR / "places.json"
    existing = json.loads(places_path.read_text())
    by_id = {p["id"]: p for p in existing["places"]}
    curated_names = {p["name"].lower() for p in existing["places"] if p.get("source") == "curated"}

    def _better(p: dict, cur: dict) -> bool:
        # curated always wins; otherwise the more specific kind wins
        return (
            (p.get("source") == "curated", -KIND_RANK.get(p.get("kind"), 12))
            > (cur.get("source") == "curated", -KIND_RANK.get(cur.get("kind"), 12))
        )

    def _dedupe_cross_kind(items: list[dict]) -> list[dict]:
        best: dict[str, dict] = {}
        for p in items:
            key = re.sub(r"[^a-z0-9]", "", (p.get("name") or "").lower())
            cur = best.get(key)
            if cur is None:
                best[key] = p
            elif _better(p, cur):
                best[key] = p

        # Exact-name matching misses the way a search pass restates the same
        # place ("Monde Sauvage Safari" / "Monde Sauvage Safari Parc").
        #
        # Similarity alone cannot separate those from the real case of one site
        # hosting two venues: Monde Sauvage scores 0.90 and Bellewaerde
        # Park/Aquapark 0.88, so any threshold that merges the first also merges
        # the second. What actually distinguishes them is *how* the names differ
        # — same host, one name a prefix of the other, and the leftover being a
        # generic venue word rather than something meaningful like "aqua".
        out: list[dict] = []
        for p in sorted(best.values(), key=lambda x: len(x.get("name") or "")):
            host = urlsplit(p.get("website") or "").netloc.lower().removeprefix("www.")
            pk = re.sub(r"[^a-z0-9]", "", (p.get("name") or "").lower())
            dup_at = None
            for i, q in enumerate(out):
                qhost = urlsplit(q.get("website") or "").netloc.lower().removeprefix("www.")
                if not host or host != qhost or not pk:
                    continue
                qk = re.sub(r"[^a-z0-9]", "", (q.get("name") or "").lower())
                short, long = sorted((pk, qk), key=len)
                if short and long.startswith(short) and long[len(short):] in _GENERIC_SUFFIXES:
                    dup_at = i
                    break
            if dup_at is None:
                out.append(p)
            elif _better(p, out[dup_at]):
                out[dup_at] = p
        return out

    def flush():
        merged = _dedupe_cross_kind(list(by_id.values()))
        merged.sort(key=lambda p: (p.get("kind", ""), p["name"]))
        geo.geocode_activities(merged)  # fills lat/lng via the shared disk cache
        out = {
            "_comment": existing.get("_comment", ""),
            "updated": date.today().isoformat(),
            "places": [{k: v for k, v in p.items() if k != "distance_km"} for p in merged],
        }
        places_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        return len(merged)

    if args.images:
        added = backfill_images(existing["places"])
        # Most venue sites carry no og:image at all, so Wikipedia does the bulk
        # of the work here; it only fills what the site itself didn't provide.
        added += wikiimage.backfill(existing["places"])
        places_path.write_text(json.dumps(
            {"_comment": existing.get("_comment", ""), "updated": date.today().isoformat(),
             "places": existing["places"]},
            ensure_ascii=False, indent=2))
        log.info("images: +%d og:image backfilled", added)
        return 0

    if args.links:
        linkcheck.check_places(existing["places"])
        places_path.write_text(json.dumps(
            {"_comment": existing.get("_comment", ""), "updated": date.today().isoformat(),
             "places": existing["places"]},
            ensure_ascii=False, indent=2))
        return 0

    if args.dedupe:
        total = flush()
        log.info("dedupe: %d places after cross-kind dedupe", total)
        return 0

    added = 0
    for kind in kinds:
        if kind not in KINDS:
            log.warning("unknown kind %r, skipping", kind)
            continue
        for place in build_kind(kind):
            if place["name"].lower() in curated_names:
                continue
            if place["id"] in by_id and by_id[place["id"]].get("source") == "curated":
                continue
            by_id[place["id"]] = place
            added += 1
        total = flush()  # save after every kind so a kill doesn't lose work
        log.info("places.json: %d total (+%d new so far)", total, added)

    log.info("done. Review the diff, then commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
