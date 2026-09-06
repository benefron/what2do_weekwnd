"""Shared fixtures.

The one rule that matters here: **no test may touch the network.** Every module
under test fetches from Belgian venue sites, Nominatim or Wikipedia, and a suite
that really called them would fail whenever a tourism site rate-limits — which
is exactly the flakiness that made the first link check report 58 dead links
that were all alive. `_no_network` is autouse, so any accidental real request
raises instead of quietly working on your machine and breaking in CI.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# automation/ is on sys.path via pytest.ini, but keep this so the files can also
# be run directly (python -m pytest automation/tests/test_geo.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class NetworkUsedInTest(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise NetworkUsedInTest(
            f"test made a real HTTP request to {args[0] if args else kwargs.get('url')!r}; "
            "patch the fetcher or add a fixture"
        )

    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx, "request", boom, raising=False)
    yield


@pytest.fixture
def fixture_text():
    """Read a file from automation/tests/fixtures/."""
    def _read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return _read


@pytest.fixture
def fixture_json():
    def _read(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return _read


@pytest.fixture
def make_activity():
    """A minimally-valid activity dict; override any field via kwargs."""
    def _make(**over):
        act = {
            "id": "abc1234567",
            "source": "uitinleuven",
            "url": "https://www.uitinleuven.be/agenda/e/test/abc1234567",
            "title_nl": "Test activiteit",
            "description_nl": "Beschrijving",
            "date_kind": "single",
            "date_start": "2026-10-20T10:00:00",
            "date_end": "2026-10-20T12:00:00",
            "all_day": False,
            "occurrences": [{"start": "2026-10-20T10:00:00", "end": "2026-10-20T12:00:00"}],
            "city": "Leuven",
            "venue_name": "Ergens",
            "lat": None,
            "lng": None,
        }
        act.update(over)
        return act
    return _make


@pytest.fixture
def make_place():
    """A minimally-valid places.json entry; override any field via kwargs."""
    def _make(**over):
        place = {
            "id": "place-museum-test",
            "kind": "museum",
            "source": "claude_build",
            "name": "Testmuseum",
            "city": "Leuven",
            "province": "Vlaams-Brabant",
            "address": "Teststraat 1, 3000 Leuven",
            "lat": 50.879,
            "lng": 4.701,
            "website": "https://example.be",
            "description_nl": "Iets",
            "blurb_en": "Something",
            "price_type": "paid",
            "price_min_eur": 5,
            "price_max_eur": 10,
            "age_min": 4,
            "age_max": 12,
            "indoor": True,
            "seasonal": None,
            "tags": [],
        }
        place.update(over)
        return place
    return _make
