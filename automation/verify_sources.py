"""Ping every configured source and print status. Run before trusting the
pipeline unattended:  python -m automation.verify_sources  (or scripts/verify_sources.sh)
"""
import logging
import sys

import config
import sources

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    print("== RSS sources ==")
    for key, src in config.RSS_SOURCES.items():
        ok = False
        for feed_url in src.get("rss", []):
            try:
                items = sources._fetch_rss_feed(feed_url)
                print(f"  OK   {key:36s} {len(items):4d} items  {feed_url}")
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {key:36s} {feed_url}  ({exc})")
        if not ok and src.get("scrape_fallback_url"):
            try:
                evs = sources.extract_jsonld_events(sources.http_get(src["scrape_fallback_url"]))
                print(f"  ~fallback {key:31s} {len(evs):4d} json-ld events  {src['scrape_fallback_url']}")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL fallback {key:27s} {src['scrape_fallback_url']}  ({exc})")

    print("\n== venue scrapers (JSON-LD) ==")
    for key, src in config.SCRAPER_SOURCES.items():
        try:
            evs = sources.extract_jsonld_events(sources.http_get(src["url"]))
            status = "OK  " if evs else "EMPTY"
            print(f"  {status} {key:28s} {len(evs):4d} events  {src['url']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {key:28s} {src['url']}  ({exc})")

    print(f"\n== UiTdatabank API: {'ENABLED' if config.UITDATABANK_ENABLED else 'disabled (v1)'} ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
