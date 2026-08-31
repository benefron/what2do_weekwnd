"""Geocoding + distance-from-Leuven.

Prefer coordinates already in the source payload. Fall back to OpenStreetMap
Nominatim for address-only records, throttled to their usage policy, with a
git-committed disk cache so future runs (and anyone cloning the repo) skip the
lookups entirely.
"""
import json
import logging
import math
import time

import httpx

import config

log = logging.getLogger(__name__)

_last_call = 0.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(2 * 6371.0088 * math.asin(math.sqrt(h)), 1)


def _load_cache() -> dict:
    if config.GEOCODE_CACHE_JSON.exists():
        try:
            return json.loads(config.GEOCODE_CACHE_JSON.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config.GEOCODE_CACHE_JSON.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True))


def _nominatim(query: str) -> tuple[float, float] | None:
    global _last_call
    wait = config.NOMINATIM_MIN_INTERVAL_SECONDS - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    try:
        resp = httpx.get(
            config.NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "be,nl,fr,de,lu"},
            headers={"User-Agent": config.NOMINATIM_USER_AGENT},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("nominatim failed for %r: %s", query, exc)
        return None
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def geocode_activities(activities: list[dict]) -> None:
    """Mutates each activity in place: sets lat/lng, geocode_source, distance_km."""
    cache = _load_cache()
    new_lookups = 0

    for act in activities:
        lat, lng = act.get("lat"), act.get("lng")
        source = "payload" if lat is not None and lng is not None else None

        if source is None:
            query = act.get("address") or " ".join(
                p for p in (act.get("venue_name") or act.get("name"), act.get("city"), "België") if p
            )
            query = (query or "").strip()
            if query:
                if query in cache:
                    hit = cache[query]
                    lat, lng, source = hit.get("lat"), hit.get("lng"), "nominatim_cache"
                else:
                    coords = _nominatim(query)
                    new_lookups += 1
                    cache[query] = {"lat": coords[0], "lng": coords[1]} if coords else {"lat": None, "lng": None}
                    if coords:
                        lat, lng, source = coords[0], coords[1], "nominatim"

        act["lat"], act["lng"] = lat, lng
        act["geocode_source"] = source or "none"
        act["distance_km"] = (
            haversine_km((lat, lng), config.LEUVEN_CENTER) if lat is not None and lng is not None else None
        )

    if new_lookups:
        _save_cache(cache)
    log.info("geocode: %d new Nominatim lookups", new_lookups)
