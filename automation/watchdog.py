"""Crash recovery for the weekly pipeline. Runs every 30 min via its own
LaunchAgent (com.benefron.weekwnd.watchdog) — separate from the Monday
07:30 schedule, because that one only fires once a week and a run that dies
mid-morning would otherwise sit broken until the following Monday.

Reads what run_weekly.py already writes — state/run.lock and
state/run_progress.json — and decides whether the last attempt is dead
(lock stale, i.e. no heartbeat for LOCK_STALE_SECONDS — see
enrich._touch_lock for why a merely-slow run doesn't look dead) and never
reached "published". If so, it retries with backoff (WATCHDOG_RETRY_BACKOFF_
SECONDS between attempts, giving up after WATCHDOG_MAX_RETRIES so a genuinely
broken run fails loud instead of retrying forever) by launching
`run_weekly.py --force` the same way the LaunchAgent does — caffeinated, so
the retry doesn't die the same way the 2026-09-14 run did (killed by a system
sleep the LaunchAgent itself did nothing to prevent).

_decide() is the whole policy and is pure — no filesystem, no subprocess — so
it's tested directly; main() is the thin I/O shell around it.
"""
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone

import config

log = logging.getLogger("watchdog")


def _setup_logging() -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.LOGS_DIR / "watchdog.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _read_json(path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _decide(now: datetime, lock_exists: bool, lock_age_seconds: float, progress: dict, watchdog_state: dict):
    """Returns (should_retry, reason, next_watchdog_state).

    `watchdog_state` and the returned `next_watchdog_state` are
    {"failed_run_id", "retries", "last_retry_at"} — the caller persists
    whatever comes back, whether or not should_retry is True, so a stale
    per-run_id state from a run that has since succeeded doesn't linger
    (it's simply never matched again, since the next crash gets a fresh
    run_id).
    """
    if not lock_exists:
        return False, "no lock — nothing running or crashed", watchdog_state
    if lock_age_seconds < config.LOCK_STALE_SECONDS:
        return False, f"lock is fresh (age={lock_age_seconds:.0f}s) — a run is legitimately in progress", watchdog_state

    stage = progress.get("stage")
    run_id = progress.get("run_id")
    if stage == "published":
        return False, "last run published — lock just hasn't been cleaned up yet", watchdog_state
    if not run_id:
        return False, "stale lock but no progress recorded — nothing to recover from", watchdog_state

    if watchdog_state.get("failed_run_id") != run_id:
        watchdog_state = {"failed_run_id": run_id, "retries": 0, "last_retry_at": None}

    if watchdog_state["retries"] >= config.WATCHDOG_MAX_RETRIES:
        return (
            False,
            f"run {run_id} already retried {watchdog_state['retries']}x — giving up, needs a human",
            watchdog_state,
        )

    if watchdog_state["last_retry_at"]:
        elapsed = (now - datetime.fromisoformat(watchdog_state["last_retry_at"])).total_seconds()
        if elapsed < config.WATCHDOG_RETRY_BACKOFF_SECONDS:
            return False, f"retried {run_id} {elapsed:.0f}s ago — within backoff", watchdog_state

    next_state = {
        **watchdog_state,
        "retries": watchdog_state["retries"] + 1,
        "last_retry_at": now.isoformat(),
    }
    reason = (
        f"run {run_id} died at stage {stage!r} — retrying "
        f"(attempt {next_state['retries']}/{config.WATCHDOG_MAX_RETRIES})"
    )
    return True, reason, next_state


def _spawn_retry() -> None:
    # The dead process still holds RUN_LOCK (it never reached its `finally`
    # unlink); run_weekly.py's own stale-lock check takes it over and logs
    # which stage died, so watchdog doesn't need to touch the lock itself.
    python3 = config.AUTOMATION_DIR / ".venv" / "bin" / "python3"
    script = config.AUTOMATION_DIR / "run_weekly.py"
    log_path = config.LOGS_DIR / "watchdog_retry.log"
    with open(log_path, "a") as log_file:
        subprocess.Popen(
            ["/usr/bin/caffeinate", "-ims", str(python3), str(script), "--force"],
            cwd=config.AUTOMATION_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def main() -> int:
    _setup_logging()
    now = datetime.now(timezone.utc)
    lock_exists = config.RUN_LOCK.exists()
    lock_age = time.time() - config.RUN_LOCK.stat().st_mtime if lock_exists else 0.0
    progress = _read_json(config.RUN_PROGRESS)
    watchdog_state = _read_json(config.WATCHDOG_STATE)

    should_retry, reason, next_state = _decide(now, lock_exists, lock_age, progress, watchdog_state)
    log.info(reason)

    if next_state != watchdog_state:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        config.WATCHDOG_STATE.write_text(json.dumps(next_state, ensure_ascii=False, indent=2))

    if should_retry:
        _spawn_retry()
        log.info("spawned retry")

    return 0


if __name__ == "__main__":
    sys.exit(main())
