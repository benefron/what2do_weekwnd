"""Ping every configured source and print status. Run before trusting the
pipeline unattended:  python -m automation.verify_sources  (or scripts/verify_sources.sh)
"""
import logging
import sys

import config
import sources

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    print("== UiT agenda listing pages ==")
    for key, src in config.UIT_AGENDA_SOURCES.items():
        try:
            uuids = sources.list_agenda_uuids(src["list_url"], min(3, config.UIT_AGENDA_MAX_PAGES))
            print(f"  {'OK  ' if uuids else 'EMPTY'} {key:16s} {len(uuids):4d} uuids (first 3 pages)  {src['list_url']}")
            if uuids:
                node = sources.hydrate_event(uuids[0])
                ok = bool(node and node.get("name"))
                print(f"       hydrate {uuids[0]}: {'OK' if ok else 'FAIL'}"
                      + (f"  \"{list((node.get('name') or {}).values())[0]}\"" if ok else ""))
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {key:16s} {src['list_url']}  ({exc})")

    print(f"\n== venue scrapers: {len(config.SCRAPER_SOURCES)} configured ==")
    print(f"== UiTdatabank Search API: {'ENABLED' if config.UITDATABANK_ENABLED else 'disabled (v1 uses the free read endpoint)'} ==")
    print(f"== manual overrides: {len(sources.load_manual_overrides())} activities ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
