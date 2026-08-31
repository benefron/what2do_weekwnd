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
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import httpx
import tls_client

import config

log = logging.getLogger(__name__)

_EVENT_LINK_RE = re.compile(r"/agenda/e/[a-z0-9-]+/([0-9a-fA-F-]{36})")


# ── URL identity ────────────────────────────────────────────────────────────
def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def activity_id(url: str) -> str:
    return hashlib.sha1(canonicalize_url(url).encode("utf-8")).hexdigest()[:10]


# ── HTTP ────────────────────────────────────────────────────────────────────
def _fetch_via_tls_client(url: str) -> bytes:
    session = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
    resp = session.get(url, headers=config.REQUEST_HEADERS, timeout_seconds=config.REQUEST_TIMEOUT_SECONDS)
    if resp.status_code >= 400:
        raise ValueError(f"tls_client fallback got HTTP {resp.status_code} for {url}")
    return resp.content


def http_get(url: str) -> bytes:
    try:
        resp = httpx.get(
            url,
            headers=config.REQUEST_HEADERS,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        log.info("url=%s got 403 via httpx, retrying via tls_client", url)
        return _fetch_via_tls_client(url)


# ── UiT agenda: list UUIDs, hydrate each ────────────────────────────────────
def list_agenda_uuids(list_url: str, max_pages: int) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    empty_streak = 0
    for page in range(max_pages):
        url = f"{list_url}?page={page}"
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

    for key, src in config.UIT_AGENDA_SOURCES.items():
        uuids = list_agenda_uuids(src["list_url"], config.UIT_AGENDA_MAX_PAGES)
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
