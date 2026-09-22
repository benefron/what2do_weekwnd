"""Weekly orchestrator. Runs Monday morning via launchd (see scripts/).

    fetch → normalize/dedupe → geocode/distance → Claude enrichment → publish

Guards (from israel-news-digest/run_daily.py): file lock, a ~20h idempotency
window so a Tuesday wake-catchup doesn't re-run, and an abort-without-overwrite
when zero activities come back.

Each stage also checkpoints to state/run_progress.json (_write_progress) —
not just for the record, but so watchdog.py (its own LaunchAgent, checks every
30 min) can tell a crashed run from a slow one and retry it with backoff
instead of leaving the feed stale until next Monday. A crash mid-run does not
lose the work that got done: enrich.py's per-batch cache write and geo.py's
geocode cache both survive a restart, so a retry mostly replays from cache
rather than re-fetching/re-paying for tokens.
"""
import argparse
import json
import logging
import socket
import sys
import time
from datetime import datetime, timezone

import config
import enrich
import geo
import normalize
import places
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


def _write_progress(run_id: str, stage: str, **extra) -> None:
    """Checkpoint after each stage: what a crashed run got to, without having
    to grep its log. watchdog.py reads this to decide whether — and where —
    to retry. `stage="published"` is the terminal, all-clear value."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "stage": stage, "at": datetime.now(timezone.utc).isoformat(), **extra}
    config.RUN_PROGRESS.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _read_progress() -> dict:
    if config.RUN_PROGRESS.exists():
        try:
            return json.loads(config.RUN_PROGRESS.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _clean_scratch() -> None:
    """Remove temporary batch files from the state directory after a successful run.

    enrich.py and verify logic create scratch files like enrich_batch_*.json,
    verify_batch_*.json, and smoke_batch_*.json during processing. These are
    useful for debugging a crashed run but should be cleaned up after success.
    Deletion failures are only logged, never fatal."""
    try:
        state_dir = config.STATE_DIR
        if not state_dir.exists():
            return
        for pattern in ("enrich_batch_*.json", "verify_batch_*.json", "smoke_batch_*.json"):
            for f in state_dir.glob(pattern):
                try:
                    f.unlink()
                except OSError as exc:
                    log.warning("failed to delete %s: %s", f, exc)
    except Exception as exc:
        log.warning("scratch cleanup failed: %s", exc)


def _wait_for_network(
    resolver=socket.gethostbyname,
    sleep=time.sleep,
    now=time.monotonic,
    host: str = None,
) -> bool:
    """Poll DNS before the fetch stage. A launchd-scheduled run can start
    right after the Mac wakes, before the network interface is actually up —
    on 2026-09-21 every source failed with "[Errno 8] nodename nor servname
    provided, or not known" and the run aborted for nothing. Retries
    `resolver(host)` every `config.NETWORK_POLL_SECONDS` until it succeeds or
    `config.NETWORK_WAIT_SECONDS` elapses. `resolver`/`sleep`/`now` are
    injectable so tests never really resolve DNS or sleep. Touches the run
    lock's mtime on each poll so watchdog.py sees a live run, not a stale one,
    while we wait."""
    host = host or config.NETWORK_CHECK_HOST
    start = now()
    waited_logged = False
    while True:
        try:
            resolver(host)
            if waited_logged:
                log.info("network is up (%s resolved)", host)
            return True
        except OSError:
            if not waited_logged:
                log.info("network not up yet (%s did not resolve), waiting up to %ds", host, config.NETWORK_WAIT_SECONDS)
                waited_logged = True
            if now() - start >= config.NETWORK_WAIT_SECONDS:
                log.error("network still not up after %ds, giving up", config.NETWORK_WAIT_SECONDS)
                return False
            try:
                if config.RUN_LOCK.exists():
                    config.RUN_LOCK.touch()
            except OSError:
                pass
            sleep(config.NETWORK_POLL_SECONDS)


def _acquire_lock() -> bool:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if config.RUN_LOCK.exists():
        age = time.time() - config.RUN_LOCK.stat().st_mtime
        if age < config.LOCK_STALE_SECONDS:
            return False
        progress = _read_progress()
        log.warning(
            "stale lock (age=%.0fs) — previous run %s died at stage %r, taking over",
            age, progress.get("run_id", "?"), progress.get("stage", "unknown"),
        )
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
        _write_progress(run_id, "started")

        if not _wait_for_network():
            reason = f"network never came up within {config.NETWORK_WAIT_SECONDS}s"
            log.error("%s, aborting without overwriting latest.json", reason)
            _write_progress(run_id, "failed", reason=reason)
            return 1

        fetched = sources.fetch_all()
        if not fetched["raw"]:
            reason = "no raw records from any source"
            log.error("%s, aborting without overwriting latest.json", reason)
            _write_progress(run_id, "failed", reason=reason)
            return 1
        _write_progress(run_id, "fetch", raw_count=len(fetched["raw"]))

        activities = normalize.normalize_all(fetched["raw"], run_id)
        if not activities:
            reason = "0 activities after normalize"
            log.error("%s, aborting without overwriting latest.json", reason)
            _write_progress(run_id, "failed", reason=reason)
            return 1
        _write_progress(run_id, "normalize", activity_count=len(activities))

        geo.geocode_activities(activities)

        # distance prefilter — drop agenda events well outside the widest ring
        # before spending Claude tokens on them (curated sources are kept even
        # if ungeocoded).
        before = len(activities)
        activities = [
            a for a in activities
            if a.get("source") in ("manual", "claude_search")
            or a.get("distance_km") is None
            or a["distance_km"] <= config.MAX_DISTANCE_KM
        ]
        log.info("distance prefilter: %d -> %d (<= %d km)", before, len(activities), config.MAX_DISTANCE_KM)
        _write_progress(run_id, "geocode", activity_count=len(activities))

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
        _write_progress(run_id, "enrich", activity_count=len(activities), degraded=degraded)

        # drop adult-only films / courses / nightlife — keep only things to do
        # with the kids plus big-name concerts & shows (family_relevant).
        if not args.no_enrich:
            before = len(activities)
            activities = [a for a in activities if a.get("family_relevant", True)]
            log.info("family_relevant filter: %d -> %d", before, len(activities))

        # merge in the permanent guide (data/places.json) verbatim — never
        # fetched or enriched by the weekly run.
        activities += places.load_places_as_activities(run_id)
        _write_progress(run_id, "merge_places", activity_count=len(activities))

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

        _clean_scratch()

        _write_progress(run_id, "published", activity_count=len(activities))
        log.info("run complete for %s", run_id)
        return 0
    except Exception as exc:
        log.exception("run failed with an unexpected exception")
        _write_progress(run_id, "failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        config.RUN_LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
