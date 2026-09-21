"""fetch_all()'s bookkeeping: which sources land in sources_fetched vs
sources_failed, and the claude_search empty-vs-failed distinction in
particular.

A dead source must log and be skipped (CLAUDE.md), never abort the rest of
fetch_all — and a genuinely broken claude_search pass must show up in
sources_failed, not get folded into "claude_search(empty)" where it is
indistinguishable from a quiet week. See claude_search.SearchFailed.
"""
import config
import claude_search
import sources


def _patch_other_stages(monkeypatch, *, uit=None, ods=None, feeds=None):
    """Stub out every fetch stage except claude_search with harmless defaults."""
    monkeypatch.setattr(config, "UITDATABANK_ENABLED", False)
    monkeypatch.setattr(sources, "fetch_uit_agenda", lambda: uit or ([], [], []))
    monkeypatch.setattr(sources, "fetch_opendatasoft", lambda: ods or ([], [], []))
    monkeypatch.setattr(sources, "fetch_feeds", lambda: feeds or ([], [], []))
    monkeypatch.setattr(sources, "load_manual_overrides", lambda: [])


# ── claude_search: empty vs failed ──────────────────────────────────────────
def test_empty_claude_search_is_still_a_success(monkeypatch):
    """The search ran and genuinely found nothing this week — not a failure."""
    _patch_other_stages(monkeypatch)
    monkeypatch.setattr(config, "CLAUDE_SEARCH_ENABLED", True)
    monkeypatch.setattr(claude_search, "fetch_events", lambda: [])

    result = sources.fetch_all()

    assert "claude_search(empty)" in result["sources_fetched"]
    assert "claude_search" not in result["sources_failed"]


def test_failed_claude_search_is_reported_as_failed_not_empty(monkeypatch):
    """Regression: a CLI/Copilot failure inside claude_search used to be
    swallowed to [] and recorded as "claude_search(empty)" — indistinguishable
    from a quiet week and never in sources_failed (2026-09-21 incident)."""
    _patch_other_stages(monkeypatch)
    monkeypatch.setattr(config, "CLAUDE_SEARCH_ENABLED", True)

    def boom():
        raise claude_search.SearchFailed("claude search exited 1")

    monkeypatch.setattr(claude_search, "fetch_events", boom)

    result = sources.fetch_all()

    assert "claude_search" in result["sources_failed"]
    assert "claude_search(empty)" not in result["sources_fetched"]
    assert "claude_search" not in result["sources_fetched"]


def test_failed_claude_search_does_not_abort_fetch_all(monkeypatch):
    """A dead source logs and is skipped — the rest of fetch_all still runs."""
    uit_records = [{"title": "kermis"}]
    _patch_other_stages(monkeypatch, uit=(uit_records, ["uitinleuven"], []))
    monkeypatch.setattr(config, "CLAUDE_SEARCH_ENABLED", True)

    def boom():
        raise claude_search.SearchFailed("boom")

    monkeypatch.setattr(claude_search, "fetch_events", boom)

    result = sources.fetch_all()

    assert result["raw"] == uit_records
    assert "uitinleuven" in result["sources_fetched"]
    assert "claude_search" in result["sources_failed"]


def test_disabled_claude_search_is_neither_fetched_nor_failed(monkeypatch):
    _patch_other_stages(monkeypatch)
    monkeypatch.setattr(config, "CLAUDE_SEARCH_ENABLED", False)

    result = sources.fetch_all()

    assert not any(s.startswith("claude_search") for s in result["sources_fetched"])
    assert "claude_search" not in result["sources_failed"]


# ── general fetch_all bookkeeping ───────────────────────────────────────────
def test_one_failing_source_is_skipped_others_still_return_records(monkeypatch):
    """opendatasoft raising must not stop uit_agenda's records from coming
    through, and must be listed in sources_failed."""
    uit_records = [{"title": "concert"}]

    def ods_boom():
        raise RuntimeError("dataset field renamed")

    monkeypatch.setattr(config, "UITDATABANK_ENABLED", False)
    monkeypatch.setattr(sources, "fetch_uit_agenda", lambda: (uit_records, ["uitinleuven"], []))
    monkeypatch.setattr(sources, "fetch_opendatasoft", ods_boom)
    monkeypatch.setattr(sources, "fetch_feeds", lambda: ([], [], []))
    monkeypatch.setattr(sources, "load_manual_overrides", lambda: [])
    monkeypatch.setattr(config, "CLAUDE_SEARCH_ENABLED", False)

    result = sources.fetch_all()

    assert result["raw"] == uit_records
    assert "uitinleuven" in result["sources_fetched"]
    assert "opendatasoft" in result["sources_failed"]


def test_all_sources_ok_combines_records_from_each(monkeypatch):
    uit_records = [{"title": "a"}]
    ods_records = [{"title": "b"}]
    feed_records = [{"title": "c"}]

    monkeypatch.setattr(config, "UITDATABANK_ENABLED", False)
    monkeypatch.setattr(sources, "fetch_uit_agenda", lambda: (uit_records, ["uitinleuven"], []))
    monkeypatch.setattr(sources, "fetch_opendatasoft", lambda: (ods_records, ["odwb_wallonie"], []))
    monkeypatch.setattr(sources, "fetch_feeds", lambda: (feed_records, ["some_feed"], []))
    monkeypatch.setattr(sources, "load_manual_overrides", lambda: [])
    monkeypatch.setattr(config, "CLAUDE_SEARCH_ENABLED", False)

    result = sources.fetch_all()

    assert result["raw"] == uit_records + ods_records + feed_records
    assert result["sources_failed"] == []
    assert set(result["sources_fetched"]) == {"uitinleuven", "odwb_wallonie", "some_feed"}
