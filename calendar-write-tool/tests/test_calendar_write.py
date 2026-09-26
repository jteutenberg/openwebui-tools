import ast
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from household_calendar_write import (
    CalendarWriteError,
    add_event,
    duplicate_event,
    list_events,
    main,
    to_ns,
)

UK = ZoneInfo("Europe/London")
TODAY = date(2026, 9, 26)

SCHEMA = """
CREATE TABLE calendar (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT,
    is_default BOOLEAN NOT NULL,
    data JSON,
    meta JSON,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE TABLE calendar_event (
    id TEXT PRIMARY KEY,
    calendar_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    start_at BIGINT NOT NULL,
    end_at BIGINT,
    all_day BOOLEAN NOT NULL,
    rrule TEXT,
    color TEXT,
    location TEXT,
    data JSON,
    meta JSON,
    is_cancelled BOOLEAN NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
"""


def make_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        """
        INSERT INTO calendar
            (id, user_id, name, color, is_default, data, meta, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, NULL, NULL, 0, 0)
        """,
        [("fam", "u1", "Family", 0), ("per", "u1", "Personal", 1)],
    )
    conn.commit()
    conn.close()
    return path


def insert_event(
    path: Path,
    event_id: str,
    title: str,
    start: datetime,
    *,
    calendar_id: str = "fam",
    description: str | None = None,
    end: datetime | None = None,
    all_day: bool = False,
    rrule: str | None = None,
    location: str | None = None,
    meta: str | None = None,
    cancelled: bool = False,
    user_id: str = "u1",
) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO calendar_event (
            id, calendar_id, user_id, title, description, start_at, end_at,
            all_day, rrule, color, location, data, meta, is_cancelled,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, 0, 0)
        """,
        (
            event_id,
            calendar_id,
            user_id,
            title,
            description,
            to_ns(start),
            to_ns(end) if end is not None else None,
            int(all_day),
            rrule,
            location,
            meta,
            int(cancelled),
        ),
    )
    conn.commit()
    conn.close()


def lines(path: Path, pattern: str | None = None, today: date = TODAY) -> list[str]:
    return [event.format() for event in list_events(path, pattern, today)]


def test_list_shows_upcoming_household_events_in_order(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    insert_event(db, "past", "Already happened", datetime(2026, 9, 25, 9, 0, tzinfo=UK))
    insert_event(
        db,
        "later",
        "Bob Wind Band",
        datetime(2026, 9, 26, 19, 0, tzinfo=UK),
        end=datetime(2026, 9, 26, 20, 0, tzinfo=UK),
    )
    insert_event(db, "early", "Bob rugby", datetime(2026, 9, 26, 14, 0, tzinfo=UK))
    insert_event(
        db,
        "utc-evening",
        "Late kickoff",
        datetime(2026, 9, 25, 23, 30, tzinfo=ZoneInfo("UTC")),
    )
    insert_event(db, "recur", "SWATA social tennis", datetime(2026, 9, 27, 9, 0, tzinfo=UK), rrule="FREQ=WEEKLY")
    insert_event(db, "personal", "Dentist", datetime(2026, 9, 26, 11, 0, tzinfo=UK), calendar_id="per")
    insert_event(db, "cancelled", "Cancelled picnic", datetime(2026, 9, 26, 12, 0, tzinfo=UK), cancelled=True)
    insert_event(
        db,
        "allday",
        "School term begins",
        datetime(2026, 9, 28, 0, 0, tzinfo=UK),
        all_day=True,
    )

    assert lines(db) == [
        "2026-09-26 Late kickoff",
        "2026-09-26 Bob rugby",
        "2026-09-26 Bob Wind Band",
        "2026-09-28 School term begins",
    ]


def test_list_regex_matches_description_without_printing_it(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    insert_event(
        db,
        "lesson",
        "Alice music lesson",
        datetime(2026, 9, 28, 19, 50, tzinfo=UK),
        description="Extra bassoon lesson",
    )
    insert_event(db, "rugby", "Bob rugby", datetime(2026, 9, 26, 14, 0, tzinfo=UK))

    assert lines(db, "BASSOON") == ["2026-09-28 Alice music lesson"]
    assert lines(db, "rugby") == ["2026-09-26 Bob rugby"]
    assert lines(db, "swimming") == []
    with pytest.raises(CalendarWriteError, match="Invalid regex"):
        list_events(db, "[", TODAY)


def test_duplicate_weekly_keeps_uk_clock_time_across_dst(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    start = datetime(2026, 10, 24, 9, 0, tzinfo=UK)
    end = datetime(2026, 10, 24, 10, 30, tzinfo=UK)
    insert_event(
        db,
        "tennis",
        "Alice tennis",
        start,
        end=end,
        description="Alice's tennis training",
        location="Peppard",
        meta='{"alert_minutes": -1}',
        user_id="jono",
    )

    created = duplicate_event(db, date(2026, 10, 24), "Alice tennis", 1, "weekly", today=date(2026, 10, 1))

    assert len(created) == 1
    assert created[0].format() == "2026-10-31 Alice tennis"
    assert created[0].start == datetime(2026, 10, 31, 9, 0, tzinfo=UK)
    assert created[0].end == datetime(2026, 10, 31, 10, 30, tzinfo=UK)
    assert created[0].start.utcoffset() != start.utcoffset()

    listed = list_events(db, "tennis training", date(2026, 10, 1))
    assert [event.format() for event in listed] == [
        "2026-10-24 Alice tennis",
        "2026-10-31 Alice tennis",
    ]

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    copy = conn.execute(
        "SELECT * FROM calendar_event WHERE id != ?",
        ("tennis",),
    ).fetchone()
    original = conn.execute("SELECT * FROM calendar_event WHERE id = ?", ("tennis",)).fetchone()
    assert copy["rrule"] is None
    assert copy["title"] == "Alice tennis"
    assert copy["description"] == "Alice's tennis training"
    assert copy["location"] == "Peppard"
    assert copy["meta"] == '{"alert_minutes": -1}'
    assert copy["calendar_id"] == "fam"
    assert copy["user_id"] == "jono"
    assert copy["all_day"] == 0
    assert original["start_at"] == to_ns(start)
    assert original["end_at"] == to_ns(end)
    conn.close()


def test_duplicate_daily_fortnightly_and_all_day(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    insert_event(db, "band", "Bob Big Band", datetime(2026, 9, 28, 13, 30, tzinfo=UK))
    insert_event(
        db,
        "term",
        "School term begins",
        datetime(2026, 9, 28, 0, 0, tzinfo=UK),
        all_day=True,
    )

    daily = duplicate_event(db, "2026-09-28", "Bob Big Band", 2, "daily", today=TODAY)
    fortnight = duplicate_event(db, "2026-09-28", "School term begins", 1, "fortnightly", today=TODAY)

    assert [event.format() for event in daily] == [
        "2026-09-29 Bob Big Band",
        "2026-09-30 Bob Big Band",
    ]
    assert daily[0].start == datetime(2026, 9, 29, 13, 30, tzinfo=UK)
    assert fortnight[0].format() == "2026-10-12 School term begins"
    assert fortnight[0].all_day is True
    assert fortnight[0].start == datetime(2026, 10, 12, 0, 0, tzinfo=UK)


def test_duplicate_rejects_bad_arguments_and_unknown_events(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    insert_event(db, "rugby", "Bob rugby", datetime(2026, 9, 26, 14, 0, tzinfo=UK))
    insert_event(db, "past", "Old match", datetime(2026, 9, 20, 14, 0, tzinfo=UK))

    with pytest.raises(CalendarWriteError, match="1 to 16"):
        duplicate_event(db, TODAY, "Bob rugby", 0, "weekly", today=TODAY)
    with pytest.raises(CalendarWriteError, match="1 to 16"):
        duplicate_event(db, TODAY, "Bob rugby", 17, "weekly", today=TODAY)
    with pytest.raises(CalendarWriteError, match="Period must be"):
        duplicate_event(db, TODAY, "Bob rugby", 1, "monthly", today=TODAY)
    with pytest.raises(CalendarWriteError, match="Date must look like"):
        duplicate_event(db, "26 September", "Bob rugby", 1, "weekly", today=TODAY)
    with pytest.raises(CalendarWriteError, match="No event named 'Missing'"):
        duplicate_event(db, TODAY, "Missing", 1, "daily", today=TODAY)
    with pytest.raises(CalendarWriteError, match="No event named 'Old match'"):
        duplicate_event(db, date(2026, 9, 20), "Old match", 1, "daily", today=TODAY)

    sixteen = duplicate_event(db, TODAY, "Bob rugby", 16, "weekly", today=TODAY)
    assert len(sixteen) == 16
    assert sixteen[-1].on == date(2027, 1, 16)


def test_same_name_and_date_copies_one_row(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    when = datetime(2026, 10, 5, 14, 30, tzinfo=UK)
    insert_event(db, "a", "Bob rugby", when, description="First stored")
    insert_event(db, "b", "Bob rugby", when, description="Second stored")

    created = duplicate_event(db, date(2026, 10, 5), "Bob rugby", 2, "weekly", today=date(2026, 10, 1))

    assert [event.format() for event in created] == [
        "2026-10-12 Bob rugby",
        "2026-10-19 Bob rugby",
    ]
    assert [event.description for event in created] == ["First stored", "First stored"]
    conn = sqlite3.connect(db)
    stored = [
        row[0]
        for row in conn.execute(
            "SELECT description FROM calendar_event WHERE id NOT IN ('a', 'b') ORDER BY start_at"
        )
    ]
    assert stored == ["First stored", "First stored"]
    assert conn.execute("SELECT count(*) FROM calendar_event").fetchone()[0] == 4
    conn.close()


def test_cli_list_and_duplicate(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "household_calendar_write.now_uk",
        lambda now=None: datetime(2026, 9, 26, 8, 0, tzinfo=UK),
    )
    db = make_db(tmp_path / "webui.db")
    insert_event(
        db,
        "rugby",
        "Bob rugby",
        datetime(2026, 9, 26, 14, 0, tzinfo=UK),
        description="Away at Dragon",
    )
    insert_event(db, "band", "Bob Wind Band", datetime(2026, 9, 26, 19, 0, tzinfo=UK))

    assert main(["--db", str(db), "list", "dragon"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "2026-09-26 Bob rugby\n"
    assert captured.err == ""

    assert main(["--db", str(db), "duplicate", "2026-09-26", "Bob rugby", "2", "Weekly"]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "Created 2 events:\n"
        "2026-10-03 Bob rugby\n"
        "2026-10-10 Bob rugby\n"
    )
    assert captured.err == ""

    assert main(["--db", str(db), "duplicate", "2026-09-26", "Missing", "1", "daily"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No event named 'Missing'" in captured.err


def _rows(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM calendar_event ORDER BY start_at, title, id"))
    conn.close()
    return rows


def test_add_event_names_one_person_and_stores_a_timed_event(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    created = add_event(
        db,
        "Bob",
        "rugby",
        "2026-10-05 14:00",
        "2026-10-05 16:00",
        location="School",
        description="Away at Dragon",
        today=TODAY,
        user_id="jono",
    )
    assert [event.format() for event in created] == ["2026-10-05 Bob rugby"]
    row = _rows(db)[0]
    assert row["title"] == "Bob rugby"
    assert row["description"] == "Away at Dragon"
    assert row["location"] == "School"
    assert row["all_day"] == 0
    assert row["rrule"] is None
    assert row["user_id"] == "jono"
    assert row["calendar_id"] == "fam"
    assert from_ns_row(row["start_at"]) == datetime(2026, 10, 5, 14, 0, tzinfo=UK)
    assert from_ns_row(row["end_at"]) == datetime(2026, 10, 5, 16, 0, tzinfo=UK)
    assert list_events(db, today=TODAY)[-1].format() == "2026-10-05 Bob rugby"


def test_add_event_keeps_a_name_that_already_starts_with_the_person(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    created = add_event(db, "marcus", "Bob rugby", "2026-10-05 14:00", "2026-10-05 16:00", today=TODAY)
    assert created[0].title == "Bob rugby"


def test_same_start_and_end_time_is_all_day(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    created = add_event(db, "Alice", "enrichment day", "2026-10-06 09:00", "2026-10-06 09:00", today=TODAY)
    assert created[0].format() == "2026-10-06 Alice enrichment day"
    assert created[0].all_day is True
    row = _rows(db)[0]
    assert row["all_day"] == 1
    assert from_ns_row(row["start_at"]) == datetime(2026, 10, 6, 0, 0, tzinfo=UK)
    assert from_ns_row(row["end_at"]) == datetime(2026, 10, 6, 23, 59, tzinfo=UK)


def test_several_days_are_split_and_names_go_in_the_description(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    created = add_event(
        db,
        "Bob and Alice",
        "tennis trip",
        "2026-10-05 14:00",
        "2026-10-07 11:00",
        description="Bring kit",
        today=TODAY,
    )
    assert [event.format() for event in created] == [
        "2026-10-05 tennis trip",
        "2026-10-06 tennis trip",
        "2026-10-07 tennis trip",
    ]
    assert created[0].description == "Bring kit (Bob and Alice)"
    rows = _rows(db)
    assert [(from_ns_row(row["start_at"]), from_ns_row(row["end_at"]), row["all_day"]) for row in rows] == [
        (datetime(2026, 10, 5, 14, 0, tzinfo=UK), datetime(2026, 10, 5, 23, 59, tzinfo=UK), 0),
        (datetime(2026, 10, 6, 0, 0, tzinfo=UK), datetime(2026, 10, 6, 23, 59, tzinfo=UK), 1),
        (datetime(2026, 10, 7, 0, 0, tzinfo=UK), datetime(2026, 10, 7, 11, 0, tzinfo=UK), 0),
    ]
    assert rows[0]["user_id"] == "u1"


def test_description_keeps_names_already_written(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    created = add_event(
        db,
        ["Alice", "Bob"],
        "school term begins",
        "2026-10-10 00:00",
        "2026-10-10 18:00",
        description="First day for Alice and Bob",
        today=TODAY,
    )
    assert created[0].title == "school term begins"
    assert created[0].description == "First day for Alice and Bob"


def test_add_event_rejects_past_and_far_future_without_writing(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    with pytest.raises(CalendarWriteError, match="2026-09-25 is in the past"):
        add_event(db, "Bob", "rugby", "2026-09-25 14:00", "2026-09-25 16:00", today=TODAY)
    with pytest.raises(CalendarWriteError, match="2027-09-27 is more than one year ahead"):
        add_event(db, "Bob", "rugby", "2027-09-27 14:00", "2027-09-27 16:00", today=TODAY)
    with pytest.raises(CalendarWriteError, match="more than one year ahead"):
        add_event(
            db,
            "Bob",
            "rugby",
            "2027-08-15 14:00",
            "2027-08-15 16:00",
            repeat="weekly",
            today=TODAY,
        )
    assert _rows(db) == []

    created = add_event(db, "Bob", "rugby", "2027-09-26 10:00", "2027-09-26 12:00", today=TODAY)
    assert created[0].on == date(2027, 9, 26)


def test_add_event_rejects_bad_input(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    with pytest.raises(CalendarWriteError, match="Dates must look like"):
        add_event(db, "Bob", "rugby", "5 October 2026 2pm", "2026-10-05 16:00", today=TODAY)
    with pytest.raises(CalendarWriteError, match="End is before the start"):
        add_event(db, "Bob", "rugby", "2026-10-05 16:00", "2026-10-05 14:00", today=TODAY)
    with pytest.raises(CalendarWriteError, match="Say who it is for"):
        add_event(db, "  ", "rugby", "2026-10-05 14:00", "2026-10-05 16:00", today=TODAY)
    with pytest.raises(CalendarWriteError, match="Repeat must be"):
        add_event(db, "Bob", "rugby", "2026-10-05 14:00", "2026-10-05 16:00", repeat="monthly", today=TODAY)
    assert _rows(db) == []


def test_repeat_for_eight_weeks(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    weekly = add_event(
        db,
        "Bob",
        "rugby",
        "2026-10-05 14:00",
        "2026-10-05 16:00",
        repeat="Weekly",
        today=TODAY,
    )
    assert len(weekly) == 8
    assert weekly[0].start == datetime(2026, 10, 5, 14, 0, tzinfo=UK)
    assert weekly[3].start == datetime(2026, 10, 26, 14, 0, tzinfo=UK)
    assert weekly[3].start.utcoffset() != weekly[0].start.utcoffset()
    assert weekly[-1].on == date(2026, 11, 23)

    fortnight = add_event(
        db,
        "Alice",
        "rowing",
        "2026-10-06 09:00",
        "2026-10-06 12:00",
        repeat="fortnightly",
        today=TODAY,
    )
    assert [event.on for event in fortnight] == [
        date(2026, 10, 6),
        date(2026, 10, 20),
        date(2026, 11, 3),
        date(2026, 11, 17),
    ]

    daily = add_event(db, "Carl", "swim", "2026-10-05 19:00", "2026-10-05 20:00", repeat="daily", today=TODAY)
    assert len(daily) == 56
    assert daily[-1].on == date(2026, 11, 29)


def test_multi_day_repeat_copies_each_day(tmp_path: Path):
    db = make_db(tmp_path / "webui.db")
    created = add_event(
        db,
        "Alice",
        "rowing camp",
        "2026-10-05 09:00",
        "2026-10-06 15:00",
        repeat="weekly",
        today=TODAY,
    )
    assert len(created) == 16
    assert [event.on for event in created[:4]] == [
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 12),
        date(2026, 10, 13),
    ]
    assert created[0].all_day is False
    assert created[1].start == datetime(2026, 10, 6, 0, 0, tzinfo=UK)
    assert created[1].end == datetime(2026, 10, 6, 15, 0, tzinfo=UK)


def test_openwebui_wrapper_has_one_add_command():
    source = (Path(__file__).resolve().parents[1] / "household_calendar_add.py").read_text()
    tree = ast.parse(source)
    methods = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Tools":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    methods.append(item.name)
    assert methods == ["add_event"]
    assert "2026-10-05 14:00" in source


def from_ns_row(ns: int) -> datetime:
    from household_calendar_write import from_ns

    return from_ns(ns)
