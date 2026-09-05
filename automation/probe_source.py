"""Probe a candidate source from a machine that can actually reach it.

The dev environment this was written in blocks every Belgian host, so the
source definitions in config.py are structural guesses. Run this before
trusting one:

    python -m automation.probe_source url https://www.quefaire.be/region-de-bruxelles
    python -m automation.probe_source ods odwb_wallonie
    python -m automation.probe_source uit          # every UIT_AGENDA_SOURCES entry

It only reads. Nothing here writes to the repo.
"""
import json
import re
import sys

import config
import sources

_FEED_RE = re.compile(
    r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*href="([^"]+)"', re.I)
_LD_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)
_LINK_RE = re.compile(r'href="([^"#?]+\.(?:shtml|html|asp|php))"', re.I)


def probe_url(url: str, lang: str | None = None) -> int:
    print(f"== {url}")
    try:
        body = sources.http_get(url, lang).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL {exc}")
        return 1
    print(f"   OK  {len(body)} bytes")

    feeds = _FEED_RE.findall(body)
    print(f"   rss/atom alternate: {feeds or 'none advertised'}")

    ld = _LD_RE.findall(body)
    types = []
    for block in ld:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type"):
                types.append(node["@type"])
    print(f"   ld+json blocks: {len(ld)}  types: {sorted(set(map(str, types))) or 'none'}")

    uuids = set(sources._EVENT_LINK_RE.findall(body))
    print(f"   UiT /agenda/e/<slug>/<uuid> links: {len(uuids)}")

    shapes: dict[str, int] = {}
    for href in _LINK_RE.findall(body):
        shapes[re.sub(r"\d+", "<n>", href)] = shapes.get(re.sub(r"\d+", "<n>", href), 0) + 1
    top = sorted(shapes.items(), key=lambda kv: -kv[1])[:5]
    print(f"   detail-link shapes: {top or 'none'}")

    # does ?page=N actually paginate, or is every page the same?
    first = set(sources._EVENT_LINK_RE.findall(body)) or {h for h in _LINK_RE.findall(body)}
    try:
        p2 = sources.http_get(sources._with_page(url, 2), lang).decode("utf-8", "replace")
        second = set(sources._EVENT_LINK_RE.findall(p2)) or {h for h in _LINK_RE.findall(p2)}
        if not second:
            print("   ?page=2: no links found")
        elif second == first:
            print("   ?page=2: IDENTICAL to page 1 — this URL does not paginate that way")
        else:
            print(f"   ?page=2: {len(second - first)} new links — pagination works")
    except Exception as exc:  # noqa: BLE001
        print(f"   ?page=2 failed: {exc}")
    return 0


def probe_ods(key: str) -> int:
    src = config.ODS_SOURCES.get(key)
    if not src:
        print(f"unknown ODS source {key!r}; known: {list(config.ODS_SOURCES)}")
        return 2
    base = src["base"].rstrip("/")
    url = f"{base}/api/explore/v2.1/catalog/datasets/{src['dataset']}/records?limit=3"
    print(f"== {url}")
    try:
        payload = json.loads(sources.http_get(url, src.get("default_language")))
    except Exception as exc:  # noqa: BLE001
        print(f"   FAIL {exc}")
        return 1
    rows = payload.get("results") or []
    print(f"   total_count: {payload.get('total_count')}   sample rows: {len(rows)}")
    if not rows:
        return 1
    print(f"   FIELD NAMES: {sorted(rows[0])}")
    print("   -- first row --")
    print("   " + json.dumps(rows[0], ensure_ascii=False, indent=2)[:1200].replace("\n", "\n   "))
    mapped = {
        "title": sources._ods_pick(rows[0], sources._ODS_TITLE),
        "start": sources._ods_pick(rows[0], sources._ODS_START),
        "city": sources._ods_pick(rows[0], sources._ODS_CITY),
        "url": sources._ods_pick(rows[0], sources._ODS_URL),
        "geo": sources._ods_geo(rows[0]),
    }
    print(f"   -- what the mapper extracts: {mapped}")
    missing = [k for k, v in mapped.items() if v in (None, (None, None))]
    if missing:
        print(f"   !! unmapped: {missing} — add the real names to sources._ODS_* tuples")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    mode = args[0]
    if mode == "url":
        return probe_url(args[1], args[2] if len(args) > 2 else None)
    if mode == "ods":
        return probe_ods(args[1]) if len(args) > 1 else max(
            probe_ods(k) for k in config.ODS_SOURCES)
    if mode == "uit":
        rc = 0
        for key, src in config.UIT_AGENDA_SOURCES.items():
            print(f"\n### {key}")
            rc |= probe_url(src["list_url"])
        return rc
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
