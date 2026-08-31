"""Turn raw source records into partial Activity dicts (everything except the
LLM-filled classification fields), then cross-source dedupe.

Output records are ready for geo.py then enrich.py.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher

from dateutil import parser as dateparser

import config
from sources import activity_id, canonicalize_url

log = logging.getLogger(__name__)

_DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}
_NL_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_DUTCH_MONTHS) + r")\s+(\d{4})\b", re.IGNORECASE
)
_NUM_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text or "")).strip()


def _parse_any_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return None
    try:
        dt = dateparser.parse(str(value), dayfirst=True, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _find_date_in_text(text: str) -> datetime | None:
    m = _NL_DATE_RE.search(text or "")
    if m:
        return datetime(int(m.group(3)), _DUTCH_MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=timezone.utc)
    m = _NUM_DATE_RE.search(text or "")
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# ── weekend / holiday bucketing ─────────────────────────────────────────────
def _weekend_windows(today: date) -> tuple[set[date], set[date]]:
    offset = (5 - today.weekday()) % 7
    if today.weekday() >= 5:  # already Sat/Sun -> "this weekend" is the current one
        offset -= 7
    saturday = today + timedelta(days=offset)
    this_wknd = {saturday, saturday + timedelta(days=1)}
    next_wknd = {saturday + timedelta(days=7), saturday + timedelta(days=8)}
    return this_wknd, next_wknd


def _holiday_for(d: date):
    for h in config.SCHOOL_HOLIDAYS:
        if date.fromisoformat(h["start"]) <= d <= date.fromisoformat(h["end"]):
            return h["name"]
    return None


def _bucketize(act: dict, today: date, window_end: date) -> None:
    this_wknd, next_wknd = _weekend_windows(today)
    occ_dates = []
    for occ in act.get("occurrences") or []:
        dt = _parse_any_date(occ.get("start"))
        if dt:
            occ_dates.append(dt.date())
    if not occ_dates and act.get("date_start"):
        dt = _parse_any_date(act["date_start"])
        if dt:
            occ_dates.append(dt.date())

    buckets: set[str] = set()
    holiday_name = None
    in_holiday = False
    for d in occ_dates:
        if d in this_wknd:
            buckets.add("this_weekend")
        if d in next_wknd:
            buckets.add("next_weekend")
        hn = _holiday_for(d)
        if hn:
            in_holiday = True
            holiday_name = holiday_name or hn
            buckets.add("school_holiday")
        if today <= d <= window_end:
            buckets.add("later")
    if act.get("date_kind") == "permanent":
        buckets = {"later"}

    act["weekend_bucket"] = sorted(buckets)
    act["in_school_holiday"] = in_holiday
    act["school_holiday_name"] = holiday_name


# ── per-kind mappers ────────────────────────────────────────────────────────
def _loc_from_jsonld(node: dict) -> dict:
    loc = node.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = loc.get("address") or {}
    if isinstance(addr, str):
        address_str, city, postal = addr, None, None
    else:
        parts = [addr.get("streetAddress"), addr.get("postalCode"), addr.get("addressLocality")]
        address_str = ", ".join(p for p in parts if p) or None
        city = addr.get("addressLocality")
        postal = addr.get("postalCode")
    geo = loc.get("geo") or {}
    lat = geo.get("latitude")
    lng = geo.get("longitude")
    return {
        "venue_name": loc.get("name"),
        "address": address_str,
        "city": city,
        "postal_code": str(postal) if postal else None,
        "lat": float(lat) if lat not in (None, "") else None,
        "lng": float(lng) if lng not in (None, "") else None,
    }


def _price_from_jsonld(node: dict) -> dict:
    offers = node.get("offers")
    if isinstance(offers, dict):
        offers = [offers]
    prices = []
    for off in offers or []:
        p = off.get("price")
        try:
            prices.append(float(p))
        except (TypeError, ValueError):
            continue
    if prices:
        lo, hi = min(prices), max(prices)
        return {
            "price_type": "free" if hi == 0 else "paid",
            "price_min_eur": lo,
            "price_max_eur": hi,
            "price_note_nl": None,
        }
    return {"price_type": "unknown", "price_min_eur": None, "price_max_eur": None, "price_note_nl": None}


def _age_from_jsonld(node: dict) -> tuple[int | None, int | None]:
    rng = node.get("typicalAgeRange")
    if not rng or not isinstance(rng, str):
        return None, None
    nums = re.findall(r"\d+", rng)
    if not nums:
        return None, None
    if len(nums) == 1:
        return int(nums[0]), None
    return int(nums[0]), int(nums[1])


def _from_jsonld(node: dict, run_id: str) -> dict | None:
    url = node.get("url") or node.get("@id")
    name = node.get("name")
    if not url or not name:
        return None
    if isinstance(name, dict):
        name = name.get("nl") or next(iter(name.values()), None)
    description = node.get("description")
    if isinstance(description, dict):
        description = description.get("nl") or next(iter(description.values()), "")
    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")

    sub_events = node.get("subEvent") or []
    occurrences = []
    for se in sub_events if isinstance(sub_events, list) else [sub_events]:
        if isinstance(se, dict) and se.get("startDate"):
            occurrences.append({"start": se.get("startDate"), "end": se.get("endDate")})
    if not occurrences and node.get("startDate"):
        occurrences.append({"start": node.get("startDate"), "end": node.get("endDate")})

    date_kind = "single"
    if node.get("eventSchedule") or node.get("eventScheduleType"):
        date_kind = "recurring"
    elif len(occurrences) > 1:
        date_kind = "multi_day"

    age_min, age_max = _age_from_jsonld(node)
    languages = node.get("inLanguage")
    if isinstance(languages, list):
        languages = ",".join(str(x) for x in languages)

    act = {
        "id": activity_id(url),
        "source": node.get("_source", "unknown"),
        "source_label": node.get("_source_label", ""),
        "source_event_id": node.get("cdbid") or node.get("identifier"),
        "url": canonicalize_url(url),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_run": run_id,
        "title_nl": str(name).strip(),
        "description_nl": _strip_html(str(description or ""))[:600],
        "organizer_nl": (node.get("organizer") or {}).get("name") if isinstance(node.get("organizer"), dict) else None,
        "image_url": image,
        "date_start": occurrences[0]["start"] if occurrences else None,
        "date_end": occurrences[-1].get("end") if occurrences else None,
        "all_day": False,
        "occurrences": occurrences,
        "date_kind": date_kind,
        "age_min": age_min,
        "age_max": age_max,
        "age_source": "typicalAgeRange" if age_min is not None else None,
        "audience": "everyone",
        "raw_language": languages,
        **_loc_from_jsonld(node),
        **_price_from_jsonld(node),
    }
    return act


def _from_rss(item: dict, run_id: str) -> dict | None:
    url, title = item.get("url"), item.get("title")
    if not url or not title:
        return None
    text = _strip_html(item.get("summary_html", ""))
    event_dt = _find_date_in_text(f"{title} {text}")
    occurrences = [{"start": event_dt.isoformat(), "end": None}] if event_dt else []
    return {
        "id": activity_id(url),
        "source": item.get("_source", "unknown"),
        "source_label": item.get("_source_label", ""),
        "source_event_id": None,
        "url": canonicalize_url(url),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_run": run_id,
        "title_nl": title.strip(),
        "description_nl": text[:600],
        "organizer_nl": None,
        "image_url": item.get("image_url"),
        "date_start": occurrences[0]["start"] if occurrences else None,
        "date_end": None,
        "all_day": False,
        "occurrences": occurrences,
        "date_kind": "single",
        "age_min": None, "age_max": None, "age_source": None,
        "audience": "everyone",
        "raw_language": None,
        "venue_name": None, "address": None, "city": None, "postal_code": None,
        "lat": None, "lng": None,
        "price_type": "unknown", "price_min_eur": None, "price_max_eur": None, "price_note_nl": None,
    }


def _from_manual(ov: dict, run_id: str) -> dict:
    url = ov.get("url") or f"manual://{ov.get('id') or ov.get('title_nl')}"
    act = {
        "id": ov.get("id") or activity_id(url),
        "source": ov.get("_source", "manual"),
        "source_label": ov.get("_source_label", "Handmatig"),
        "source_event_id": None,
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_run": run_id,
        "title_nl": ov.get("title_nl", ""),
        "description_nl": (ov.get("description_nl") or "")[:600],
        "organizer_nl": ov.get("organizer_nl"),
        "image_url": ov.get("image_url"),
        "date_start": ov.get("date_start"),
        "date_end": ov.get("date_end"),
        "all_day": ov.get("all_day", False),
        "occurrences": ov.get("occurrences") or ([{"start": ov["date_start"], "end": ov.get("date_end")}] if ov.get("date_start") else []),
        "date_kind": ov.get("date_kind", "permanent"),
        "age_min": ov.get("age_min"), "age_max": ov.get("age_max"),
        "age_source": "manual" if ov.get("age_min") is not None else None,
        "audience": "everyone",
        "raw_language": ov.get("primary_language"),
        "venue_name": ov.get("venue_name"),
        "address": ov.get("address"),
        "city": ov.get("city"),
        "postal_code": ov.get("postal_code"),
        "lat": ov.get("lat"), "lng": ov.get("lng"),
        "price_type": ov.get("price_type", "unknown"),
        "price_min_eur": ov.get("price_min_eur"),
        "price_max_eur": ov.get("price_max_eur"),
        "price_note_nl": ov.get("price_note_nl"),
    }
    # let a manual override carry any classification field straight through
    for k in ("category", "feature_tags", "blurb_en", "primary_language",
              "french_required", "is_special_event", "fits_4yo", "fits_8yo"):
        if k in ov:
            act[k] = ov[k]
    return act


# ── dedupe ──────────────────────────────────────────────────────────────────
def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _dedupe(activities: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for act in activities:
        dup = None
        for other in kept:
            if other["id"] == act["id"]:
                dup = other
                break
            if (
                _similar(act["title_nl"], other["title_nl"]) >= 0.9
                and act.get("date_start") == other.get("date_start")
                and (act.get("city") or "") == (other.get("city") or "")
            ):
                dup = other
                break
        if dup is None:
            kept.append(act)
            continue
        # prefer the record with more structure (address + geo + age)
        score = lambda x: sum(bool(x.get(k)) for k in ("lat", "address", "age_min", "image_url", "description_nl"))
        if score(act) > score(dup):
            kept[kept.index(dup)] = {**act, "url": dup["url"], "id": dup["id"]}
    return kept


# ── entrypoint ──────────────────────────────────────────────────────────────
def normalize_all(raw_records: list[dict], run_id: str) -> list[dict]:
    today = datetime.now(timezone.utc).astimezone().date()
    window_end = today + timedelta(weeks=config.WINDOW_WEEKS)

    activities: list[dict] = []
    for rec in raw_records:
        kind = rec.get("_kind")
        try:
            if kind == "jsonld":
                act = _from_jsonld(rec, run_id)
            elif kind == "rss":
                act = _from_rss(rec, run_id)
            elif kind == "manual":
                act = _from_manual(rec, run_id)
            else:
                act = None
        except Exception as exc:  # noqa: BLE001 - one bad record must not abort
            log.warning("normalize failed for a %s record: %s", kind, exc)
            act = None
        if act and act.get("title_nl"):
            activities.append(act)

    # drop past-only events and adults-only
    fresh: list[dict] = []
    for act in activities:
        if (act.get("age_min") or 0) >= 16:
            continue
        occ_future = False
        for occ in act.get("occurrences") or []:
            dt = _parse_any_date(occ.get("start"))
            if dt and dt.date() >= today:
                occ_future = True
        if act.get("date_kind") == "permanent":
            occ_future = True
        if not (act.get("occurrences")) and act.get("date_kind") != "permanent":
            # unknown date — keep it, frontend shows a "date unknown" bucket
            occ_future = True
        if not occ_future:
            continue
        _bucketize(act, today, window_end)
        fresh.append(act)

    deduped = _dedupe(fresh)
    log.info("normalize: %d raw -> %d normalized -> %d after dedupe", len(raw_records), len(fresh), len(deduped))
    return deduped
