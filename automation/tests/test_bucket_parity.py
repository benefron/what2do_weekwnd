"""Parity test between `normalize._bucketize` (Python, the pipeline's source
of truth) and `frontend/src/lib/buckets.ts#computeBuckets` (the browser's live
re-derivation of the same fields, added in ba47ab5 so a feed published Monday
doesn't show stale buckets by Saturday).

See `frontend/src/lib/buckets.parity.test.ts` for the TypeScript half of this
pair — both load the SAME fixture,
`automation/tests/fixtures/bucket_cases.json`, so a change to either
implementation's date arithmetic that isn't mirrored on the other side fails a
test on whichever side drifted, instead of silently shipping a frontend that
disagrees with the weekly feed.

The fixture is `{"holidays_nl": [...], "holidays_fr": [...], "cases": [...]}`.
`holidays_nl`/`holidays_fr` are `config.SCHOOL_HOLIDAYS_NL`/`_FR` verbatim, in
the exact shape `publish.build_payload` ships them to the frontend as
`school_holidays_nl`/`_fr` (`{"name", "start", "end"}`) — embedded in the
fixture itself, not hand-copied into the TS test a third time. If config.py's
calendars are edited without regenerating the fixture,
`test_fixture_calendars_match_config` below fails loudly with a pointer here,
instead of the two parity suites quietly agreeing with each other while both
disagreeing with the real calendars.

Regenerating the fixture (Python is the source of truth — run this, then
hand-check a handful of cases, then re-run both suites; if a case OTHER than
the one you meant to touch changes, stop and find out why before accepting
it):

    import sys, json
    from datetime import date, timedelta
    sys.path.insert(0, "automation")
    import config, normalize

    def bucketize(today_iso, activity, window_weeks=config.WINDOW_WEEKS):
        today = date.fromisoformat(today_iso)
        window_end = today + timedelta(weeks=window_weeks)
        act = dict(activity)
        normalize._bucketize(act, today, window_end)
        return {k: act.get(k) for k in
                ("weekend_bucket", "school_holiday_nl", "school_holiday_fr", "in_school_holiday")}

    path = "automation/tests/fixtures/bucket_cases.json"
    data = json.load(open(path))
    data["holidays_nl"] = config.SCHOOL_HOLIDAYS_NL
    data["holidays_fr"] = config.SCHOOL_HOLIDAYS_FR
    for case in data["cases"]:
        case["expected"] = bucketize(case["today"], case["activity"])
    json.dump(data, open(path, "w"), indent=2, ensure_ascii=False)
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import config
import normalize

FIXTURE = Path(__file__).parent / "fixtures" / "bucket_cases.json"
FIXTURE_DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = FIXTURE_DATA["cases"]


def _run(case):
    today = date.fromisoformat(case["today"])
    window_end = today + timedelta(weeks=config.WINDOW_WEEKS)
    act = dict(case["activity"])
    normalize._bucketize(act, today, window_end)
    return {
        "weekend_bucket": act.get("weekend_bucket"),
        "school_holiday_nl": act.get("school_holiday_nl"),
        "school_holiday_fr": act.get("school_holiday_fr"),
        "in_school_holiday": act.get("in_school_holiday"),
    }


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_bucket_case(case):
    if case.get("known_divergence"):
        pytest.xfail(f"known TS/py divergence: {case['known_divergence']}")
    actual = _run(case)
    assert actual == case["expected"]


def test_fixture_has_enough_coverage():
    assert len(CASES) >= 25


def test_fixture_calendars_match_config():
    """See the module docstring: the fixture embeds its own copy of the two
    school calendars so the TS test can read them from the fixture instead of
    hand-copying config.py's tables a third time. This is what catches a
    config.py calendar edit (e.g. extending the tables for a new school year)
    that forgot to regenerate the fixture -- a silent drift would otherwise
    make both parity suites agree with each other while disagreeing with the
    real calendars."""
    assert FIXTURE_DATA["holidays_nl"] == config.SCHOOL_HOLIDAYS_NL, (
        "bucket_cases.json's holidays_nl is stale -- regenerate the fixture "
        "(see test_bucket_parity.py's module docstring)"
    )
    assert FIXTURE_DATA["holidays_fr"] == config.SCHOOL_HOLIDAYS_FR, (
        "bucket_cases.json's holidays_fr is stale -- regenerate the fixture "
        "(see test_bucket_parity.py's module docstring)"
    )
