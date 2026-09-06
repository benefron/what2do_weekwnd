"""Backend selection, fallback, and the timeouts that bound a run.

Both backends must be bounded. The Claude path always was, via
subprocess.run(timeout=...); the Copilot fallback only had httpx's per-operation
timeout, which a trickling response resets indefinitely — one call ran 52
minutes and stalled a whole weekly run.
"""
import json
import time

import pytest

import config
import llm_runner


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr(llm_runner, "_gh_token", lambda: "tok")


@pytest.fixture
def instructions(tmp_path):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps([{"id": "a", "title_nl": "x"}]))
    return path


SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


# ── backend selection ───────────────────────────────────────────────────────
def test_claude_is_preferred(instructions, monkeypatch):
    monkeypatch.setattr(llm_runner, "_try_claude", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(llm_runner, "_try_copilot",
                        lambda *a, **k: pytest.fail("must not reach the fallback"))

    got = llm_runner.run_with_schema("do it", instructions, SCHEMA, "haiku", "1.50", "sonnet")
    assert got == {"ok": True}


def test_falls_back_to_copilot_when_claude_fails(instructions, monkeypatch):
    def claude_fails(*a, **k):
        raise RuntimeError("claude exited 1: ")
    monkeypatch.setattr(llm_runner, "_try_claude", claude_fails)
    monkeypatch.setattr(llm_runner, "_try_copilot", lambda *a, **k: {"ok": "fallback"})

    got = llm_runner.run_with_schema("do it", instructions, SCHEMA, "haiku", "1.50", "sonnet")
    assert got == {"ok": "fallback"}


def test_both_backends_failing_raises(instructions, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(llm_runner, "_try_claude", boom)
    monkeypatch.setattr(llm_runner, "_try_copilot", boom)

    with pytest.raises(RuntimeError):
        llm_runner.run_with_schema("do it", instructions, SCHEMA, "haiku", "1.50", "sonnet")


# ── the wall-clock ceiling on the fallback ──────────────────────────────────
def test_copilot_is_bounded_by_wall_clock(monkeypatch):
    """Regression: httpx's timeout is per-read, so a response that keeps
    trickling never trips it. Without an overall deadline one call blocked the
    pipeline for 52 minutes."""
    monkeypatch.setattr(config, "COPILOT_TOTAL_TIMEOUT_SECONDS", 0.2)

    def never_returns(token, system_msg, user_msg, model):
        time.sleep(30)
        return "too late"
    monkeypatch.setattr(llm_runner, "_post_copilot", never_returns)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="wall clock"):
        llm_runner._call_copilot("sys", "user", "sonnet")
    assert time.monotonic() - started < 5, "the deadline did not actually fire"


def test_copilot_returns_normally_within_the_deadline(monkeypatch):
    monkeypatch.setattr(config, "COPILOT_TOTAL_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(llm_runner, "_post_copilot",
                        lambda token, s, u, m: "the answer")
    assert llm_runner._call_copilot("sys", "user", "sonnet") == "the answer"


def test_copilot_errors_propagate_unchanged(monkeypatch):
    monkeypatch.setattr(config, "COPILOT_TOTAL_TIMEOUT_SECONDS", 5)

    def boom(*a, **k):
        raise ValueError("401 unauthorised")
    monkeypatch.setattr(llm_runner, "_post_copilot", boom)

    with pytest.raises(ValueError, match="401"):
        llm_runner._call_copilot("sys", "user", "sonnet")


def test_total_timeout_is_not_looser_than_the_claude_path():
    """A fallback allowed to run longer than the primary it replaces would make
    failure slower than success."""
    assert config.COPILOT_TOTAL_TIMEOUT_SECONDS < 240
    assert config.COPILOT_READ_TIMEOUT_SECONDS <= config.COPILOT_TOTAL_TIMEOUT_SECONDS


# ── response parsing ────────────────────────────────────────────────────────
def test_copilot_json_is_parsed(instructions, monkeypatch):
    monkeypatch.setattr(llm_runner, "_call_copilot", lambda *a: '{"ok": true}')
    assert llm_runner._try_copilot("do it", instructions, SCHEMA, "sonnet") == {"ok": True}


def test_fenced_json_is_unwrapped(instructions, monkeypatch):
    """Chat models wrap JSON in markdown fences even when told not to."""
    monkeypatch.setattr(llm_runner, "_call_copilot",
                        lambda *a: '```json\n{"ok": true}\n```')
    assert llm_runner._try_copilot("do it", instructions, SCHEMA, "sonnet") == {"ok": True}


def test_bare_fence_is_unwrapped(instructions, monkeypatch):
    monkeypatch.setattr(llm_runner, "_call_copilot", lambda *a: '```\n{"ok": true}\n```')
    assert llm_runner._try_copilot("do it", instructions, SCHEMA, "sonnet") == {"ok": True}


def test_unparseable_response_raises(instructions, monkeypatch):
    monkeypatch.setattr(llm_runner, "_call_copilot", lambda *a: "I cannot help with that")
    with pytest.raises(json.JSONDecodeError):
        llm_runner._try_copilot("do it", instructions, SCHEMA, "sonnet")
