"""Assemble data/latest.json, mirror it for the frontend, snapshot an archive
copy, and commit + push (the push triggers the Pages build/deploy workflow).
"""
import json
import logging
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)

_PUBLISHED_FIELDS = (
    "id", "source", "source_label", "source_event_id", "url", "last_seen_run",
    "title_nl", "description_nl", "organizer_nl", "blurb_en", "image_url",
    "image_credit", "image_credit_url",
    "date_start", "date_end", "all_day", "occurrences", "date_kind",
    "weekend_bucket", "in_school_holiday", "school_holiday_name",
    "school_holiday_nl", "school_holiday_fr",
    "venue_name", "address", "city", "postal_code", "lat", "lng",
    "distance_km", "geocode_source", "kind", "province", "indoor",
    "indoor_outdoor", "link_ok", "seasonal",
    "category", "feature_tags", "audience", "age_min", "age_max", "age_source",
    "price_type", "price_min_eur", "price_max_eur", "price_note_nl",
    "primary_language", "language_note", "language_free",
    "is_special_event", "is_recurring_class", "booking_required",
    "enrichment_model", "confidence",
)


def build_payload(activities: list[dict], run_id: str, sources_fetched, sources_failed, degraded: bool) -> dict:
    today = datetime.now(timezone.utc).astimezone().date()
    slim = [{k: a.get(k) for k in _PUBLISHED_FIELDS} for a in activities]

    events = [a for a in slim if a.get("date_kind") != "permanent"]
    permanent = [a for a in slim if a.get("date_kind") == "permanent"]
    cat_counts = Counter(a.get("category") for a in events if a.get("category"))
    tag_counts = Counter(t for a in slim for t in (a.get("feature_tags") or []))
    kind_counts = Counter(a.get("kind") for a in permanent if a.get("kind"))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "window": {
            "start": today.isoformat(),
            "end": (today + timedelta(weeks=config.WINDOW_WEEKS)).isoformat(),
        },
        "leuven_center": list(config.LEUVEN_CENTER),
        "school_holidays": config.SCHOOL_HOLIDAYS_NL,
        "school_holidays_nl": config.SCHOOL_HOLIDAYS_NL,
        "school_holidays_fr": config.SCHOOL_HOLIDAYS_FR,
        "categories": [{"key": k, "count": c} for k, c in cat_counts.most_common()],
        "feature_tags": [{"key": k, "count": c} for k, c in tag_counts.most_common()],
        "place_kinds": [{"key": k, "count": c} for k, c in kind_counts.most_common()],
        "sources_fetched": sources_fetched,
        "sources_failed": sources_failed,
        "degraded": degraded,
        "activities": slim,
    }


def prune_archive(archive_dir: Path, keep: int = config.ARCHIVE_KEEP) -> list[Path]:
    """Delete all but the newest `keep` snapshots, sorted by filename.

    Run IDs are YYYY-MM-DD_HHMM so lexical order is chronological.
    Ignores non-.json files and returns the list of removed paths.
    """
    if not archive_dir.exists():
        return []

    json_files = sorted([f for f in archive_dir.iterdir() if f.is_file() and f.suffix == ".json"])
    to_remove = json_files[:-keep] if len(json_files) > keep else []
    removed = []
    for f in to_remove:
        try:
            f.unlink()
            removed.append(f)
        except OSError as exc:
            log.warning("failed to delete %s: %s", f, exc)
    return removed


def write_latest(payload: dict, run_id: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.LATEST_JSON.write_text(text)

    config.FRONTEND_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.FRONTEND_DATA_DIR / "latest.json").write_text(text)

    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (config.ARCHIVE_DIR / f"{run_id}.json").write_text(text)

    try:
        prune_archive(config.ARCHIVE_DIR, config.ARCHIVE_KEEP)
    except Exception as exc:
        log.warning("archive pruning failed: %s", exc)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=config.REPO_ROOT, capture_output=True, text=True, check=True)


def commit_and_push(run_id: str) -> bool:
    paths = [
        str(config.LATEST_JSON),
        str(config.ARCHIVE_DIR / f"{run_id}.json"),
        str(config.GEOCODE_CACHE_JSON),
        str(config.ENRICHMENT_CACHE_JSON),
        str(config.MANUAL_OVERRIDES_JSON),
        str(config.DATA_DIR / "places.json"),
    ]
    _git("add", *[p for p in paths])
    # Stage deletions in archive directory (pruning)
    _git("add", "-A", str(config.ARCHIVE_DIR))
    status = _git("status", "--porcelain", "--", *paths)
    if not status.stdout.strip():
        log.info("nothing to commit for %s", run_id)
        return False
    _git("commit", "-m", f"weekend update — {run_id}")
    _git("push", "origin", "main")
    log.info("pushed weekend update for %s", run_id)
    return True
