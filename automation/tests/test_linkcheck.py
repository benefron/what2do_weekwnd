"""Link health and the repair-or-flag policy.

The important cases are the negative ones. A naive checker calls a third of
places.json dead, because Belgian tourism sites rate-limit and fingerprint —
so a 403 or a dropped connection must NOT be treated as link rot. Only a
server saying "this page is gone" counts.
"""
import httpx
import pytest

import linkcheck


def _status_error(code):
    request = httpx.Request("GET", "https://example.be")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(linkcheck.time, "sleep", lambda s: None)


def fake_fetcher(monkeypatch, behaviour):
    """behaviour: url -> None (ok) or an exception instance to raise."""
    def _get(url, lang=None):
        outcome = behaviour(url)
        if isinstance(outcome, Exception):
            raise outcome
        return b"<html></html>"
    monkeypatch.setattr(linkcheck.sources, "http_get", _get)


# ── _root_of ────────────────────────────────────────────────────────────────
def test_root_of_strips_the_path():
    assert linkcheck._root_of("https://www.limburg.be/kiewit") == "https://www.limburg.be"


def test_root_of_returns_none_when_already_a_root():
    assert linkcheck._root_of("https://www.limburg.be") is None
    assert linkcheck._root_of("https://www.limburg.be/") is None


def test_root_of_rejects_junk():
    assert linkcheck._root_of("not-a-url") is None
    assert linkcheck._root_of("") is None


# ── outcomes ────────────────────────────────────────────────────────────────
def test_working_link_is_ok(monkeypatch):
    fake_fetcher(monkeypatch, lambda url: None)
    places = [{"id": "a", "name": "Fine", "website": "https://example.be/page"}]
    stats = linkcheck.check_places(places, throttle=0)

    assert places[0]["link_ok"] is True
    assert stats["ok"] == 1


def test_404_is_repaired_to_the_site_root(monkeypatch):
    fake_fetcher(monkeypatch,
                 lambda url: _status_error(404) if url.endswith("/kiewit") else None)
    places = [{"id": "a", "name": "Domein Kiewit", "website": "https://www.limburg.be/kiewit"}]
    stats = linkcheck.check_places(places, throttle=0)

    assert places[0]["website"] == "https://www.limburg.be"
    assert places[0]["link_ok"] is True
    assert stats["repaired"] == 1


def test_404_with_a_dead_root_is_flagged_not_dropped(monkeypatch):
    """The place still exists in the real world — keep it, drop only the link."""
    fake_fetcher(monkeypatch, lambda url: _status_error(404))
    places = [{"id": "a", "name": "Gone", "website": "https://dead.be/page"}]
    stats = linkcheck.check_places(places, throttle=0)

    assert places[0]["link_ok"] is False
    assert places[0]["website"] == "https://dead.be/page", "the URL is kept for inspection"
    assert stats["broken"] == 1
    assert len(places) == 1


def test_410_counts_as_gone(monkeypatch):
    fake_fetcher(monkeypatch, lambda url: _status_error(410))
    places = [{"id": "a", "name": "Gone", "website": "https://dead.be/page"}]
    linkcheck.check_places(places, throttle=0)
    assert places[0]["link_ok"] is False


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_bot_blocking_and_server_errors_are_not_link_rot(monkeypatch, code):
    """403 from west-vlaanderen.be means "you look like a robot", not "gone"."""
    fake_fetcher(monkeypatch, lambda url: _status_error(code))
    places = [{"id": "a", "name": "Blocked", "website": "https://www.west-vlaanderen.be/domeinen"}]
    stats = linkcheck.check_places(places, throttle=0)

    assert places[0]["link_ok"] is True
    assert stats["unknown"] == 1
    assert stats["broken"] == 0


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    httpx.RemoteProtocolError("bad"),
    RuntimeError("tls_client blew up"),
])
def test_network_failures_are_not_link_rot(monkeypatch, exc):
    fake_fetcher(monkeypatch, lambda url: exc)
    places = [{"id": "a", "name": "Flaky", "website": "https://www.pukkemuk.be"}]
    stats = linkcheck.check_places(places, throttle=0)

    assert places[0]["link_ok"] is True
    assert stats["unknown"] == 1


def test_place_without_a_website_is_skipped(monkeypatch):
    fake_fetcher(monkeypatch, lambda url: None)
    places = [{"id": "a", "name": "No site", "website": None}]
    stats = linkcheck.check_places(places, throttle=0)

    assert places[0]["link_ok"] is False
    assert stats["skipped"] == 1


def test_repair_is_not_attempted_when_the_url_is_already_a_root(monkeypatch):
    seen = []

    def _get(url, lang=None):
        seen.append(url)
        raise _status_error(404)
    monkeypatch.setattr(linkcheck.sources, "http_get", _get)

    places = [{"id": "a", "name": "Root 404", "website": "https://dead.be"}]
    linkcheck.check_places(places, throttle=0)
    assert seen == ["https://dead.be"], "no point re-requesting the same URL as its own root"


def test_mixed_batch_tallies_correctly(monkeypatch):
    def behaviour(url):
        if "gone" in url:
            return _status_error(404)
        if "blocked" in url:
            return _status_error(403)
        return None
    fake_fetcher(monkeypatch, behaviour)

    places = [
        {"id": "1", "name": "ok", "website": "https://fine.be"},
        {"id": "2", "name": "repairable", "website": "https://fine.be/gone"},
        {"id": "3", "name": "blocked", "website": "https://blocked.be"},
        {"id": "4", "name": "none", "website": ""},
    ]
    stats = linkcheck.check_places(places, throttle=0)
    assert stats == {"ok": 1, "repaired": 1, "broken": 0, "unknown": 1, "skipped": 1}
    assert places[1]["website"] == "https://fine.be"
