"""Fetch layer: UiTinVlaanderen agenda RSS + venue JSON-LD scrapers + manual
overrides. Produces a flat list of raw records tagged with `_kind` so
normalize.py can dispatch. A dead source logs and is skipped — it never
aborts the run.
"""
import hashlib
import json
import logging
import re
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import httpx
import tls_client
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

_IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_JSONLD_EVENT_TYPES = {
    "Event", "Festival", "ExhibitionEvent", "ScreeningEvent", "TheaterEvent",
    "ChildrensEvent", "SocialEvent", "MusicEvent", "EducationEvent",
    "SportsEvent", "VisualArtsEvent", "DanceEvent", "ComedyEvent", "FoodEvent",
}


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


# ── RSS ─────────────────────────────────────────────────────────────────────
def _extract_rss_image(entry: dict) -> str | None:
    for m in entry.get("media_content", []) or []:
        if m.get("url"):
            return m["url"]
    for enc in entry.get("enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href and enc.get("type", "").startswith("image"):
            return href
    html = entry.get("summary") or entry.get("description") or ""
    match = _IMG_TAG_RE.search(html)
    return match.group(1) if match else None


def _fetch_rss_feed(feed_url: str) -> list[dict]:
    parsed = feedparser.parse(http_get(feed_url))
    if not parsed.entries:
        raise ValueError(f"no entries in feed: {feed_url}")
    items = []
    for entry in parsed.entries:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            continue
        published_at = None
        if entry.get("published_parsed"):
            from datetime import datetime, timezone
            published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        items.append({
            "title": title.strip(),
            "url": url.strip(),
            "published_at": published_at,
            "summary_html": entry.get("summary") or entry.get("description") or "",
            "image_url": _extract_rss_image(entry),
        })
    return items


# ── JSON-LD ─────────────────────────────────────────────────────────────────
def _walk_jsonld(node, out: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, out)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _walk_jsonld(node["@graph"], out)
    node_type = node.get("@type")
    types = {node_type} if isinstance(node_type, str) else set(node_type or [])
    if types & _JSONLD_EVENT_TYPES:
        out.append(node)


def extract_jsonld_events(html: bytes) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        _walk_jsonld(data, events)
    return events


def _fetch_scraper(source_key: str, url: str) -> list[dict]:
    events = extract_jsonld_events(http_get(url))
    log.info("scraper=%s url=%s jsonld_events=%d", source_key, url, len(events))
    return events


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

    # UiTdatabank Search API — disabled in v1. When enabled it becomes the
    # primary structured source; RSS + scrapers stay as fallback.
    if config.UITDATABANK_ENABLED:
        try:
            import uitdatabank_client
            events = uitdatabank_client.fetch_events()
            for ev in events:
                ev["_source"] = "uitdatabank"
                ev["_source_label"] = "UiTdatabank"
                ev["_kind"] = "jsonld"
            raw.extend(events)
            fetched.append("uitdatabank")
        except Exception as exc:  # noqa: BLE001
            log.warning("uitdatabank fetch failed: %s", exc)
            failed.append("uitdatabank")

    for key, src in config.RSS_SOURCES.items():
        items: list[dict] = []
        for feed_url in src.get("rss", []):
            try:
                items = _fetch_rss_feed(feed_url)
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("rss source=%s feed=%s failed: %s", key, feed_url, exc)
        if not items and src.get("scrape_fallback_url"):
            try:
                items = extract_jsonld_events(http_get(src["scrape_fallback_url"]))
                for ev in items:
                    ev["_kind"] = "jsonld"
            except Exception as exc:  # noqa: BLE001
                log.warning("rss source=%s scrape fallback failed: %s", key, exc)
                items = []
        if not items:
            failed.append(key)
            continue
        fetched.append(key)
        for item in items:
            item.setdefault("_kind", "rss")
            item["_source"] = key
            item["_source_label"] = src["label"]
            raw.append(item)

    for key, src in config.SCRAPER_SOURCES.items():
        try:
            events = _fetch_scraper(key, src["url"])
        except Exception as exc:  # noqa: BLE001
            log.warning("scraper source=%s failed: %s", key, exc)
            failed.append(key)
            continue
        if not events:
            failed.append(key)
            continue
        fetched.append(key)
        for ev in events:
            ev["_kind"] = "jsonld"
            ev["_source"] = key
            ev["_source_label"] = src["label"]
            raw.append(ev)

    overrides = load_manual_overrides()
    for ov in overrides:
        ov["_kind"] = "manual"
        ov["_source"] = ov.get("source", "manual")
        ov["_source_label"] = ov.get("source_label", "Handmatig")
        raw.append(ov)
    if overrides:
        fetched.append("manual_overrides")

    log.info("fetch_all: %d raw records, %d sources ok, %d failed", len(raw), len(fetched), len(failed))
    return {"raw": raw, "sources_fetched": fetched, "sources_failed": failed}


def _resolve_image(_):  # kept for symmetry / future use
    return None
