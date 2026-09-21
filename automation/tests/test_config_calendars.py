"""Pins the school-holiday calendars in config.py: structural sanity (sorted,
non-overlapping, every school year fully covered) plus the lapse guard that
this file exists to protect — SCHOOL_HOLIDAYS_NL / SCHOOL_HOLIDAYS_FR are
hardcoded (see config.py's "Update yearly" comment) and nothing else notices
when they run out; a run just silently stops setting holiday flags.
"""
from datetime import date, timedelta

import config
from normalize import calendars_lapse_warning

def _spans(table):
    return [(date.fromisoformat(h["start"]), date.fromisoformat(h["end"])) for h in table]


def _check_sorted_and_non_overlapping(table):
    spans = _spans(table)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert s1 <= s2, f"{table} not sorted: {s1} comes before {s2} out of order"
        assert e1 < s2, f"overlap in {table}: ({s1}, {e1}) overlaps ({s2}, {e2})"
    for s, e in spans:
        assert s <= e, f"start after end: {s} > {e}"


def test_nl_calendar_sorted_and_non_overlapping():
    _check_sorted_and_non_overlapping(config.SCHOOL_HOLIDAYS_NL)


def test_fr_calendar_sorted_and_non_overlapping():
    _check_sorted_and_non_overlapping(config.SCHOOL_HOLIDAYS_FR)


def _check_every_school_year_has_all_periods(table, summer_name, other_names):
    # The table is a flat list bracketed by summers: summer, [4 periods],
    # summer, [4 periods], summer, ... So between every consecutive pair of
    # summer entries there must be exactly one of each of the other four
    # periods, in some order, and nothing else.
    summer_idxs = [i for i, h in enumerate(table) if h["name"] == summer_name]
    assert len(summer_idxs) >= 4, f"expected at least 4 school years, got {len(summer_idxs)}"
    for start_i, end_i in zip(summer_idxs, summer_idxs[1:]):
        between = [h["name"] for h in table[start_i + 1 : end_i]]
        assert sorted(between) == sorted(other_names), (
            f"school year between index {start_i} and {end_i} is missing a period "
            f"or has an unexpected one: {between}"
        )


def test_nl_calendar_has_five_periods_every_school_year():
    _check_every_school_year_has_all_periods(
        config.SCHOOL_HOLIDAYS_NL,
        "zomervakantie",
        ["herfstvakantie", "kerstvakantie", "krokusvakantie", "paasvakantie"],
    )


def test_fr_calendar_has_five_periods_every_school_year():
    _check_every_school_year_has_all_periods(
        config.SCHOOL_HOLIDAYS_FR,
        "grandes vacances",
        ["conge d'automne", "vacances d'hiver", "conge de detente", "vacances de printemps"],
    )


def test_nl_and_fr_summers_differ():
    # Pins the "education is a community competence" fact this repo's docs call
    # out repeatedly: FWB moved to a shorter, differently-timed summer in
    # 2022-23, so the two calendars' zomervakantie/grandes vacances spans for
    # the same summer must NOT be identical.
    nl_summers = [h for h in config.SCHOOL_HOLIDAYS_NL if h["name"] == "zomervakantie"]
    fr_summers = [h for h in config.SCHOOL_HOLIDAYS_FR if h["name"] == "grandes vacances"]
    assert len(nl_summers) == len(fr_summers) >= 3
    for nl, fr in zip(nl_summers, fr_summers):
        assert (nl["start"], nl["end"]) != (fr["start"], fr["end"]), (
            f"NL and FR summers coincide for {nl}/{fr} — they should not, per "
            "FWB's 2022-23 rhythm change"
        )


def test_calendars_extend_at_least_a_year_out():
    # This is the one test in the suite that is deliberately NOT deterministic
    # against a fixed date: its entire job is to fail in CI roughly a year
    # before either calendar actually lapses, which only works if it checks
    # against the real "today" rather than a frozen one. Every other test here
    # (and calendars_lapse_warning's own tests) pins fixed dates as usual.
    today = date.today()
    for label, table in (("NL", config.SCHOOL_HOLIDAYS_NL), ("FR", config.SCHOOL_HOLIDAYS_FR)):
        last_end = max(date.fromisoformat(h["end"]) for h in table)
        months_left = (last_end.year - today.year) * 12 + (last_end.month - today.month)
        assert months_left >= 12, (
            f"SCHOOL_HOLIDAYS_{label} only extends to {last_end.isoformat()} — "
            "fewer than 12 months of runway left. Extend it (see config.py's "
            "'Update yearly' comment)."
        )


def test_lapse_warning_none_when_far_from_expiry():
    # Both tables currently extend to 2029; a "today" of 2026 is well clear of
    # the 6-month guard.
    assert calendars_lapse_warning(date(2026, 9, 21)) is None


def test_lapse_warning_fires_within_six_months_of_either_table():
    nl_end = max(date.fromisoformat(h["end"]) for h in config.SCHOOL_HOLIDAYS_NL)
    fr_end = max(date.fromisoformat(h["end"]) for h in config.SCHOOL_HOLIDAYS_FR)
    last_end = min(nl_end, fr_end)
    # 5 months before whichever calendar ends first is inside the 6-month window.
    almost_lapsed = date(last_end.year, last_end.month, 1) - _months(5)
    msg = calendars_lapse_warning(almost_lapsed)
    assert msg is not None
    assert "SCHOOL_HOLIDAYS_*" in msg


def test_lapse_warning_fires_after_expiry():
    nl_end = max(date.fromisoformat(h["end"]) for h in config.SCHOOL_HOLIDAYS_NL)
    fr_end = max(date.fromisoformat(h["end"]) for h in config.SCHOOL_HOLIDAYS_FR)
    last_end = max(nl_end, fr_end)
    msg = calendars_lapse_warning(last_end + timedelta(days=1))
    assert msg is not None


def _months(n):
    return timedelta(days=30 * n)
