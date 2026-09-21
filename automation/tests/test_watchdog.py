"""watchdog._decide is pure — every case here is filesystem- and
subprocess-free, matching the "flag what was done, retry from there" ask that
motivated this module: a crashed run should be retried automatically, with
backoff, and eventually left alone for a human.
"""
from datetime import datetime, timedelta, timezone

import pytest

import config
import watchdog

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
FRESH_STATE = {"failed_run_id": None, "retries": 0, "last_retry_at": None}


@pytest.fixture(autouse=True)
def _small_thresholds(monkeypatch):
    """Real values (2h stale, 45min backoff, 3 retries) are unwieldy to write
    test timestamps against; the policy shape is what's under test, not the
    numbers, so shrink them."""
    monkeypatch.setattr(config, "LOCK_STALE_SECONDS", 100)
    monkeypatch.setattr(config, "WATCHDOG_RETRY_BACKOFF_SECONDS", 1000)
    monkeypatch.setattr(config, "WATCHDOG_MAX_RETRIES", 3)
    monkeypatch.setattr(watchdog, "WATCHDOG_NO_LOCK_SETTLE_SECONDS", 100)
    monkeypatch.setattr(watchdog, "WATCHDOG_EPISODE_RESET_SECONDS", 10000)


def test_no_lock_means_nothing_to_recover():
    should, reason, state = watchdog._decide(NOW, False, 0, {}, {})
    assert should is False
    assert "no lock" in reason
    assert state == {}


def test_fresh_lock_means_a_run_is_legitimately_in_progress():
    # age (50s) < LOCK_STALE_SECONDS (100s): a real run could just be between
    # heartbeats, not dead.
    should, reason, _ = watchdog._decide(NOW, True, 50, {"run_id": "r1", "stage": "enrich"}, {})
    assert should is False
    assert "legitimately in progress" in reason


def test_stale_lock_but_last_run_published_is_just_uncleaned_lock():
    should, reason, _ = watchdog._decide(NOW, True, 999, {"run_id": "r1", "stage": "published"}, {})
    assert should is False
    assert "published" in reason


def test_stale_lock_with_no_progress_file_has_nothing_to_recover():
    should, reason, _ = watchdog._decide(NOW, True, 999, {}, {})
    assert should is False
    assert "no progress" in reason


def test_stale_lock_mid_run_triggers_a_first_retry():
    progress = {"run_id": "2026-09-18_0730", "stage": "enrich"}
    should, reason, state = watchdog._decide(NOW, True, 999, progress, {})
    assert should is True
    assert "2026-09-18_0730" in reason and "enrich" in reason
    assert state == {"failed_run_id": "2026-09-18_0730", "retries": 1, "last_retry_at": NOW.isoformat()}


def test_retry_within_backoff_window_is_skipped():
    progress = {"run_id": "r1", "stage": "enrich"}
    prior = {"failed_run_id": "r1", "retries": 1, "last_retry_at": (NOW - timedelta(seconds=500)).isoformat()}
    should, reason, state = watchdog._decide(NOW, True, 999, progress, prior)
    assert should is False
    assert "within backoff" in reason
    assert state == prior  # unchanged — no attempt was made


def test_retry_after_backoff_window_elapses():
    progress = {"run_id": "r1", "stage": "enrich"}
    prior = {"failed_run_id": "r1", "retries": 1, "last_retry_at": (NOW - timedelta(seconds=1500)).isoformat()}
    should, reason, state = watchdog._decide(NOW, True, 999, progress, prior)
    assert should is True
    assert state["retries"] == 2


def test_gives_up_after_max_retries():
    progress = {"run_id": "r1", "stage": "enrich"}
    prior = {"failed_run_id": "r1", "retries": 3, "last_retry_at": (NOW - timedelta(seconds=5000)).isoformat()}
    should, reason, state = watchdog._decide(NOW, True, 999, progress, prior)
    assert should is False
    assert "giving up" in reason
    assert state == prior


def test_a_new_crash_after_an_old_one_resets_the_counter():
    # watchdog_state still remembers a previous run_id that gave up (retries=3);
    # this week's is a different run_id AND the last retry was long enough ago
    # (beyond WATCHDOG_EPISODE_RESET_SECONDS, with nothing to say it's a
    # continuation) that it's treated as a fresh, unrelated failure — see
    # test_failed_retry_with_fresh_run_id_does_not_reset_the_retry_counter for
    # the neighbour where a fresh run_id shows up *within* the same episode
    # and must NOT get a clean slate.
    progress = {"run_id": "2026-09-25_0730", "stage": "fetch"}
    prior = {
        "failed_run_id": "2026-09-18_0730",
        "retries": 3,
        "last_retry_at": (NOW - timedelta(seconds=20000)).isoformat(),
    }
    should, reason, state = watchdog._decide(NOW, True, 999, progress, prior)
    assert should is True
    assert state == {"failed_run_id": "2026-09-25_0730", "retries": 1, "last_retry_at": NOW.isoformat()}


# ── no-lock clean abort (bug: 2026-09-21 DNS-down abort left no lock and the
# watchdog was blind to it forever) ─────────────────────────────────────────

def test_clean_abort_with_no_lock_is_retried():
    # DNS was down after wake; run_weekly.py aborted with 0 raw records and
    # its `finally` unlinked the lock on the way out. No lock, but progress
    # says a run failed well outside the settle window and nothing since has
    # succeeded — this must be recoverable, or the feed silently goes stale.
    progress = {
        "run_id": "2026-09-21_0730",
        "stage": "failed",
        "reason": "DNS unreachable",
        "at": (NOW - timedelta(seconds=500)).isoformat(),
    }
    last_run = {"last_success_at": (NOW - timedelta(days=7)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, {}, last_run)
    assert should is True
    assert "2026-09-21_0730" in reason and "aborted" in reason
    assert state == {"failed_run_id": "2026-09-21_0730", "retries": 1, "last_retry_at": NOW.isoformat()}


def test_no_lock_progress_published_is_not_retried():
    # Known-good neighbour: no lock and the last run actually succeeded —
    # nothing to recover.
    progress = {"run_id": "r1", "stage": "published", "at": (NOW - timedelta(seconds=500)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, {}, {})
    assert should is False
    assert "no lock" in reason
    assert state == {}


def test_no_lock_clean_abort_within_settle_window_is_not_retried():
    # Known-good neighbour: the abort just happened (progress written,
    # nothing has actually gone stale yet) — a tick landing right then
    # shouldn't fire a retry mid-shutdown.
    progress = {
        "run_id": "2026-09-21_0730",
        "stage": "failed",
        "at": (NOW - timedelta(seconds=10)).isoformat(),
    }
    should, reason, state = watchdog._decide(NOW, False, 0, progress, {}, {})
    assert should is False
    assert "settle window" in reason
    assert state == {}


def test_no_lock_clean_abort_superseded_by_later_success_is_not_retried():
    # Known-good neighbour: a later run succeeded after this failed one was
    # recorded (e.g. a manual --force in between) — don't re-run stale work.
    progress = {
        "run_id": "2026-09-21_0730",
        "stage": "failed",
        "at": (NOW - timedelta(seconds=500)).isoformat(),
    }
    last_run = {"last_success_at": (NOW - timedelta(seconds=200)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, {}, last_run)
    assert should is False
    assert "already succeeded" in reason
    assert state == {}


def test_no_lock_clean_abort_respects_backoff():
    progress = {
        "run_id": "2026-09-21_0730",
        "stage": "failed",
        "at": (NOW - timedelta(seconds=5000)).isoformat(),
    }
    prior = {"failed_run_id": "2026-09-21_0730", "retries": 1, "last_retry_at": (NOW - timedelta(seconds=500)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, prior, {})
    assert should is False
    assert "within backoff" in reason
    assert state == prior


def test_no_lock_clean_abort_gives_up_after_max_retries():
    progress = {
        "run_id": "2026-09-21_0730",
        "stage": "failed",
        "at": (NOW - timedelta(seconds=5000)).isoformat(),
    }
    prior = {"failed_run_id": "2026-09-21_0730", "retries": 3, "last_retry_at": (NOW - timedelta(seconds=5000)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, prior, {})
    assert should is False
    assert "giving up" in reason
    assert state == prior


def test_no_lock_early_kill_with_started_stage_is_also_retried():
    # A run killed very early may only have gotten as far as writing
    # {"stage": "started"} before dying with no lock left behind (e.g. killed
    # before the lock file was even created) — any non-"published" stage with
    # a run_id and a timestamp is recoverable, not just "failed".
    progress = {
        "run_id": "2026-09-21_0730",
        "stage": "started",
        "at": (NOW - timedelta(seconds=5000)).isoformat(),
    }
    should, reason, state = watchdog._decide(NOW, False, 0, progress, {}, {})
    assert should is True
    assert "started" in reason


# ── retry counter is per FAILURE EPISODE, not per run_id (bug: every retry
# spawns run_weekly.py --force under a brand-new run_id, so counting per
# run_id reset to 0 on every tick and WATCHDOG_MAX_RETRIES was unreachable
# for any failure — e.g. DNS still down — that outlived one retry) ─────────

def test_failed_retry_with_fresh_run_id_does_not_reset_the_retry_counter():
    # A prior retry (runA, attempt 1) itself failed under a new run_id
    # (runB) — same underlying problem, still unresolved (no success since,
    # not decayed) — so the counter must carry forward to attempt 2, not
    # reset to 1 forever.
    prior = {"failed_run_id": "runA", "retries": 1, "last_retry_at": (NOW - timedelta(seconds=1500)).isoformat()}
    progress = {"run_id": "runB", "stage": "failed", "at": (NOW - timedelta(seconds=200)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, prior, {})
    assert should is True
    assert state == {"failed_run_id": "runB", "retries": 2, "last_retry_at": NOW.isoformat()}


def test_failed_retry_eventually_gives_up_within_one_episode():
    # Carrying the counter across run_ids has to actually reach
    # WATCHDOG_MAX_RETRIES and stop — this is the whole point of the fix.
    prior = {"failed_run_id": "runB", "retries": 3, "last_retry_at": (NOW - timedelta(seconds=1500)).isoformat()}
    progress = {"run_id": "runC", "stage": "failed", "at": (NOW - timedelta(seconds=200)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, prior, {})
    assert should is False
    assert "giving up" in reason
    assert state == {"failed_run_id": "runC", "retries": 3, "last_retry_at": prior["last_retry_at"]}


def test_backoff_holds_across_run_ids_within_an_episode():
    # The backoff clock is also episode-scoped: a fresh run_id within backoff
    # of the episode's last retry must still wait, not retry immediately.
    prior = {"failed_run_id": "runA", "retries": 1, "last_retry_at": (NOW - timedelta(seconds=500)).isoformat()}
    progress = {"run_id": "runB", "stage": "failed", "at": (NOW - timedelta(seconds=200)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, prior, {})
    assert should is False
    assert "within backoff" in reason
    assert state == {"failed_run_id": "runB", "retries": 1, "last_retry_at": prior["last_retry_at"]}


def test_success_since_last_retry_starts_a_fresh_episode():
    # Known-good neighbour: a run actually succeeded after the last retry —
    # the problem is resolved, so a later, unrelated failure gets a clean
    # slate rather than inheriting a stale counter.
    prior = {"failed_run_id": "runA", "retries": 3, "last_retry_at": (NOW - timedelta(seconds=1000)).isoformat()}
    last_run = {"last_success_at": (NOW - timedelta(seconds=500)).isoformat()}
    progress = {"run_id": "runB", "stage": "failed", "at": (NOW - timedelta(seconds=150)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, prior, last_run)
    assert should is True
    assert state == {"failed_run_id": "runB", "retries": 1, "last_retry_at": NOW.isoformat()}


def test_old_episode_decays_so_next_weeks_failure_gets_a_fresh_budget():
    # Known-good neighbour: last week's episode gave up (retries maxed) and
    # nothing has succeeded since, but it's been long enough
    # (WATCHDOG_EPISODE_RESET_SECONDS) that this week's crash is treated as
    # its own thing rather than permanently stuck at "giving up".
    prior = {"failed_run_id": "runA", "retries": 3, "last_retry_at": (NOW - timedelta(seconds=20000)).isoformat()}
    progress = {"run_id": "runB", "stage": "failed", "at": (NOW - timedelta(seconds=200)).isoformat()}
    should, reason, state = watchdog._decide(NOW, False, 0, progress, prior, {})
    assert should is True
    assert state == {"failed_run_id": "runB", "retries": 1, "last_retry_at": NOW.isoformat()}
