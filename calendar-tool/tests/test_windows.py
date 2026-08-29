from datetime import date, datetime

from household_calendar import (
    TermDates,
    WindowError,
    format_occurrence,
    matches_who,
    parse_when,
    resolve_window,
    split_who,
    still_upcoming,
)

NOW = datetime.fromisoformat("2026-08-29T10:46:00+01:00")
TERM = TermDates(
    term_start=date(2026, 9, 1),
    term_end=date(2026, 12, 18),
    half_term_start=date(2026, 10, 19),
    half_term_end=date(2026, 10, 27),
)


def _span(phrase: str) -> tuple[str, str]:
    start, end = parse_when(phrase, NOW, TERM)
    return start.isoformat(), end.isoformat()


def test_today_and_tomorrow():
    assert _span("today")[0].startswith("2026-08-29T00:00:00")
    assert _span("tomorrow")[0].startswith("2026-08-30T00:00:00")


def test_this_week_is_monday_to_sunday():
    start, end = _span("this week")
    assert start.startswith("2026-08-24T00:00:00")
    assert end.startswith("2026-08-30T23:59:59")


def test_this_weekend_is_saturday_sunday():
    start, end = _span("this weekend")
    assert start.startswith("2026-08-29T00:00:00")
    assert end.startswith("2026-08-30T23:59:59")


def test_tuesday_is_next_tuesday():
    start, _end = _span("Tuesday")
    assert start.startswith("2026-09-01T00:00:00")


def test_saturday_is_today_when_today_is_saturday():
    start, _end = _span("Saturday")
    assert start.startswith("2026-08-29T00:00:00")


def test_term_phrases():
    this_term = _span("this term")
    assert this_term[0].startswith("2026-09-01T00:00:00")
    assert this_term[1].startswith("2026-12-18T23:59:59")

    after_half = _span("after half term")
    assert after_half[0].startswith("2026-10-28T00:00:00")
    assert after_half[1].startswith("2026-12-18T23:59:59")

    after_term = _span("after this term")
    assert after_term[0].startswith("2026-12-19T00:00:00")
    assert after_term[1].startswith("2027-02-12T23:59:59")


def test_iso_and_named_dates():
    assert _span("2026-09-03")[0].startswith("2026-09-03T00:00:00")
    assert _span("3 September")[0].startswith("2026-09-03T00:00:00")


def test_unknown_phrase():
    try:
        parse_when("in two moons", NOW, TERM)
        assert False, "expected WindowError"
    except WindowError as exc:
        assert "unknown time" in str(exc)


def test_missing_term_valves():
    empty = TermDates(None, None, None, None)
    try:
        parse_when("this term", NOW, empty)
        assert False, "expected WindowError"
    except WindowError as exc:
        assert "term_start" in str(exc)


def test_default_windows():
    start, end = resolve_window("", NOW, TERM, "eight_weeks")
    assert start == NOW
    assert (end - start).days == 56

    start, end = resolve_window("", NOW, TERM, "this week")
    assert start.isoformat().startswith("2026-08-24T00:00:00")


def test_who_union_and_split():
    assert split_who("Tom and Mum") == ["Tom", "Mum"]
    assert split_who("Tom, Mum") == ["Tom", "Mum"]
    title = "Swimming gala"
    description = "Tom Mum"
    assert matches_who(title, description, ["Tom"])
    assert matches_who(title, description, ["Tom", "Sarah"])  # union: Tom matches
    assert not matches_who("Sarah swimming", "Sarah", ["Tom"])


def test_ended_events_are_dropped():
    start = datetime.fromisoformat("2026-08-29T09:00:00+01:00")
    end = datetime.fromisoformat("2026-08-29T10:00:00+01:00")
    assert still_upcoming(start, end, False, NOW) is False
    later = datetime.fromisoformat("2026-08-29T16:00:00+01:00")
    assert still_upcoming(later, later.replace(hour=17), False, NOW) is True
    all_day = datetime.fromisoformat("2026-08-29T00:00:00+01:00")
    assert still_upcoming(all_day, None, True, NOW) is True


def test_format_lines():
    start = datetime.fromisoformat("2026-09-03T16:00:00+01:00")
    end = datetime.fromisoformat("2026-09-03T17:00:00+01:00")
    assert format_occurrence("Tom swimming", start, end, False) == "2026-09-03 16:00-17:00 Tom swimming"
    assert format_occurrence("BBQ", start, None, True) == "2026-09-03 all-day BBQ"
