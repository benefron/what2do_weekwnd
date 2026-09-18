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
    # this week's is a different run_id and deserves its own fresh attempts.
    progress = {"run_id": "2026-09-25_0730", "stage": "fetch"}
    prior = {"failed_run_id": "2026-09-18_0730", "retries": 3, "last_retry_at": NOW.isoformat()}
    should, reason, state = watchdog._decide(NOW, True, 999, progress, prior)
    assert should is True
    assert state == {"failed_run_id": "2026-09-25_0730", "retries": 1, "last_retry_at": NOW.isoformat()}
