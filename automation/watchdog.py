"""Crash recovery for the weekly pipeline. Runs every 30 min via its own
LaunchAgent (com.benefron.weekwnd.watchdog) — separate from the Monday
07:30 schedule, because that one only fires once a week and a run that dies
mid-morning would otherwise sit broken until the following Monday.

Reads what run_weekly.py already writes — state/run.lock, state/
run_progress.json and state/last_run.json — and decides whether the last
attempt needs retrying. Two shapes of failure both land here:

- **Hard kill**: the process died without reaching its `finally`, so
  RUN_LOCK is still on disk. Dead once it's stale (no heartbeat for
  LOCK_STALE_SECONDS — see enrich._touch_lock for why a merely-slow run
  doesn't look dead) and progress never reached "published".
- **Clean abort**: run_weekly.py unlinks RUN_LOCK in a `finally` on *every*
  exit path, including a deliberate `sys.exit(1)` (2026-09-21: DNS still
  down right after wake, 0 raw records fetched → abort). That leaves no
  lock at all, so without this case the watchdog was blind to it forever —
  the feed just went stale silently. It's recoverable the same way once
  run_progress.json's `stage` isn't "published", is recent enough to be
  this failure and not some ancient one (WATCHDOG_NO_LOCK_SETTLE_SECONDS)
  and not already superseded by a later successful run (last_run.json's
  `last_success_at`).

Either way, once something is recovered, it retries with backoff
(WATCHDOG_RETRY_BACKOFF_SECONDS between attempts, giving up after
WATCHDOG_MAX_RETRIES so a genuinely broken run fails loud instead of
retrying forever) by launching `run_weekly.py --force` the same way the
LaunchAgent does — caffeinated, so the retry doesn't die the same way the
2026-09-14 run did (killed by a system sleep the LaunchAgent itself did
nothing to prevent). The retry counter and backoff are tracked **per failure
episode, not per run_id**: every retry spawns a brand-new `run_weekly.py`
process with its own fresh run_id, so if the underlying cause (e.g. DNS still
down) outlives the retry, counting per run_id would reset to 0 on every tick
and WATCHDOG_MAX_RETRIES would never be reachable. An episode carries its
counter and last-retry timestamp forward across run_ids until it's resolved
— either a run has actually succeeded since the last retry, or the last
retry is old enough (WATCHDOG_EPISODE_RESET_SECONDS) that it's treated as a
new, unrelated failure (so next Monday's crash still gets a fresh budget
even if last week's episode ended in "giving up" with no success in
between).

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

# How stale a no-lock run_progress.json entry has to be before a clean abort
# is treated as something to retry. Guards against a watchdog tick that lands
# mid-shutdown (progress just written, lock not yet unlinked) being mistaken
# for a crash. Kept as a module constant rather than a config.py entry (this
# file's own turf in the diagnostic-2026-09 split); `getattr` still lets
# config override it without this module needing config to define it.
WATCHDOG_NO_LOCK_SETTLE_SECONDS = getattr(config, "WATCHDOG_NO_LOCK_SETTLE_SECONDS", 60 * 60)

# How old last_run.json's last_success_at can get before main() shouts about
# it regardless of whether there's anything actionable to retry — a feed that
# has been silently stale for over a week is worse than any single crash.
STALE_FEED_WARNING_DAYS = getattr(config, "STALE_FEED_WARNING_DAYS", 8)

# How long a failure episode's retry counter/backoff survive across run_ids
# with no intervening success. Below this, a new run_id failing is still
# "the same problem, still unresolved" and inherits the counter; above it,
# it's treated as an unrelated new failure (e.g. next Monday) and starts at 0.
WATCHDOG_EPISODE_RESET_SECONDS = getattr(config, "WATCHDOG_EPISODE_RESET_SECONDS", 24 * 60 * 60)


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


def _episode_over(now: datetime, watchdog_state: dict, last_run: dict) -> bool:
    """True when the failure episode `watchdog_state` describes should be
    considered resolved/expired, so a new run_id starts a fresh counter
    instead of inheriting it. Two ways out: something has actually
    succeeded since the last retry, or the last retry is old enough that
    this is obviously a different, later failure (next week's crash, say)
    rather than the same one still limping along.
    """
    last_retry_at = watchdog_state.get("last_retry_at")
    if not last_retry_at:
        return True  # nothing retried yet under this state — no episode to continue

    last_retry_dt = datetime.fromisoformat(last_retry_at)

    last_success_at = last_run.get("last_success_at")
    if last_success_at:
        try:
            if datetime.fromisoformat(last_success_at) > last_retry_dt:
                return True  # a run has succeeded since we last retried — problem's gone
        except ValueError:
            pass  # unparseable last_success_at shouldn't end an episode we can't confirm ended

    return (now - last_retry_dt).total_seconds() > WATCHDOG_EPISODE_RESET_SECONDS


def _retry_decision(now: datetime, run_id: str, stage, watchdog_state: dict, last_run: dict, verb: str):
    """Shared tail for both the stale-lock and no-lock-clean-abort paths once
    each has established "there's a dead/failed run_id worth looking at":
    the retry counter, backoff window and give-up threshold, tracked **per
    failure episode** (see module docstring) rather than per run_id — every
    retry spawns a new process with its own run_id, so resetting on run_id
    change would make WATCHDOG_MAX_RETRIES unreachable for any failure that
    outlives one retry. `verb` only changes the human-readable reason
    ("died at stage" vs "aborted at stage").

    Returns (should_retry, reason, next_watchdog_state) — see `_decide` for
    what callers do with the state half.
    """
    if watchdog_state.get("failed_run_id") != run_id:
        if _episode_over(now, watchdog_state, last_run):
            watchdog_state = {"failed_run_id": run_id, "retries": 0, "last_retry_at": None}
        else:
            # Same episode, new run_id (this retry's own spawn failed too) —
            # carry the counter and backoff clock forward.
            watchdog_state = {**watchdog_state, "failed_run_id": run_id}

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
        f"run {run_id} {verb} at stage {stage!r} — retrying "
        f"(attempt {next_state['retries']}/{config.WATCHDOG_MAX_RETRIES})"
    )
    return True, reason, next_state


def _decide(
    now: datetime,
    lock_exists: bool,
    lock_age_seconds: float,
    progress: dict,
    watchdog_state: dict,
    last_run: dict = None,
):
    """Returns (should_retry, reason, next_watchdog_state).

    `watchdog_state` and the returned `next_watchdog_state` are
    {"failed_run_id", "retries", "last_retry_at"} — the caller persists
    whatever comes back, whether or not should_retry is True, so a stale
    per-run_id state from a run that has since succeeded doesn't linger
    (it's simply never matched again, since the next crash gets a fresh
    run_id).

    `last_run` is state/last_run.json's contents (or {}/None) —
    {"last_success_at": "<iso utc>", ...}. On the no-lock path it also makes
    sure a clean abort isn't retried after a later run has already
    superseded it; on both paths it feeds `_episode_over` (via
    `_retry_decision`), which decides whether a new run_id continues the
    same failure episode or starts a fresh one.
    """
    last_run = last_run or {}

    if not lock_exists:
        run_id = progress.get("run_id")
        stage = progress.get("stage")
        at = progress.get("at")
        if stage == "published" or not run_id or not at:
            return False, "no lock — nothing running or crashed", watchdog_state
        try:
            at_dt = datetime.fromisoformat(at)
        except ValueError:
            return False, "no lock — nothing running or crashed", watchdog_state

        age = (now - at_dt).total_seconds()
        if age < WATCHDOG_NO_LOCK_SETTLE_SECONDS:
            return (
                False,
                f"no lock but run {run_id} aborted only {age:.0f}s ago — within settle window",
                watchdog_state,
            )

        last_success_at = last_run.get("last_success_at")
        if last_success_at:
            try:
                if datetime.fromisoformat(last_success_at) >= at_dt:
                    return False, f"no lock but a later run already succeeded (run {run_id} is stale)", watchdog_state
            except ValueError:
                pass  # unparseable last_success_at shouldn't block a legitimate retry

        return _retry_decision(now, run_id, stage, watchdog_state, last_run, verb="aborted")

    if lock_age_seconds < config.LOCK_STALE_SECONDS:
        return False, f"lock is fresh (age={lock_age_seconds:.0f}s) — a run is legitimately in progress", watchdog_state

    stage = progress.get("stage")
    run_id = progress.get("run_id")
    if stage == "published":
        return False, "last run published — lock just hasn't been cleaned up yet", watchdog_state
    if not run_id:
        return False, "stale lock but no progress recorded — nothing to recover from", watchdog_state

    return _retry_decision(now, run_id, stage, watchdog_state, last_run, verb="died")


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
    last_run = _read_json(config.LAST_RUN_STATE)

    last_success_at = last_run.get("last_success_at")
    if last_success_at:
        try:
            age_days = (now - datetime.fromisoformat(last_success_at)).total_seconds() / 86400
            if age_days > STALE_FEED_WARNING_DAYS:
                log.warning(f"feed is stale — last successful run was {age_days:.1f} days ago ({last_success_at})")
        except ValueError:
            log.warning(f"last_run.json has an unparseable last_success_at: {last_success_at!r}")

    should_retry, reason, next_state = _decide(now, lock_exists, lock_age, progress, watchdog_state, last_run)
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
