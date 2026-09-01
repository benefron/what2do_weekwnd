"""Load the permanent guide (data/places.json) and shape each entry as a
published Activity. Read verbatim by the weekly run — never re-fetched.
"""
import json
import logging
from datetime import datetime, timezone

import config
from geo import haversine_km

log = logging.getLogger(__name__)

PLACES_JSON = config.DATA_DIR / "places.json"


def load_places_as_activities(run_id: str) -> list[dict]:
    if not PLACES_JSON.exists():
        return []
    try:
        data = json.loads(PLACES_JSON.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("places.json invalid: %s", exc)
        return []

    vocab = set(config.FEATURE_TAG_VOCAB)
    out: list[dict] = []
    for p in data.get("places", []):
        lat, lng = p.get("lat"), p.get("lng")
        tags = [t for t in (p.get("tags") or []) if t in vocab]
        dist = haversine_km((lat, lng), config.LEUVEN_CENTER) if lat is not None and lng is not None else None
        out.append({
            "id": p["id"],
            "source": p.get("source", "places"),
            "source_label": "Guide",
            "source_event_id": None,
            "url": p.get("website") or f"place://{p['id']}",
            "last_seen_run": run_id,
            "title_nl": p.get("name", ""),
            "description_nl": p.get("description_nl") or p.get("blurb_en") or "",
            "organizer_nl": None,
            "blurb_en": p.get("blurb_en"),
            "image_url": p.get("image_url"),
            "date_start": None,
            "date_end": None,
            "all_day": False,
            "occurrences": [],
            "date_kind": "permanent",
            "weekend_bucket": ["later"],
            "in_school_holiday": False,
            "school_holiday_name": None,
            "venue_name": p.get("name"),
            "address": p.get("address"),
            "city": p.get("city"),
            "postal_code": None,
            "lat": lat,
            "lng": lng,
            "distance_km": round(dist, 1) if dist is not None else None,
            "geocode_source": "places",
            "kind": p.get("kind", "other"),
            "province": p.get("province"),
            "indoor": p.get("indoor"),
            "seasonal": p.get("seasonal"),
            "category": _KIND_TO_CATEGORY.get(p.get("kind"), "other"),
            "feature_tags": tags,
            "audience": "everyone",
            "age_min": p.get("age_min"),
            "age_max": p.get("age_max"),
            "age_source": "curated",
            "fits_4yo": p.get("fits_4yo", True),
            "fits_8yo": p.get("fits_8yo", True),
            "price_type": p.get("price_type", "unknown"),
            "price_min_eur": p.get("price_min_eur"),
            "price_max_eur": p.get("price_max_eur"),
            "price_note_nl": p.get("price_note_nl"),
            "primary_language": "nl",
            "french_required": False,
            "language_note": None,
            "is_special_event": False,
            "is_recurring_class": False,
            "booking_required": None,
            "enrichment_model": p.get("source", "curated"),
            "confidence": "high",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    log.info("places: loaded %d permanent entries", len(out))
    return out


_KIND_TO_CATEGORY = {
    "museum": "museum_exhibition",
    "zoo": "zoo_animal_park",
    "provincial_domain": "nature_farm",
    "speelbos": "nature_farm",
    "playground_indoor": "playground_indoor",
    "playground_outdoor": "playground_indoor",
    "multimove": "sports_active",
    "zomerbar": "market_food",
    "playground_restaurant": "market_food",
    "farm": "nature_farm",
    "attraction_park": "other",
    "castle": "guided_tour",
}
