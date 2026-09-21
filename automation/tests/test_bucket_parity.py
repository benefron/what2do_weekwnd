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

Regenerating the fixture (Python is the source of truth: run this snippet,
hand-check a handful of cases, then re-run both suites):

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

    # then update fixtures/bucket_cases.json's "expected" for each case with
    # bucketize(case["today"], case["activity"]).

The full generator used to build the current fixture lives in this PR's
scratch history; the one-liner above is enough to regenerate any single case
by hand after editing `config.SCHOOL_HOLIDAYS_NL` / `_FR` or `_bucketize`
itself.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import normalize

FIXTURE = Path(__file__).parent / "fixtures" / "bucket_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _run(case):
    today = date.fromisoformat(case["today"])
    window_end = today + timedelta(weeks=13)  # config.WINDOW_WEEKS
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
