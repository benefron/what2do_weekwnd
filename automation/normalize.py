"""Turn raw source records into partial Activity dicts (everything except the
LLM-filled classification fields), apply a kid-relevance prefilter, then
cross-source dedupe.

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

_TAG_RE = re.compile(r"<[^>]+>")
_AGE_RE = re.compile(r"^\s*(\d+)?\s*-\s*(\d+)?\s*$")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text or "")).strip()


def _pick_lang(value, prefer=("nl", "en", "fr", "de")):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in prefer:
            if value.get(k):
                return value[k]
        return next(iter(value.values()), None)
    return None


def _parse_any_date(value) -> datetime | None:
    if not value or isinstance(value, (int, float)):
        return None
    try:
        dt = dateparser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_age_range(raw: str | None) -> tuple[int | None, int | None]:
    if not raw or not isinstance(raw, str):
        return None, None
    m = _AGE_RE.match(raw)
    if not m:
        nums = re.findall(r"\d+", raw)
        if not nums:
            return None, None
        return int(nums[0]), (int(nums[1]) if len(nums) > 1 else None)
    lo = int(m.group(1)) if m.group(1) else None
    hi = int(m.group(2)) if m.group(2) else None
    return lo, hi


# ── weekend / holiday bucketing ─────────────────────────────────────────────
def _weekend_windows(today: date) -> tuple[set[date], set[date]]:
    offset = (5 - today.weekday()) % 7
    if today.weekday() >= 5:
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


def _occ_dates(act: dict) -> list[date]:
    out = []
    for occ in act.get("occurrences") or []:
        dt = _parse_any_date(occ.get("start"))
        if dt:
            out.append(dt.date())
    if not out and act.get("date_start"):
        dt = _parse_any_date(act["date_start"])
        if dt:
            out.append(dt.date())
    return out


def _bucketize(act: dict, today: date, window_end: date) -> None:
    this_wknd, next_wknd = _weekend_windows(today)
    buckets: set[str] = set()
    holiday_name = None
    in_holiday = False

    if act.get("date_kind") == "permanent":
        act["weekend_bucket"] = ["later"]
        act["in_school_holiday"] = False
        act["school_holiday_name"] = None
        return

    for d in _occ_dates(act):
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

    # a periodic/multi-day run that spans a weekend or holiday even if we only
    # captured its start/end
    ds, de = _parse_any_date(act.get("date_start")), _parse_any_date(act.get("date_end"))
    if ds and de:
        span = {ds.date() + timedelta(days=i) for i in range((de.date() - ds.date()).days + 1)}
        if span & this_wknd:
            buckets.add("this_weekend")
        if span & next_wknd:
            buckets.add("next_weekend")
        for d in span:
            if _holiday_for(d):
                in_holiday = True
                holiday_name = holiday_name or _holiday_for(d)
                buckets.add("school_holiday")
        if any(today <= d <= window_end for d in span):
            buckets.add("later")

    act["weekend_bucket"] = sorted(buckets)
    act["in_school_holiday"] = in_holiday
    act["school_holiday_name"] = holiday_name


# ── UiTdatabank JSON-LD ─────────────────────────────────────────────────────
def _uit_location(node: dict) -> dict:
    loc = node.get("location") or {}
    addr = _pick_lang(loc.get("address")) or {}
    if isinstance(addr, str):
        addr = {}
    geo = loc.get("geo") or {}
    parts = [addr.get("streetAddress"), addr.get("postalCode"), addr.get("addressLocality")]
    return {
        "venue_name": _pick_lang(loc.get("name")),
        "address": ", ".join(p for p in parts if p) or None,
        "city": addr.get("addressLocality"),
        "postal_code": str(addr["postalCode"]) if addr.get("postalCode") else None,
        "lat": geo.get("latitude"),
        "lng": geo.get("longitude"),
    }


def _uit_price(node: dict) -> dict:
    prices = []
    for p in node.get("priceInfo") or []:
        try:
            prices.append(float(p["price"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not prices:
        return {"price_type": "unknown", "price_min_eur": None, "price_max_eur": None, "price_note_nl": None}
    lo, hi = min(prices), max(prices)
    note = next((_pick_lang(p.get("name")) for p in node["priceInfo"] if p.get("name")), None)
    return {
        "price_type": "free" if hi == 0 else "paid",
        "price_min_eur": lo,
        "price_max_eur": hi,
        "price_note_nl": note,
    }


def _uit_image(node: dict) -> str | None:
    url = None
    if isinstance(node.get("image"), str):
        url = node["image"]
    else:
        for mo in node.get("mediaObject") or []:
            if mo.get("contentUrl") or mo.get("@id"):
                url = mo.get("contentUrl") or mo.get("@id")
                break
    if not url:
        return None
    # The raw images.uitdatabank.be files are often 8000px multi-MB PNGs and
    # 301-redirect to imgix. Point straight at imgix with a card-sized crop so
    # the frontend loads a ~60KB image instead of choking on the original.
    m = re.search(r"/([0-9a-f-]{36}\.\w+)(?:$|\?)", url)
    if m:
        return f"https://images-prod-uitdatabank.imgix.net/{m.group(1)}?auto=format&fit=crop&w=768&h=480"
    return url


def _uit_occurrences(node: dict) -> list[dict]:
    subs = node.get("subEvent")
    occ = []
    if isinstance(subs, list):
        for se in subs:
            if se.get("startDate"):
                occ.append({"start": se["startDate"], "end": se.get("endDate")})
    if not occ and node.get("startDate"):
        occ.append({"start": node["startDate"], "end": node.get("endDate")})
    return occ


def _uit_terms(node: dict) -> list[str]:
    return [t.get("label") for t in node.get("terms") or [] if t.get("label")]


def _from_uitdatabank(node: dict, run_id: str) -> dict | None:
    uuid = node.get("_uuid") or (node.get("@id") or "").rstrip("/").split("/")[-1]
    name = _pick_lang(node.get("name"))
    if not uuid or not name:
        return None
    same_as = node.get("sameAs") or []
    public_url = next((u for u in same_as if "uitinvlaanderen" in u or "uitin" in u), None)
    public_url = public_url or f"https://www.uitinleuven.be/agenda/e/x/{uuid}"

    cal = node.get("calendarType") or "single"
    date_kind = {
        "single": "single", "multiple": "multi_day",
        "periodic": "recurring", "permanent": "permanent",
    }.get(cal, "single")
    occ = _uit_occurrences(node)
    age_min, age_max = _parse_age_range(node.get("typicalAgeRange"))
    langs = node.get("languages") or []

    return {
        "id": activity_id(public_url),
        "source": node.get("_source", "uitdatabank"),
        "source_label": node.get("_source_label", "UiT"),
        "source_event_id": uuid,
        "url": canonicalize_url(public_url),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_run": run_id,
        "title_nl": str(name).strip(),
        "description_nl": _strip_html(_pick_lang(node.get("description")) or "")[:600],
        "organizer_nl": _pick_lang((node.get("organizer") or {}).get("name")) if isinstance(node.get("organizer"), dict) else None,
        "image_url": _uit_image(node),
        "date_start": node.get("startDate"),
        "date_end": node.get("endDate"),
        "all_day": False,
        "occurrences": occ,
        "date_kind": date_kind,
        "age_min": age_min,
        "age_max": age_max,
        "age_source": "typicalAgeRange" if age_min is not None else None,
        "audience": (node.get("audience") or {}).get("audienceType", "everyone"),
        "raw_language": ",".join(langs) if langs else None,
        "_terms": _uit_terms(node),
        "_labels": node.get("labels") or [],
        **_uit_location(node),
        **_uit_price(node),
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
        "occurrences": ov.get("occurrences")
        or ([{"start": ov["date_start"], "end": ov.get("date_end")}] if ov.get("date_start") else []),
        "date_kind": ov.get("date_kind", "permanent"),
        "age_min": ov.get("age_min"),
        "age_max": ov.get("age_max"),
        "age_source": "manual" if ov.get("age_min") is not None else None,
        "audience": "everyone",
        "raw_language": ov.get("primary_language"),
        "_terms": [],
        "_labels": [],
        "venue_name": ov.get("venue_name"),
        "address": ov.get("address"),
        "city": ov.get("city"),
        "postal_code": ov.get("postal_code"),
        "lat": ov.get("lat"),
        "lng": ov.get("lng"),
        "price_type": ov.get("price_type", "unknown"),
        "price_min_eur": ov.get("price_min_eur"),
        "price_max_eur": ov.get("price_max_eur"),
        "price_note_nl": ov.get("price_note_nl"),
    }
    for k in ("category", "feature_tags", "blurb_en", "primary_language",
              "french_required", "is_special_event", "fits_4yo", "fits_8yo"):
        if k in ov:
            act[k] = ov[k]
    return act


# ── kid-relevance prefilter ─────────────────────────────────────────────────
def _is_kid_relevant(act: dict) -> bool:
    if act.get("source") == "manual":
        return True
    lo = act.get("age_min")
    if lo is not None and lo <= config.PREFILTER_MAX_AGE_MIN:
        return True
    hay = " ".join([
        act.get("title_nl", ""),
        act.get("description_nl", ""),
        " ".join(act.get("_terms") or []),
        " ".join(str(x) for x in (act.get("_labels") or [])),
    ]).lower()
    return any(kw in hay for kw in config.KID_KEYWORDS)


# ── dedupe ──────────────────────────────────────────────────────────────────
def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _dedupe(activities: list[dict]) -> list[dict]:
    kept: list[dict] = []
    score = lambda x: sum(bool(x.get(k)) for k in ("lat", "address", "age_min", "image_url", "description_nl"))
    for act in activities:
        dup = None
        for other in kept:
            if other["id"] == act["id"] or (
                _similar(act["title_nl"], other["title_nl"]) >= 0.9
                and act.get("date_start") == other.get("date_start")
                and (act.get("city") or "") == (other.get("city") or "")
            ):
                dup = other
                break
        if dup is None:
            kept.append(act)
        elif score(act) > score(dup):
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
            if kind == "uitdatabank":
                act = _from_uitdatabank(rec, run_id)
            elif kind == "manual":
                act = _from_manual(rec, run_id)
            else:
                act = None
        except Exception as exc:  # noqa: BLE001
            log.warning("normalize failed for a %s record: %s", kind, exc)
            act = None
        if act and act.get("title_nl"):
            activities.append(act)

    kid = [a for a in activities if _is_kid_relevant(a)]

    fresh: list[dict] = []
    for act in kid:
        if (act.get("age_min") or 0) >= 16:
            continue
        future = act.get("date_kind") == "permanent" or not act.get("occurrences")
        for d in _occ_dates(act):
            if d >= today:
                future = True
        de = _parse_any_date(act.get("date_end"))
        if de and de.date() >= today:
            future = True
        if not future:
            continue
        _bucketize(act, today, window_end)
        # drop things whose only future window is beyond our horizon
        if act.get("date_kind") != "permanent" and not act.get("weekend_bucket"):
            continue
        for k in ("_terms", "_labels"):
            act.pop(k, None)
        fresh.append(act)

    deduped = _dedupe(fresh)
    log.info(
        "normalize: %d raw -> %d parsed -> %d kid-relevant -> %d in-window -> %d deduped",
        len(raw_records), len(activities), len(kid), len(fresh), len(deduped),
    )
    return deduped
