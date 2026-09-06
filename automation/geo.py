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


class LookupFailed(Exception):
    """Nominatim did not answer (rate-limited, timeout, 5xx).

    Distinct from "answered, found nothing". The disk cache never expires, so
    caching a transient failure as {lat: null} would permanently mark a place as
    unlocatable — which is how 132 entries ended up null and un-retryable.
    """


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
        raise LookupFailed(query) from exc
    if not results:
        return None  # definitive: Nominatim answered and has no such place
    return float(results[0]["lat"]), float(results[0]["lon"])


def geocode_activities(activities: list[dict]) -> None:
    """Mutates each activity in place: sets lat/lng, geocode_source, distance_km."""
    cache = _load_cache()
    new_lookups = 0
    failed = 0

    for act in activities:
        lat, lng = act.get("lat"), act.get("lng")
        source = "payload" if lat is not None and lng is not None else None

        if source is None:
            name = act.get("venue_name") or act.get("name")
            city = act.get("city")
            # Most precise first. A real street address is often simply absent
            # from OSM ("Am Stadtpark, 4700 Eupen" returns nothing while "Eupen"
            # resolves), and a town-centre point is worth far more than null
            # here: distance_km is what the whole distance filter reads, so an
            # ungeocoded place silently disappears from every search.
            candidates = [
                act.get("address"),
                " ".join(p for p in (name, city, "België") if p) if city or name else None,
                f"{city} België" if city else None,
            ]
            for i, query in enumerate([(q or "").strip() for q in candidates]):
                if not query:
                    continue
                precise = i == 0
                if query in cache:
                    hit = cache[query]
                    if hit.get("lat") is None:
                        continue  # known miss — fall through to the next candidate
                    lat, lng = hit["lat"], hit["lng"]
                    source = "nominatim_cache" if precise else "nominatim_cache_city"
                    break
                try:
                    coords = _nominatim(query)
                except LookupFailed:
                    # Leave it uncached so the next run retries it.
                    failed += 1
                    break
                new_lookups += 1
                cache[query] = (
                    {"lat": coords[0], "lng": coords[1]} if coords
                    else {"lat": None, "lng": None}
                )
                if coords:
                    lat, lng = coords
                    source = "nominatim" if precise else "nominatim_city"
                    break

        act["lat"], act["lng"] = lat, lng
        act["geocode_source"] = source or "none"
        act["distance_km"] = (
            haversine_km((lat, lng), config.LEUVEN_CENTER) if lat is not None and lng is not None else None
        )

    if new_lookups:
        _save_cache(cache)
    if failed:
        log.warning("geocode: %d lookups failed and were NOT cached — re-run to retry", failed)
    log.info("geocode: %d new Nominatim lookups", new_lookups)


# ── postcode -> province ────────────────────────────────────────────────────
# Belgian postcodes are allocated in contiguous provincial blocks, so a place
# with a postcode needs no reverse-geocode to be filed under a province. Names
# match the Dutch spellings already used in data/places.json.
_POSTCODE_PROVINCES = (
    (1000, 1299, "Brussel"),
    (1300, 1499, "Waals-Brabant"),
    (1500, 1999, "Vlaams-Brabant"),
    (2000, 2999, "Antwerpen"),
    (3000, 3499, "Vlaams-Brabant"),
    (3500, 3999, "Limburg"),
    (4000, 4999, "Luik"),
    (5000, 5999, "Namen"),
    (6000, 6599, "Henegouwen"),
    (6600, 6999, "Luxemburg"),
    (7000, 7999, "Henegouwen"),
    (8000, 8999, "West-Vlaanderen"),
    (9000, 9999, "Oost-Vlaanderen"),
)


def province_for_postcode(postcode) -> str | None:
    try:
        pc = int(str(postcode).strip()[:4])
    except (TypeError, ValueError):
        return None
    for lo, hi, name in _POSTCODE_PROVINCES:
        if lo <= pc <= hi:
            return name
    return None
