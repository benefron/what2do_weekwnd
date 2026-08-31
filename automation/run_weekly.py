"""Weekly orchestrator. Runs Monday morning via launchd (see scripts/).

    fetch → normalize/dedupe → geocode/distance → Claude enrichment → publish

Guards (from israel-news-digest/run_daily.py): file lock, a ~20h idempotency
window so a Tuesday wake-catchup doesn't re-run, and an abort-without-overwrite
when zero activities come back.
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone

import config
import enrich
import geo
import normalize
import publish
import sources

log = logging.getLogger("run_weekly")


def _setup_logging(date_str: str) -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.LOGS_DIR / f"run_{date_str}.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _load_state() -> dict:
    if config.LAST_RUN_STATE.exists():
        return json.loads(config.LAST_RUN_STATE.read_text())
    return {}


def _save_state(state: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.LAST_RUN_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _acquire_lock() -> bool:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if config.RUN_LOCK.exists():
        age = time.time() - config.RUN_LOCK.stat().st_mtime
        if age < config.LOCK_STALE_SECONDS:
            return False
        log.warning("stale lock (age=%.0fs), taking over", age)
    config.RUN_LOCK.write_text(str(time.time()))
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="ignore the once-a-week idempotency guard")
    parser.add_argument("--no-push", action="store_true", help="write data files but skip git commit/push")
    parser.add_argument("--no-enrich", action="store_true", help="skip the Claude enrichment step (debug)")
    args = parser.parse_args()

    now_local = datetime.now(timezone.utc).astimezone()
    today = now_local.date().isoformat()
    run_id = now_local.strftime("%Y-%m-%d_%H%M")
    _setup_logging(today)

    state = _load_state()
    last_success_at = state.get("last_success_at")
    if not args.force and last_success_at:
        elapsed_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last_success_at)).total_seconds() / 3600
        if elapsed_h < config.MIN_HOURS_BETWEEN_RUNS:
            log.info("last run was %.1fh ago (< %sh), exiting", elapsed_h, config.MIN_HOURS_BETWEEN_RUNS)
            return 0

    if not _acquire_lock():
        log.warning("another run appears to be in progress, exiting")
        return 0

    try:
        fetched = sources.fetch_all()
        if not fetched["raw"]:
            log.error("no raw records from any source, aborting without overwriting latest.json")
            return 1

        activities = normalize.normalize_all(fetched["raw"], run_id)
        if not activities:
            log.error("0 activities after normalize, aborting without overwriting latest.json")
            return 1

        geo.geocode_activities(activities)

        if args.no_enrich:
            for a in activities:
                a.setdefault("category", "other")
                a.setdefault("feature_tags", [])
                a.setdefault("confidence", "low")
                a.setdefault("is_recurring_class", False)
                a.setdefault("booking_required", None)
                a.setdefault("enrichment_model", "no-enrich")
            enrich_stats = {"skipped": True}
        else:
            enrich_stats = enrich.enrich_all(activities)

        degraded = bool(enrich_stats.get("skipped")) or all(
            a.get("enrichment_model") == "degraded" for a in activities
        )

        payload = publish.build_payload(
            activities, run_id, fetched["sources_fetched"], fetched["sources_failed"], degraded
        )
        publish.write_latest(payload, run_id)
        log.info("published %d activities (degraded=%s)", len(activities), degraded)

        if not args.no_push:
            publish.commit_and_push(run_id)

        state["last_success_at"] = datetime.now(timezone.utc).isoformat()
        state["last_run_id"] = run_id
        state["last_activity_count"] = len(activities)
        _save_state(state)
        log.info("run complete for %s", run_id)
        return 0
    finally:
        config.RUN_LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
