"""Payload construction: field slimming, filter counts, and the metadata the
frontend reads. Archive pruning after successful publish.
"""
import pytest

import config
import publish


def build(activities, **kw):
    return publish.build_payload(
        activities,
        kw.get("run_id", "2026-09-06_1200"),
        kw.get("fetched", ["uitinleuven"]),
        kw.get("failed", []),
        kw.get("degraded", False),
    )


@pytest.fixture
def event(make_activity):
    def _make(**over):
        act = make_activity(
            category="museum_exhibition", feature_tags=["animals"],
            weekend_bucket=["this_weekend"], kind=None, indoor_outdoor="indoor",
            price_type="free", primary_language="nl",
        )
        act.update(over)
        return act
    return _make


@pytest.fixture
def place(make_activity):
    def _make(**over):
        act = make_activity(
            date_kind="permanent", category="zoo_animal_park", feature_tags=["animals"],
            weekend_bucket=["later"], kind="zoo", indoor_outdoor="both",
        )
        act.update(over)
        return act
    return _make


# ── slimming ────────────────────────────────────────────────────────────────
def test_only_published_fields_survive(event):
    payload = build([event(secret_internal_field="leaked")])
    assert "secret_internal_field" not in payload["activities"][0]


def test_legacy_single_family_fields_are_dropped(event):
    """places.json entries (and old cache/archive records) may still carry
    fits_4yo/fits_8yo/french_required from before the app served families
    anywhere in Belgium of any age mix. publish must slim them away rather than
    ship a field the frontend no longer knows about."""
    act = build([event(fits_4yo=True, fits_8yo=True, french_required=False)])["activities"][0]
    assert "fits_4yo" not in act
    assert "fits_8yo" not in act
    assert "french_required" not in act


def test_every_published_field_is_present_even_when_unset(event):
    act = payload_first = build([event()])["activities"][0]
    for field in publish._PUBLISHED_FIELDS:
        assert field in act, f"{field} missing from the published record"
    assert payload_first is act


def test_new_fields_reach_the_payload(event):
    act = build([event(indoor_outdoor="indoor", link_ok=True,
                       school_holiday_nl="herfstvakantie",
                       school_holiday_fr=None)])["activities"][0]
    assert act["indoor_outdoor"] == "indoor"
    assert act["link_ok"] is True
    assert act["school_holiday_nl"] == "herfstvakantie"
    assert act["school_holiday_fr"] is None


# ── counts ──────────────────────────────────────────────────────────────────
def test_categories_are_counted_from_events_only(event, place):
    payload = build([event(category="museum_exhibition"),
                     place(category="zoo_animal_park")])
    keys = {c["key"] for c in payload["categories"]}
    assert "museum_exhibition" in keys
    assert "zoo_animal_park" not in keys, "place categories belong to place_kinds"


def test_place_kinds_are_counted_from_places_only(event, place):
    payload = build([event(), place(kind="zoo")])
    keys = {k["key"] for k in payload["place_kinds"]}
    assert keys == {"zoo"}


def test_feature_tags_are_counted_across_everything(event, place):
    payload = build([event(feature_tags=["animals"]), place(feature_tags=["animals", "water_play"])])
    counts = {t["key"]: t["count"] for t in payload["feature_tags"]}
    assert counts["animals"] == 2
    assert counts["water_play"] == 1


def test_counts_are_ordered_by_frequency(event):
    payload = build([event(id="1", category="museum_exhibition"),
                     event(id="2", category="museum_exhibition"),
                     event(id="3", category="film")])
    assert payload["categories"][0]["key"] == "museum_exhibition"
    assert payload["categories"][0]["count"] == 2


# ── metadata ────────────────────────────────────────────────────────────────
def test_both_school_calendars_are_shipped(event):
    payload = build([event()])
    assert payload["school_holidays_nl"] == config.SCHOOL_HOLIDAYS_NL
    assert payload["school_holidays_fr"] == config.SCHOOL_HOLIDAYS_FR


def test_legacy_school_holidays_key_is_kept(event):
    """Older cached clients still read `school_holidays`."""
    payload = build([event()])
    assert payload["school_holidays"] == config.SCHOOL_HOLIDAYS_NL


def test_run_metadata_is_recorded(event):
    payload = build([event()], run_id="run-42", fetched=["a"], failed=["b"], degraded=True)
    assert payload["run_id"] == "run-42"
    assert payload["sources_fetched"] == ["a"]
    assert payload["sources_failed"] == ["b"]
    assert payload["degraded"] is True


def test_window_spans_the_configured_horizon(event):
    from datetime import date
    payload = build([event()])
    start = date.fromisoformat(payload["window"]["start"])
    end = date.fromisoformat(payload["window"]["end"])
    assert (end - start).days == config.WINDOW_WEEKS * 7


def test_leuven_centre_is_shipped_for_the_client_side_fallback(event):
    payload = build([event()])
    assert payload["leuven_center"] == list(config.LEUVEN_CENTER)


def test_empty_input_still_builds_a_valid_payload():
    payload = build([])
    assert payload["activities"] == []
    assert payload["categories"] == []
    assert payload["place_kinds"] == []


# ── archive pruning ─────────────────────────────────────────────────────────
def test_archive_is_pruned_to_newest_n(tmp_path, monkeypatch):
    """Pruning keeps only the newest `keep` snapshots, sorted lexically."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    # Create 12 snapshots with lexically sortable names (run_id format).
    for i in range(12):
        (archive_dir / f"2026-09-{10+i:02d}_0000.json").write_text(f'{{"n": {i}}}')

    monkeypatch.setattr(config, "ARCHIVE_KEEP", 8)
    removed = publish.prune_archive(archive_dir, keep=8)

    # Should remove 4 oldest, keep 8 newest.
    assert len(removed) == 4
    assert len(list(archive_dir.glob("*.json"))) == 8

    # Check that the oldest files were removed.
    for i in range(4):
        assert not (archive_dir / f"2026-09-{10+i:02d}_0000.json").exists()

    # Check that the newest files remain.
    for i in range(4, 12):
        assert (archive_dir / f"2026-09-{10+i:02d}_0000.json").exists()


def test_prune_keeps_everything_when_under_the_limit(tmp_path):
    """If fewer than `keep` files exist, none are deleted."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    for i in range(5):
        (archive_dir / f"2026-09-{10+i:02d}_0000.json").write_text(f'{{"n": {i}}}')

    removed = publish.prune_archive(archive_dir, keep=8)

    assert removed == []
    assert len(list(archive_dir.glob("*.json"))) == 5


def test_prune_ignores_non_json_files(tmp_path):
    """Non-.json files in the archive dir are not touched."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    # Create 12 .json files and some other files.
    for i in range(12):
        (archive_dir / f"2026-09-{10+i:02d}_0000.json").write_text(f'{{"n": {i}}}')
    (archive_dir / "README.txt").write_text("keep me")
    (archive_dir / "old_backup.bak").write_text("keep me too")

    removed = publish.prune_archive(archive_dir, keep=8)

    assert len(removed) == 4
    assert (archive_dir / "README.txt").exists()
    assert (archive_dir / "old_backup.bak").exists()


def test_prune_failure_does_not_fail_the_run(tmp_path, monkeypatch):
    """A failure to delete one file is logged but not raised."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    for i in range(10):
        (archive_dir / f"2026-09-{10+i:02d}_0000.json").write_text(f'{{"n": {i}}}')

    deleted_files = []
    original_unlink = type(archive_dir).unlink

    def failing_unlink(self):
        deleted_files.append(self)
        if len(deleted_files) == 2:  # Fail on second delete
            raise OSError("Permission denied")
        original_unlink(self)

    # Monkeypatch unlink on Path instances
    monkeypatch.setattr("pathlib.Path.unlink", failing_unlink)

    removed = publish.prune_archive(archive_dir, keep=8)

    # First file should succeed, second should fail and be logged,
    # but the function continues.
    # removed list only includes successfully deleted files.
    assert len(removed) == 1
    # But the files are still attempted to be deleted (2 were processed).
    assert len(deleted_files) >= 2
    # At least one successful deletion happened.
    assert len(list(archive_dir.glob("*.json"))) < 10


def test_prune_archive_dir_not_exists(tmp_path):
    """Pruning a nonexistent directory returns empty list."""
    archive_dir = tmp_path / "nonexistent"
    removed = publish.prune_archive(archive_dir, keep=8)
    assert removed == []
