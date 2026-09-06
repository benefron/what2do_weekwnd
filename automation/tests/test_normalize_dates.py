"""Weekend windows, school-holiday flags and bucketing.

Two real bugs came out of this file's logic, so it gets the most attention:
`this_weekend` pointing at the weekend that had already ended on Saturdays, and
a single Flemish holiday table being applied to the whole country.
"""
from datetime import date, timedelta

import pytest

import config
import normalize


# ── _weekend_windows ────────────────────────────────────────────────────────
@pytest.mark.parametrize("today,expected_saturday", [
    (date(2026, 10, 12), date(2026, 10, 17)),  # Mon -> upcoming Sat
    (date(2026, 10, 13), date(2026, 10, 17)),  # Tue
    (date(2026, 10, 14), date(2026, 10, 17)),  # Wed
    (date(2026, 10, 15), date(2026, 10, 17)),  # Thu
    (date(2026, 10, 16), date(2026, 10, 17)),  # Fri
    (date(2026, 10, 17), date(2026, 10, 17)),  # Sat -> itself, NOT last week
    (date(2026, 10, 18), date(2026, 10, 17)),  # Sun -> the Saturday just gone
])
def test_weekend_window_saturday(today, expected_saturday):
    this_wknd, _ = normalize._weekend_windows(today)
    assert min(this_wknd) == expected_saturday


@pytest.mark.parametrize("offset", range(14))
def test_this_weekend_is_never_in_the_past(offset):
    """Regression: on Saturdays the window stepped back a week, so the app's
    primary chip offered a weekend that had already finished."""
    today = date(2026, 10, 12) + timedelta(days=offset)
    this_wknd, _ = normalize._weekend_windows(today)
    assert max(this_wknd) >= today, f"{today:%a %Y-%m-%d} offered a past weekend"


@pytest.mark.parametrize("offset", range(14))
def test_next_weekend_follows_this_weekend(offset):
    today = date(2026, 10, 12) + timedelta(days=offset)
    this_wknd, next_wknd = normalize._weekend_windows(today)
    assert min(next_wknd) == min(this_wknd) + timedelta(days=7)
    assert len(this_wknd) == len(next_wknd) == 2


# ── the two school calendars ────────────────────────────────────────────────
def test_both_calendars_are_populated():
    assert config.SCHOOL_HOLIDAYS_NL
    assert config.SCHOOL_HOLIDAYS_FR


@pytest.mark.parametrize("table_name", ["SCHOOL_HOLIDAYS_NL", "SCHOOL_HOLIDAYS_FR"])
def test_holiday_entries_are_well_formed(table_name):
    for h in getattr(config, table_name):
        start, end = date.fromisoformat(h["start"]), date.fromisoformat(h["end"])
        assert start <= end, f"{table_name} {h['name']} ends before it starts"
        assert h["name"].strip()


@pytest.mark.parametrize("table_name", ["SCHOOL_HOLIDAYS_NL", "SCHOOL_HOLIDAYS_FR"])
def test_holidays_do_not_overlap_within_one_calendar(table_name):
    spans = sorted(
        (date.fromisoformat(h["start"]), date.fromisoformat(h["end"]), h["name"])
        for h in getattr(config, table_name)
    )
    for (a_start, a_end, a_name), (b_start, b_end, b_name) in zip(spans, spans[1:]):
        assert a_end < b_start, f"{table_name}: {a_name} overlaps {b_name}"


def test_the_calendars_actually_differ():
    """If these ever coincide the whole three-flag design is pointless — most
    likely someone copy-pasted the Flemish table into the FWB one."""
    nl = {(h["start"], h["end"]) for h in config.SCHOOL_HOLIDAYS_NL}
    fr = {(h["start"], h["end"]) for h in config.SCHOOL_HOLIDAYS_FR}
    assert nl != fr


def test_fwb_autumn_week_is_not_a_flemish_holiday():
    """20 Oct 2026 is the FWB congé d'automne; Flanders is still in class."""
    d = date(2026, 10, 20)
    assert normalize._holiday_in(config.SCHOOL_HOLIDAYS_FR, d)
    assert normalize._holiday_in(config.SCHOOL_HOLIDAYS_NL, d) is None


def test_flemish_herfstvakantie_is_shared():
    d = date(2026, 10, 28)
    assert normalize._holiday_in(config.SCHOOL_HOLIDAYS_NL, d)
    assert normalize._holiday_in(config.SCHOOL_HOLIDAYS_FR, d)


def test_holiday_for_uses_the_flemish_table():
    assert normalize._holiday_for(date(2026, 10, 28)) == "herfstvakantie"
    assert normalize._holiday_for(date(2026, 10, 20)) is None


# ── _bucketize ──────────────────────────────────────────────────────────────
TODAY = date(2026, 10, 12)          # a Monday
WINDOW_END = TODAY + timedelta(weeks=12)


def bucketize(act):
    normalize._bucketize(act, TODAY, WINDOW_END)
    return act


def test_permanent_places_get_later_and_no_holiday(make_activity):
    act = bucketize(make_activity(date_kind="permanent", occurrences=[],
                                  date_start=None, date_end=None))
    assert act["weekend_bucket"] == ["later"]
    assert act["in_school_holiday"] is False
    assert act["school_holiday_nl"] is None
    assert act["school_holiday_fr"] is None


def test_fwb_only_week_sets_fr_flag_only(make_activity):
    act = bucketize(make_activity(
        date_start="2026-10-20T10:00:00", date_end="2026-10-20T12:00:00",
        occurrences=[{"start": "2026-10-20T10:00:00"}]))
    assert act["school_holiday_nl"] is None
    assert act["school_holiday_fr"] == "conge d'automne"
    assert act["in_school_holiday"] is True      # Brussels sees the union
    assert "school_holiday" in act["weekend_bucket"]


def test_shared_holiday_week_sets_both_flags(make_activity):
    act = bucketize(make_activity(
        date_start="2026-10-28T10:00:00", date_end="2026-10-28T12:00:00",
        occurrences=[{"start": "2026-10-28T10:00:00"}]))
    assert act["school_holiday_nl"] == "herfstvakantie"
    assert act["school_holiday_fr"] == "conge d'automne"


def test_term_time_event_has_no_holiday_flags(make_activity):
    act = bucketize(make_activity(
        date_start="2026-11-10T10:00:00", date_end="2026-11-10T12:00:00",
        occurrences=[{"start": "2026-11-10T10:00:00"}]))
    assert act["school_holiday_nl"] is None
    assert act["school_holiday_fr"] is None
    assert act["in_school_holiday"] is False
    assert "school_holiday" not in act["weekend_bucket"]


def test_in_school_holiday_is_the_union(make_activity):
    """Brussels has no calendar of its own, so either community's break counts."""
    fr_only = bucketize(make_activity(
        date_start="2026-10-20T10:00:00", date_end="2026-10-20T12:00:00",
        occurrences=[{"start": "2026-10-20T10:00:00"}]))
    assert fr_only["in_school_holiday"] is True
    assert fr_only["school_holiday_name"] == fr_only["school_holiday_fr"]


def test_this_and_next_weekend_buckets(make_activity):
    this_sat = bucketize(make_activity(
        date_start="2026-10-17T10:00:00", date_end="2026-10-17T12:00:00",
        occurrences=[{"start": "2026-10-17T10:00:00"}]))
    assert "this_weekend" in this_sat["weekend_bucket"]

    next_sun = bucketize(make_activity(
        date_start="2026-10-25T10:00:00", date_end="2026-10-25T12:00:00",
        occurrences=[{"start": "2026-10-25T10:00:00"}]))
    assert "next_weekend" in next_sun["weekend_bucket"]


def test_wednesday_bucket_needs_an_afternoon_or_all_day_slot(make_activity):
    """Belgian schools finish at noon on Wednesday, so a 09:00 start is not
    something a school-age child can attend."""
    afternoon = bucketize(make_activity(
        date_start="2026-10-14T14:00:00", date_end="2026-10-14T16:00:00",
        occurrences=[{"start": "2026-10-14T14:00:00"}]))
    assert "wednesday" in afternoon["weekend_bucket"]

    morning = bucketize(make_activity(
        date_start="2026-10-14T09:00:00", date_end="2026-10-14T10:00:00",
        occurrences=[{"start": "2026-10-14T09:00:00"}]))
    assert "wednesday" not in morning["weekend_bucket"]


def test_all_day_wednesday_counts(make_activity):
    act = bucketize(make_activity(
        all_day=True,
        date_start="2026-10-14T09:00:00", date_end="2026-10-14T17:00:00",
        occurrences=[{"start": "2026-10-14T09:00:00"}]))
    assert "wednesday" in act["weekend_bucket"]


def test_multi_day_span_is_bucketed_without_occurrences(make_activity):
    """A run captured only as start+end must still light up every bucket its
    span covers — this is the path that has no occurrences[] to iterate."""
    act = bucketize(make_activity(
        date_kind="multi_day", occurrences=[],
        date_start="2026-10-13T10:00:00", date_end="2026-10-22T18:00:00"))
    assert "wednesday" in act["weekend_bucket"]      # span covers Wed 14 Oct
    assert "this_weekend" in act["weekend_bucket"]   # and Sat/Sun 17-18 Oct
    assert act["school_holiday_fr"] == "conge d'automne"


def test_single_day_wednesday_morning_is_not_a_wednesday_activity(make_activity):
    """Regression: the multi-day span rule fired for single-day events too, so a
    09:00 Wednesday event reached the chip aimed at the school half-day even
    though the occurrence-level check had already rejected it."""
    act = bucketize(make_activity(
        occurrences=[],   # no occurrences: only the span path can bucket this
        date_start="2026-10-14T09:00:00", date_end="2026-10-14T10:00:00"))
    assert "wednesday" not in act["weekend_bucket"]


def test_single_day_wednesday_afternoon_still_counts(make_activity):
    act = bucketize(make_activity(
        occurrences=[],
        date_start="2026-10-14T14:00:00", date_end="2026-10-14T16:00:00"))
    assert "wednesday" in act["weekend_bucket"]


def test_single_day_all_day_wednesday_counts_without_occurrences(make_activity):
    act = bucketize(make_activity(
        occurrences=[], all_day=True,
        date_start="2026-10-14T09:00:00", date_end="2026-10-14T17:00:00"))
    assert "wednesday" in act["weekend_bucket"]


def test_buckets_are_sorted_and_deduplicated(make_activity):
    act = bucketize(make_activity(
        date_kind="multi_day", occurrences=[{"start": "2026-10-17T10:00:00"}],
        date_start="2026-10-17T10:00:00", date_end="2026-10-18T18:00:00"))
    assert act["weekend_bucket"] == sorted(set(act["weekend_bucket"]))


def test_event_outside_the_window_gets_no_later_bucket(make_activity):
    act = bucketize(make_activity(
        date_start="2027-06-01T10:00:00", date_end="2027-06-01T12:00:00",
        occurrences=[{"start": "2027-06-01T10:00:00"}]))
    assert "later" not in act["weekend_bucket"]


def test_past_event_gets_no_later_bucket(make_activity):
    act = bucketize(make_activity(
        date_start="2026-09-01T10:00:00", date_end="2026-09-01T12:00:00",
        occurrences=[{"start": "2026-09-01T10:00:00"}]))
    assert "later" not in act["weekend_bucket"]
