"""Fetch layer.

v1 path: scrape the server-rendered UiT agenda listing pages for event UUIDs,
then hydrate each via the public UiTdatabank read endpoint
(https://io.uitdatabank.be/events/<uuid>) which returns full JSON-LD with no
auth. Plus manual overrides from data/manual_overrides.json.

Produces a flat list of raw records tagged with `_kind` so normalize.py can
dispatch. A dead source logs and is skipped — it never aborts the run.
"""
import hashlib
import json
import logging
import re
import time
from datetime import date, datetime
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import httpx
import tls_client

import config

log = logging.getLogger(__name__)

# UiTdatabank normally uses RFC-4122 UUIDs (36 chars), but publiq still emits
# legacy CDBIDs in an 8-4-4-16 uppercase shape (35 chars) on some listings. The
# exact-36 pattern silently dropped those; widen the length while keeping the
# surrounding /agenda/e/<slug>/ path as the anchor so it can't over-match.
_EVENT_LINK_RE = re.compile(r"/agenda/e/[a-z0-9-]+/([0-9a-fA-F-]{32,40})")


# ── URL identity ────────────────────────────────────────────────────────────
def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def activity_id(url: str) -> str:
    return hashlib.sha1(canonicalize_url(url).encode("utf-8")).hexdigest()[:10]


# ── HTTP ────────────────────────────────────────────────────────────────────
def _headers(lang: str | None = None) -> dict:
    """Per-source Accept-Language: the global default is nl-BE, which makes a
    francophone site serve its Dutch edition (or none)."""
    if not lang:
        return config.REQUEST_HEADERS
    return {**config.REQUEST_HEADERS, "Accept-Language": config.ACCEPT_LANGUAGE.get(lang, lang)}


def _fetch_via_tls_client(url: str, lang: str | None = None) -> bytes:
    session = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
    resp = session.get(url, headers=_headers(lang), timeout_seconds=config.REQUEST_TIMEOUT_SECONDS)
    if resp.status_code >= 400:
        raise ValueError(f"tls_client fallback got HTTP {resp.status_code} for {url}")
    return resp.content


def http_get(url: str, lang: str | None = None) -> bytes:
    try:
        resp = httpx.get(
            url,
            headers=_headers(lang),
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        log.info("url=%s got 403 via httpx, retrying via tls_client", url)
        return _fetch_via_tls_client(url, lang)


def _with_page(list_url: str, page: int) -> str:
    """Some listing URLs already carry a query string, so ?page= would be a
    second '?' and silently return page 1 forever."""
    return f"{list_url}{'&' if '?' in list_url else '?'}page={page}"


# ── UiT agenda: list UUIDs, hydrate each ────────────────────────────────────
def list_agenda_uuids(list_url: str, max_pages: int, first_page: int = 0) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    empty_streak = 0
    for page in range(first_page, first_page + max_pages):
        url = _with_page(list_url, page)
        try:
            html = http_get(url).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            log.warning("agenda list page %d failed: %s", page, exc)
            break
        page_uuids = [m.lower() for m in _EVENT_LINK_RE.findall(html)]
        new = [u for u in page_uuids if u not in seen_set]
        for u in new:
            seen_set.add(u)
            seen.append(u)
        if not new:
            empty_streak += 1
            if empty_streak >= 2:
                break
        else:
            empty_streak = 0
    return seen


def hydrate_event(uuid: str) -> dict | None:
    url = config.UIT_READ_ENDPOINT.format(uuid=uuid)
    try:
        data = json.loads(http_get(url))
    except Exception as exc:  # noqa: BLE001
        log.warning("hydrate %s failed: %s", uuid, exc)
        return None
    data["_uuid"] = uuid
    return data


def fetch_uit_agenda() -> tuple[list[dict], list[str], list[str]]:
    fetched: list[str] = []
    failed: list[str] = []
    records: list[dict] = []

    seen_uuids: set[str] = set()
    for key, src in config.UIT_AGENDA_SOURCES.items():
        uuids = list_agenda_uuids(
            src["list_url"],
            src.get("max_pages", config.UIT_AGENDA_MAX_PAGES),
            src.get("first_page", 0),
        )
        uuids = [u for u in uuids if u not in seen_uuids]
        seen_uuids.update(uuids)
        if not uuids:
            failed.append(key)
            continue
        log.info("agenda %s: %d event uuids", key, len(uuids))
        got = 0
        for uuid in uuids:
            node = hydrate_event(uuid)
            time.sleep(0.15)  # be polite to io.uitdatabank.be
            if not node:
                continue
            node["_kind"] = "uitdatabank"
            node["_source"] = key
            node["_source_label"] = src["label"]
            records.append(node)
            got += 1
        if got:
            fetched.append(key)
        else:
            failed.append(key)
    return records, fetched, failed


# ── OpenDataSoft portals (public v2.1 API, no auth) ─────────────────────────
# Field names vary per dataset, so each getter tries the names these portals
# actually use rather than hard-coding one schema we cannot verify from here.
_ODS_TITLE = ("title", "titre", "nom", "name", "intitule", "libelle")
_ODS_DESC = ("description", "descriptif", "description_courte", "resume", "contenu", "texte")
_ODS_START = ("start_datetime", "startdate", "date_debut", "datedebut", "start_date", "date_start", "debut", "date")
_ODS_END = ("end_datetime", "enddate", "date_fin", "datefin", "end_date", "date_end", "fin")
_ODS_URL = ("url", "event_url", "lien", "link", "website", "site_web", "url_evenement")
_ODS_CITY = ("commune", "ville", "city", "localite", "municipality", "address_city")
_ODS_VENUE = ("lieu", "venue", "place", "nom_lieu", "location", "adresse_lieu", "owner_diary_name")
_ODS_ADDRESS = ("adresse", "address", "rue", "street", "address_street_name")


def _ods_pick(rec: dict, names: tuple[str, ...]):
    for n in names:
        v = rec.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return v
    return None


def _ods_geo(rec: dict) -> tuple[float | None, float | None]:
    for key in ("geo_point_2d", "geo_point", "coordinates", "coordonnees", "location", "geolocalisation"):
        v = rec.get(key)
        if isinstance(v, dict) and v.get("lat") is not None:
            return float(v["lat"]), float(v.get("lon") or v.get("lng"))
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return float(v[0]), float(v[1])
    return None, None


def fetch_opendatasoft() -> tuple[list[dict], list[str], list[str]]:
    fetched: list[str] = []
    failed: list[str] = []
    records: list[dict] = []
    today = date.today().isoformat()

    for key, src in config.ODS_SOURCES.items():
        if not src.get("enabled", True):
            continue
        base = src["base"].rstrip("/")
        endpoint = f"{base}/api/explore/v2.1/catalog/datasets/{src['dataset']}/records"
        got: list[dict] = []
        try:
            for offset in range(0, src.get("max_records", 400), 100):
                url = f"{endpoint}?limit=100&offset={offset}"
                payload = json.loads(http_get(url, src.get("default_language")))
                rows = payload.get("results") or []
                got.extend(rows)
                if len(rows) < 100:
                    break
                time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            log.warning("opendatasoft %s failed: %s", key, exc)
            failed.append(key)
            continue

        kept = 0
        for rec in got:
            start = _ods_pick(rec, _ODS_START)
            if not start or str(start)[:10] < today:
                continue  # past, or no date we can place on a calendar
            lat, lng = _ods_geo(rec)
            records.append({
                "_kind": "ods",
                "_source": key,
                "_source_label": src["label"],
                "_default_language": src.get("default_language"),
                "title": _ods_pick(rec, _ODS_TITLE),
                "description": _ods_pick(rec, _ODS_DESC),
                "start": start,
                "end": _ods_pick(rec, _ODS_END),
                "url": _ods_pick(rec, _ODS_URL),
                "city": _ods_pick(rec, _ODS_CITY),
                "venue_name": _ods_pick(rec, _ODS_VENUE),
                "address": _ods_pick(rec, _ODS_ADDRESS),
                "lat": lat,
                "lng": lng,
            })
            kept += 1
        log.info("opendatasoft %s: %d rows -> %d upcoming", key, len(got), kept)
        (fetched if kept else failed).append(key)

    return records, fetched, failed


# ── RSS / Atom feeds ────────────────────────────────────────────────────────
def fetch_feeds() -> tuple[list[dict], list[str], list[str]]:
    fetched: list[str] = []
    failed: list[str] = []
    records: list[dict] = []

    for key, src in config.FEED_SOURCES.items():
        if not src.get("enabled", True):
            continue
        try:
            raw = http_get(src["url"], src.get("default_language"))
            parsed = feedparser.parse(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("feed %s failed: %s", key, exc)
            failed.append(key)
            continue

        entries = parsed.entries or []
        for e in entries:
            when = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            records.append({
                "_kind": "feed",
                "_source": key,
                "_source_label": src["label"],
                "_default_language": src.get("default_language"),
                "title": (getattr(e, "title", "") or "").strip(),
                "description": (getattr(e, "summary", "") or "").strip(),
                "start": datetime(*when[:6]).isoformat() if when else None,
                "url": getattr(e, "link", None),
            })
        log.info("feed %s: %d entries", key, len(entries))
        (fetched if entries else failed).append(key)

    return records, fetched, failed


# ── manual overrides ────────────────────────────────────────────────────────
def load_manual_overrides() -> list[dict]:
    if not config.MANUAL_OVERRIDES_JSON.exists():
        return []
    try:
        data = json.loads(config.MANUAL_OVERRIDES_JSON.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("manual_overrides.json is invalid JSON, ignoring: %s", exc)
        return []
    return data.get("activities", []) if isinstance(data, dict) else []


# ── orchestration ───────────────────────────────────────────────────────────
def fetch_all() -> dict:
    raw: list[dict] = []
    fetched: list[str] = []
    failed: list[str] = []

    if config.UITDATABANK_ENABLED:
        try:
            import uitdatabank_client
            for ev in uitdatabank_client.fetch_events():
                ev["_kind"] = "uitdatabank"
                ev["_source"] = "uitdatabank_api"
                ev["_source_label"] = "UiTdatabank"
                raw.append(ev)
            fetched.append("uitdatabank_api")
        except Exception as exc:  # noqa: BLE001
            log.warning("uitdatabank API fetch failed: %s", exc)
            failed.append("uitdatabank_api")

    uit_records, uit_ok, uit_bad = fetch_uit_agenda()
    raw.extend(uit_records)
    fetched.extend(uit_ok)
    failed.extend(uit_bad)

    try:
        ods_records, ods_fetched, ods_failed = fetch_opendatasoft()
        raw.extend(ods_records)
        fetched.extend(ods_fetched)
        failed.extend(ods_failed)
    except Exception as exc:  # noqa: BLE001
        log.warning("opendatasoft stage failed: %s", exc)
        failed.append("opendatasoft")

    try:
        feed_records, feed_fetched, feed_failed = fetch_feeds()
        raw.extend(feed_records)
        fetched.extend(feed_fetched)
        failed.extend(feed_failed)
    except Exception as exc:  # noqa: BLE001
        log.warning("feed stage failed: %s", exc)
        failed.append("feeds")

    if config.CLAUDE_SEARCH_ENABLED:
        try:
            import claude_search
            hits = claude_search.fetch_events()
            for h in hits:
                h["_kind"] = "claude_search"
                h["_source"] = "claude_search"
                h["_source_label"] = "Claude web search"
                raw.append(h)
            fetched.append("claude_search" if hits else "claude_search(empty)")
        except Exception as exc:  # noqa: BLE001
            log.warning("claude web search failed: %s", exc)
            failed.append("claude_search")

    overrides = load_manual_overrides()
    for ov in overrides:
        ov["_kind"] = "manual"
        ov["_source"] = ov.get("source", "manual")
        ov["_source_label"] = ov.get("source_label", "Handmatig")
        raw.append(ov)
    if overrides:
        fetched.append("manual_overrides")

    log.info("fetch_all: %d raw records, sources ok=%s failed=%s", len(raw), fetched, failed)
    return {"raw": raw, "sources_fetched": fetched, "sources_failed": failed}
