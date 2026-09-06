"""Incremental cache persistence.

The cache used to be written once, after every batch and the verify pass, so a
run killed at batch 40 of 47 lost all forty. That matters most on a
SCHEMA_VERSION bump, when the whole cache is invalid and the run is hours rather
than minutes — which is exactly when a flaky backend is most likely to stall it.
"""
import json

import pytest

import config
import enrich


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "enrichment_cache.json"
    monkeypatch.setattr(config, "ENRICHMENT_CACHE_JSON", path)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "ENRICH_BATCH_SIZE", 2)
    return path


def read_cache(path):
    return json.loads(path.read_text()) if path.exists() else {}


def classification(act_id, **over):
    fields = {
        "id": act_id,
        "category": "other",
        "feature_tags": [],
        "age_min": 4, "age_max": 10,
        "fits_4yo": True, "fits_8yo": True,
        "primary_language": "nl",
        "french_required": False,
        "language_note": None,
        "language_free": False,
        "price_type": "paid",
        "price_min_eur": 5, "price_max_eur": 5,
        "blurb_en": "A thing.",
        "is_special_event": True,
        "is_recurring_class": False,
        "family_relevant": True,
        "confidence": "high",
        "indoor_outdoor": "indoor",
    }
    fields.update(over)
    return fields


def stub_backend(monkeypatch, responder):
    """responder(payload_ids) -> list of classification dicts, or raises."""
    def fake_run(**kw):
        payload = json.loads(kw["input_path"].read_text())
        ids = [a["id"] for a in payload["activities"]]
        return {"activities": responder(ids)}
    monkeypatch.setattr(enrich.llm_runner, "run_with_schema", fake_run)


# ── the core guarantee ──────────────────────────────────────────────────────
def test_cache_is_written_before_the_run_finishes(cache_file, monkeypatch, make_activity):
    """A batch's work must survive the run being killed straight afterwards."""
    acts = [make_activity(id=f"id{i}", title_nl=f"Event {i}") for i in range(6)]
    snapshots: list[set[str]] = []
    calls = {"n": 0}

    def responder(ids):
        calls["n"] += 1
        # Record what is on disk as each batch *starts*, so snapshots[n] is the
        # state after n batches finished.
        snapshots.append(set(read_cache(cache_file)))
        return [classification(i) for i in ids]

    stub_backend(monkeypatch, responder)
    enrich.enrich_all(acts)

    assert snapshots[0] == set(), "nothing should be cached before the first batch"
    assert snapshots[1] == {"id0", "id1"}, "batch 1 was not persisted before batch 2 ran"
    assert snapshots[2] == {"id0", "id1", "id2", "id3"}
    assert set(read_cache(cache_file)) == {f"id{i}" for i in range(6)}


def test_a_stall_partway_keeps_the_earlier_batches(cache_file, monkeypatch, make_activity):
    """The 52-minute and 3h21m stalls that motivated this: the batches before
    them used to be thrown away."""
    acts = [make_activity(id=f"id{i}", title_nl=f"Event {i}") for i in range(6)]

    calls = {"n": 0}

    def responder(ids):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("copilot exceeded 200s wall clock")
        return [classification(i) for i in ids]

    stub_backend(monkeypatch, responder)
    enrich.enrich_all(acts)

    cached = read_cache(cache_file)
    assert set(cached) == {"id0", "id1"}, "the successful first batch was lost"


def test_a_resumed_run_skips_what_was_already_cached(cache_file, monkeypatch, make_activity):
    """Second run must not re-pay for records the first one already classified."""
    acts = [make_activity(id=f"id{i}", title_nl=f"Event {i}") for i in range(6)]

    calls = {"n": 0}

    def failing_after_one(ids):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("stalled")
        return [classification(i) for i in ids]

    stub_backend(monkeypatch, failing_after_one)
    enrich.enrich_all(acts)

    # Fresh objects, as a new run would build them.
    acts2 = [make_activity(id=f"id{i}", title_nl=f"Event {i}") for i in range(6)]
    asked = []

    def record_ids(ids):
        asked.extend(ids)
        return [classification(i) for i in ids]

    stub_backend(monkeypatch, record_ids)
    stats = enrich.enrich_all(acts2)

    assert "id0" not in asked and "id1" not in asked, "re-classified cached records"
    assert stats["classified"] == 4
    assert acts2[0]["category"] == "other", "cached record was not applied"


# ── the verify interaction ──────────────────────────────────────────────────
def test_low_confidence_records_are_not_cached_early(cache_file, monkeypatch, make_activity):
    """Caching a low-confidence classification would let a later run serve it
    from cache and skip the Sonnet pass it was queued for."""
    acts = [make_activity(id="id0", title_nl="Thin"), make_activity(id="id1", title_nl="Also thin")]

    def responder(ids):
        return [classification(i, confidence="low") for i in ids]

    stub_backend(monkeypatch, responder)
    enrich.enrich_all(acts)

    # It reaches the cache only via the final write, after verify ran.
    cached = read_cache(cache_file)
    for entry in cached.values():
        assert entry["model"].endswith("+verify") or entry["confidence"] != "low"


def test_rule_conflicts_are_not_cached_early(cache_file, monkeypatch, make_activity):
    acts = [
        make_activity(id="id0", description_nl="Toegang € 5"),
        make_activity(id="id1", description_nl="Toegang € 5"),
    ]
    seen_mid_run = {}
    calls = {"n": 0}

    def responder(ids):
        calls["n"] += 1
        if calls["n"] > 1:
            seen_mid_run.update(read_cache(cache_file))
        return [classification(i, price_type="free") for i in ids]

    monkeypatch.setattr(config, "ENRICH_BATCH_SIZE", 1)
    stub_backend(monkeypatch, responder)
    enrich.enrich_all(acts)

    assert "id0" not in seen_mid_run, "a euro-vs-free conflict was cached before verify"


def test_high_confidence_records_are_cached_early(cache_file, monkeypatch, make_activity):
    acts = [make_activity(id="id0"), make_activity(id="id1")]
    seen_mid_run = {}
    calls = {"n": 0}

    def responder(ids):
        calls["n"] += 1
        if calls["n"] > 1:
            seen_mid_run.update(read_cache(cache_file))
        return [classification(i, confidence="high") for i in ids]

    monkeypatch.setattr(config, "ENRICH_BATCH_SIZE", 1)
    stub_backend(monkeypatch, responder)
    enrich.enrich_all(acts)

    assert "id0" in seen_mid_run


# ── robustness ──────────────────────────────────────────────────────────────
def test_a_failing_persist_hook_does_not_lose_the_batch(cache_file, monkeypatch, make_activity):
    acts = [make_activity(id="id0"), make_activity(id="id1")]

    def boom(cache):
        raise OSError("disk full")
    monkeypatch.setattr(enrich, "_save_cache", boom)
    stub_backend(monkeypatch, lambda ids: [classification(i) for i in ids])

    # Should complete and still classify, even though nothing could be written.
    enrich.enrich_all(acts)
    assert acts[0]["category"] == "other"


def test_every_batch_still_reaches_the_result(cache_file, monkeypatch, make_activity):
    acts = [make_activity(id=f"id{i}") for i in range(6)]
    stub_backend(monkeypatch, lambda ids: [classification(i) for i in ids])
    stats = enrich.enrich_all(acts)

    assert stats["batches"] == 3
    assert all(a["enrichment_model"].startswith(config.ENRICH_MODEL) for a in acts)
    assert set(read_cache(cache_file)) == {f"id{i}" for i in range(6)}


def test_a_failed_batch_degrades_only_its_own_records(cache_file, monkeypatch, make_activity):
    acts = [make_activity(id=f"id{i}") for i in range(4)]
    calls = {"n": 0}

    def responder(ids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("batch 0 stalled")
        return [classification(i) for i in ids]

    stub_backend(monkeypatch, responder)
    enrich.enrich_all(acts)

    assert acts[0]["enrichment_model"] == "degraded"
    assert acts[2]["enrichment_model"].startswith(config.ENRICH_MODEL)
    # A degraded record must stay visible on either side of the rainy-day filter.
    assert acts[0]["indoor_outdoor"] == "both"
