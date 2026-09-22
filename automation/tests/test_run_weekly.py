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


# ── _wait_for_network ────────────────────────────────────────────────────────
# 2026-09-21: a launchd-scheduled run started at 07:37 right after the Mac
# woke, before the network interface was up. Every source failed with
# "[Errno 8] nodename nor servname provided, or not known" and the run
# aborted. _wait_for_network polls DNS before the fetch stage instead of
# letting that happen silently.

def test_run_waits_for_dns_after_wake():
    """Resolver fails twice, then succeeds — should retry and return True,
    sleeping (not really) between polls."""
    calls = {"resolve": 0, "sleep": 0}

    def fake_resolver(host):
        calls["resolve"] += 1
        if calls["resolve"] < 3:
            raise OSError("[Errno 8] nodename nor servname provided, or not known")
        return "1.2.3.4"

    def fake_sleep(seconds):
        calls["sleep"] += 1

    assert run_weekly._wait_for_network(resolver=fake_resolver, sleep=fake_sleep) is True
    assert calls["resolve"] == 3
    assert calls["sleep"] == 2


def test_wait_for_network_succeeds_immediately_without_sleeping():
    resolved = run_weekly._wait_for_network(resolver=lambda host: "1.2.3.4", sleep=lambda s: (_ for _ in ()).throw(AssertionError("should not sleep")))
    assert resolved is True


def test_wait_for_network_gives_up_after_timeout():
    """DNS never comes up — give up once the elapsed time budget is spent,
    rather than retrying forever and blocking the launchd job indefinitely."""
    clock = {"t": 0.0}

    def fake_resolver(host):
        raise OSError("still down")

    def fake_sleep(seconds):
        clock["t"] += seconds

    def fake_now():
        return clock["t"]

    assert run_weekly._wait_for_network(
        resolver=fake_resolver, sleep=fake_sleep, now=fake_now,
    ) is False


def test_wait_for_network_touches_the_lock_on_each_poll():
    """So watchdog.py sees a live run during the wait, not a stale one."""
    config.RUN_LOCK.write_text(str(time.time() - 1000))
    import os
    old_mtime = config.RUN_LOCK.stat().st_mtime

    calls = {"n": 0}

    def fake_resolver(host):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("down")
        return "1.2.3.4"

    run_weekly._wait_for_network(resolver=fake_resolver, sleep=lambda s: None)
    assert config.RUN_LOCK.stat().st_mtime >= old_mtime


def test_wait_for_network_logs_start_and_recovery(caplog):
    calls = {"n": 0}

    def fake_resolver(host):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("down")
        return "1.2.3.4"

    with caplog.at_level("INFO"):
        run_weekly._wait_for_network(resolver=fake_resolver, sleep=lambda s: None)

    messages = [r.message for r in caplog.records]
    assert any("waiting" in m.lower() or "network" in m.lower() for m in messages)
    assert any("up" in m.lower() or "resolved" in m.lower() or "available" in m.lower() for m in messages)


# ── stage="failed" on every abort path ───────────────────────────────────────
# 2026-09-21: the abort correctly left latest.json untouched, but
# run_progress.json was left at {"stage": "started"} and the lock removed —
# nothing recorded that the run had actually failed, so a human (or another
# tool) reading run_progress.json couldn't tell a dead-and-noticed run from
# one that just hadn't gotten anywhere yet.

def test_write_progress_accepts_a_failed_stage_with_reason():
    run_weekly._write_progress("2026-09-21_0737", "failed", reason="no raw records from any source")
    progress = run_weekly._read_progress()
    assert progress["stage"] == "failed"
    assert progress["reason"] == "no raw records from any source"


def test_aborted_run_records_stage_failed_when_no_raw_records(monkeypatch):
    """The exact 2026-09-21 scenario: fetch_all returns nothing (network was
    down), so main() must abort AND record stage=failed with a reason,
    instead of leaving run_progress.json at 'started'."""
    import run_weekly as rw
    monkeypatch.setattr(rw, "_wait_for_network", lambda *a, **k: True)
    monkeypatch.setattr(rw.sources, "fetch_all", lambda: {"raw": [], "sources_fetched": [], "sources_failed": []})
    monkeypatch.setattr(
        rw.sys, "argv", ["run_weekly.py", "--force", "--no-push"],
    )

    rc = rw.main()

    assert rc == 1
    progress = rw._read_progress()
    assert progress["stage"] == "failed"
    assert "reason" in progress and progress["reason"]


def test_aborted_run_records_stage_failed_when_network_never_comes_up(monkeypatch):
    import run_weekly as rw
    monkeypatch.setattr(rw, "_wait_for_network", lambda *a, **k: False)
    monkeypatch.setattr(
        rw.sys, "argv", ["run_weekly.py", "--force", "--no-push"],
    )

    rc = rw.main()

    assert rc == 1
    progress = rw._read_progress()
    assert progress["stage"] == "failed"
    assert "network" in progress["reason"].lower()


def test_successful_run_still_ends_at_stage_published(monkeypatch):
    """Guard against the failed-stage plumbing accidentally clobbering the
    success path's terminal 'published' checkpoint."""
    import run_weekly as rw
    monkeypatch.setattr(rw, "_wait_for_network", lambda *a, **k: True)
    monkeypatch.setattr(rw.sources, "fetch_all", lambda: {"raw": [{"x": 1}], "sources_fetched": ["x"], "sources_failed": []})
    monkeypatch.setattr(rw.normalize, "normalize_all", lambda raw, run_id: [{"id": "1"}])
    monkeypatch.setattr(rw.geo, "geocode_activities", lambda acts: None)
    monkeypatch.setattr(rw.places, "load_places_as_activities", lambda run_id: [])
    monkeypatch.setattr(rw.publish, "build_payload", lambda *a, **k: {"activities": []})
    monkeypatch.setattr(rw.publish, "write_latest", lambda payload, run_id: None)
    monkeypatch.setattr(
        rw.sys, "argv", ["run_weekly.py", "--force", "--no-push", "--no-enrich"],
    )

    rc = rw.main()

    assert rc == 0
    progress = rw._read_progress()
    assert progress["stage"] == "published"


# ── scratch cleanup ─────────────────────────────────────────────────────────
def test_scratch_batches_are_cleaned_after_publish():
    """Scratch batch files are deleted after a successful run completes."""
    # Create scratch files in the state dir.
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (config.STATE_DIR / f"enrich_batch_{i}.json").write_text("{}")
    for i in range(2):
        (config.STATE_DIR / f"verify_batch_{i}.json").write_text("{}")
    (config.STATE_DIR / "smoke_batch_0.json").write_text("{}")
    (config.STATE_DIR / "other_file.json").write_text("{}")  # Should not be deleted

    run_weekly._clean_scratch()

    # Scratch files should be gone.
    assert not (config.STATE_DIR / "enrich_batch_0.json").exists()
    assert not (config.STATE_DIR / "enrich_batch_1.json").exists()
    assert not (config.STATE_DIR / "verify_batch_0.json").exists()
    assert not (config.STATE_DIR / "smoke_batch_0.json").exists()

    # Other files should remain.
    assert (config.STATE_DIR / "other_file.json").exists()


def test_scratch_is_kept_when_run_fails(monkeypatch):
    """Scratch files are only deleted on success, not on failed runs."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (config.STATE_DIR / "enrich_batch_0.json").write_text("{}")

    import run_weekly as rw
    monkeypatch.setattr(rw, "_wait_for_network", lambda *a, **k: False)
    monkeypatch.setattr(
        rw.sys, "argv", ["run_weekly.py", "--force", "--no-push"],
    )

    rc = rw.main()

    assert rc == 1
    # Scratch file should still exist (cleanup only runs on success).
    assert (config.STATE_DIR / "enrich_batch_0.json").exists()
