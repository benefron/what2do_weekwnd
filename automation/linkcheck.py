"""Link health for data/places.json, with cheap automatic repair.

Places outlive their URLs: a commune reorganises its CMS and
`limburg.be/kiewit` 404s even though Domein Kiewit is very much still there.
So a dead link is never a reason to drop a place — it is a reason to stop
offering a broken link and, where possible, to fix it.

Three outcomes per place:

  ok       the URL answered                        -> link_ok: true
  repaired the URL 404'd but its site root answers -> website rewritten, link_ok: true
  broken   still failing after the repair attempt  -> link_ok: false, URL kept

Network-level failures (timeouts, refused connections, TLS handshakes) are
deliberately NOT treated as broken: a naive checker reports a third of the file
as dead simply because Belgian tourism sites rate-limit and fingerprint. Only a
definitive 4xx from the server counts. Everything goes through
`sources.http_get`, which already retries a 403 through tls_client.
"""
import logging
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

import sources

log = logging.getLogger(__name__)

# Statuses that mean "this specific page is gone" rather than "you are a bot".
_GONE = {404, 410}


def _root_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return root if root.rstrip("/") != url.rstrip("/") else None


def _probe(url: str) -> tuple[str, int | None]:
    """-> (outcome, status). outcome is 'ok' | 'gone' | 'unknown'."""
    try:
        sources.http_get(url)
        return "ok", 200
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        return ("gone" if code in _GONE else "unknown"), code
    except Exception:  # noqa: BLE001  — timeout, DNS, TLS, tls_client failure
        return "unknown", None


def check_places(places: list[dict], throttle: float = 0.4) -> dict:
    stats = {"ok": 0, "repaired": 0, "broken": 0, "unknown": 0, "skipped": 0}
    repairs: list[tuple[str, str, str]] = []
    broken: list[tuple[str, str]] = []

    for p in places:
        url = (p.get("website") or "").strip()
        if not url:
            p["link_ok"] = False
            stats["skipped"] += 1
            continue

        outcome, code = _probe(url)
        time.sleep(throttle)

        if outcome == "ok":
            p["link_ok"] = True
            stats["ok"] += 1
            continue

        if outcome == "unknown":
            # Bot-blocked or flaky, not proven dead — leave the link in place.
            p["link_ok"] = True
            stats["unknown"] += 1
            continue

        root = _root_of(url)
        if root:
            root_outcome, _ = _probe(root)
            time.sleep(throttle)
            if root_outcome == "ok":
                repairs.append((p.get("name", p["id"]), url, root))
                p["website"] = root
                p["link_ok"] = True
                stats["repaired"] += 1
                continue

        p["link_ok"] = False
        broken.append((p.get("name", p["id"]), url))
        stats["broken"] += 1

    for name, old, new in repairs:
        log.info("linkcheck repaired %-38s %s -> %s", name[:38], old, new)
    for name, url in broken:
        log.warning("linkcheck BROKEN   %-38s %s", name[:38], url)
    log.info("linkcheck: %s", stats)
    return stats
