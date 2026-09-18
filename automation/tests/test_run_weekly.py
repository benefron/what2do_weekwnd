"""Stage checkpointing and the stale-lock takeover — the "flag for what was
done" that lets watchdog.py (and a human) tell where a crashed run died
without grepping its log.
"""
import json
import time

import pytest

import config
import run_weekly


@pytest.fixture(autouse=True)
def _state_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUN_LOCK", tmp_path / "run.lock")
    monkeypatch.setattr(config, "RUN_PROGRESS", tmp_path / "run_progress.json")
    monkeypatch.setattr(config, "LAST_RUN_STATE", tmp_path / "last_run.json")
    monkeypatch.setattr(config, "LOCK_STALE_SECONDS", 100)


def test_write_then_read_progress_round_trips():
    run_weekly._write_progress("2026-09-18_0730", "enrich", activity_count=42)
    progress = run_weekly._read_progress()
    assert progress["run_id"] == "2026-09-18_0730"
    assert progress["stage"] == "enrich"
    assert progress["activity_count"] == 42
    assert "at" in progress


def test_read_progress_with_no_file_is_empty_not_an_error():
    assert run_weekly._read_progress() == {}


def test_read_progress_survives_a_corrupt_file():
    config.RUN_PROGRESS.write_text("{not json")
    assert run_weekly._read_progress() == {}


def test_acquire_lock_succeeds_when_none_exists():
    assert run_weekly._acquire_lock() is True
    assert config.RUN_LOCK.exists()


def test_acquire_lock_refuses_a_fresh_lock():
    config.RUN_LOCK.write_text(str(time.time()))
    assert run_weekly._acquire_lock() is False


def test_acquire_lock_takes_over_a_stale_lock_and_logs_the_dead_stage(caplog):
    old = time.time() - config.LOCK_STALE_SECONDS - 1
    config.RUN_LOCK.write_text(str(old))
    import os
    os.utime(config.RUN_LOCK, (old, old))
    run_weekly._write_progress("2026-09-14_0744", "enrich", activity_count=90)

    with caplog.at_level("WARNING"):
        assert run_weekly._acquire_lock() is True

    assert any("2026-09-14_0744" in r.message and "enrich" in r.message for r in caplog.records)


def test_acquire_lock_takes_over_a_stale_lock_with_no_progress_file():
    old = time.time() - config.LOCK_STALE_SECONDS - 1
    config.RUN_LOCK.write_text(str(old))
    import os
    os.utime(config.RUN_LOCK, (old, old))
    assert run_weekly._acquire_lock() is True
