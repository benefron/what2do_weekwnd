"""Build / expand data/places.json — the permanent kids guide to Belgium.

Run MANUALLY (not part of the weekly cron):

    scripts/build_places.sh                                  # all kinds
    scripts/build_places.sh --kinds speelbos,zomerbar,playground_restaurant

Uses Claude web search to compile Belgium-wide lists per kind, geocodes the new
entries, and merges into data/places.json. Entries with source == "curated" are
never overwritten. Does not commit — review the diff, then commit yourself.
"""
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import geo
import llm_runner

log = logging.getLogger("build_places")

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

This is for a permanent "things to do with kids (ages 4 and 8)" guide, so only
include places that are genuinely worth a family visit and are permanently open
(not one-off events). Aim for 20-60 entries. For each: the real name, city,
province (one of the enum values), a one-sentence factual English description,
the official website, rough price, typical age range, whether it is mainly
indoor, and whether it is seasonal (summer/winter/null).

Do not invent places. If unsure a place exists or is still open, leave it out.
Return JSON matching the schema exactly."""


def build_kind(kind: str) -> list[dict]:
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
            "seasonal": p.get("seasonal"),
            "tags": p.get("tags", []),
        })
    log.info("build_places[%s]: %d places", kind, len(out))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", help="comma-separated subset of: " + ",".join(KINDS))
    args = ap.parse_args()
    # priority order — the kinds the guide is thinnest on come first
    default_order = [
        "provincial_domain", "speelbos", "playground_indoor", "playground_outdoor",
        "zomerbar", "playground_restaurant", "multimove", "farm", "castle",
        "attraction_park", "zoo", "museum",
    ]
    kinds = args.kinds.split(",") if args.kinds else default_order

    existing = json.loads(config.DATA_DIR.joinpath("places.json").read_text())
    by_id = {p["id"]: p for p in existing["places"]}
    curated_names = {p["name"].lower() for p in existing["places"] if p.get("source") == "curated"}

    def flush():
        merged = sorted(by_id.values(), key=lambda p: (p.get("kind", ""), p["name"]))
        geo.geocode_activities(merged)  # fills lat/lng via the shared disk cache
        out = {
            "_comment": existing.get("_comment", ""),
            "updated": date.today().isoformat(),
            "places": [{k: v for k, v in p.items() if k != "distance_km"} for p in merged],
        }
        config.DATA_DIR.joinpath("places.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
        return len(merged)

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
