"""Ping every configured source and print status. Run before trusting the
pipeline unattended:  python -m automation.verify_sources  (or scripts/verify_sources.sh)
"""
import json
import logging
import sys

import feedparser

import config
import sources

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    print("== UiT agenda listing pages ==")
    for key, src in config.UIT_AGENDA_SOURCES.items():
        try:
            uuids = sources.list_agenda_uuids(src["list_url"], 3, src.get("first_page", 0))
            print(f"  {'OK  ' if uuids else 'EMPTY'} {key:16s} {len(uuids):4d} uuids (first 3 pages)  {src['list_url']}")
            if uuids:
                node = sources.hydrate_event(uuids[0])
                ok = bool(node and node.get("name"))
                print(f"       hydrate {uuids[0]}: {'OK' if ok else 'FAIL'}"
                      + (f"  \"{list((node.get('name') or {}).values())[0]}\"" if ok else ""))
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {key:16s} {src['list_url']}  ({exc})")

    print("\n== OpenDataSoft datasets ==")
    for key, src in config.ODS_SOURCES.items():
        if not src.get("enabled", True):
            print(f"  SKIP {key:20s} (disabled in config)")
            continue
        url = f"{src['base'].rstrip('/')}/api/explore/v2.1/catalog/datasets/{src['dataset']}/records?limit=1"
        try:
            payload = json.loads(sources.http_get(url, src.get("default_language")))
            rows = payload.get("results") or []
            total = payload.get("total_count")
            print(f"  {'OK  ' if rows else 'EMPTY'} {key:20s} total_count={total}  {src['dataset']}")
            if rows:
                mapped_title = sources._ods_pick(rows[0], sources._ODS_TITLE)
                mapped_start = sources._ods_pick(rows[0], sources._ODS_START)
                print(f"       maps to title={mapped_title!r} start={mapped_start!r}")
                if not (mapped_title and mapped_start):
                    print(f"       !! field mismatch — run: python -m automation.probe_source ods {key}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {key:20s} {url}  ({exc})")

    print(f"\n== RSS/Atom feeds: {len(config.FEED_SOURCES)} configured ==")
    for key, src in config.FEED_SOURCES.items():
        if not src.get("enabled", True):
            print(f"  SKIP {key:20s} (disabled in config)")
            continue
        try:
            parsed = feedparser.parse(sources.http_get(src["url"], src.get("default_language")))
            n = len(parsed.entries or [])
            title = (parsed.feed or {}).get("title", "?")
            print(f"  {'OK  ' if n else 'EMPTY'} {key:20s} {n:4d} entries  \"{title}\"")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {key:20s} {src['url']}  ({exc})")

    print(f"\n== venue scrapers: {len(config.SCRAPER_SOURCES)} configured ==")
    print(f"== UiTdatabank Search API: {'ENABLED' if config.UITDATABANK_ENABLED else 'disabled (v1 uses the free read endpoint)'} ==")
    print(f"== manual overrides: {len(sources.load_manual_overrides())} activities ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
