#!/usr/bin/env python3
"""
Write the household calendar in an Open WebUI sqlite database.

Always uses the calendar named Family. Recurring events are omitted.
Each listed line is a UK date and the event name, for example:

    2026-09-26 Bob rugby

Import list_events, duplicate_event, and add_event, or run this file:

    python household_calendar_write.py list [REGEX]
    python household_calendar_write.py duplicate DATE TITLE COUNT daily|weekly|fortnightly

An Open WebUI tool can import those functions, or call this file as a subprocess.
The database defaults to webui.db beside this file, then CALENDAR_DB, then --db.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

UK_TZ = ZoneInfo("Europe/London")
HOUSEHOLD_NAME = "Family"
MAX_DUPLICATIONS = 16
PERIOD_DAYS = {"daily": 1, "weekly": 7, "fortnightly": 14}
EIGHT_WEEKS_DAYS = 56
EXACT_WHEN = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})$")
ALERT_META = '{"alert_minutes": -1}'
DEFAULT_DB = Path(__file__).resolve().parent / "webui.db"


class CalendarWriteError(Exception):
    pass


@dataclass(frozen=True)
class EventLine:
    on: date
    title: str
    start: datetime
    end: Optional[datetime]
    description: Optional[str]
    all_day: bool

    def format(self) -> str:
        return f"{self.on.isoformat()} {self.title}"


def now_uk(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(UK_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=UK_TZ)
    return now.astimezone(UK_TZ)


def from_ns(ns: int) -> datetime:
    seconds, nanos = divmod(int(ns), 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UK_TZ).replace(microsecond=nanos // 1_000)


def to_ns(moment: datetime) -> int:
    local = moment.astimezone(UK_TZ)
    return int(local.timestamp()) * 1_000_000_000 + local.microsecond * 1_000


def add_calendar_days(moment: datetime, days: int) -> datetime:
    local = moment.astimezone(UK_TZ)
    return datetime.combine(local.date() + timedelta(days=days), local.time(), tzinfo=UK_TZ)


def default_db_path() -> Path:
    env = os.environ.get("CALENDAR_DB", "").strip()
    if env:
        return Path(env)
    return DEFAULT_DB


def connect(db_path: Path, *, write: bool) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise CalendarWriteError(f"Database not found: {path}")
    if write:
        conn = sqlite3.connect(path, timeout=10)
    else:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    return conn


def household_calendar(conn: sqlite3.Connection) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT id, user_id FROM calendar WHERE lower(trim(name)) = lower(?)",
        (HOUSEHOLD_NAME,),
    ).fetchall()
    if not rows:
        raise CalendarWriteError(f"Household calendar '{HOUSEHOLD_NAME}' not found.")
    if len(rows) > 1:
        raise CalendarWriteError(f"More than one calendar named '{HOUSEHOLD_NAME}'.")
    return rows[0]


def household_calendar_id(conn: sqlite3.Connection) -> str:
    return household_calendar(conn)["id"]


def _is_recurring(rrule: Optional[str]) -> bool:
    return bool(rrule and rrule.strip())


def load_upcoming(conn: sqlite3.Connection, today: date) -> list[sqlite3.Row]:
    calendar_id = household_calendar_id(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM calendar_event
        WHERE calendar_id = ?
        ORDER BY start_at, title, id
        """,
        (calendar_id,),
    ).fetchall()
    upcoming = []
    for row in rows:
        if row["is_cancelled"]:
            continue
        if _is_recurring(row["rrule"]):
            continue
        if from_ns(row["start_at"]).date() < today:
            continue
        upcoming.append(row)
    return upcoming


def _compile(pattern: Optional[str]) -> Optional[re.Pattern[str]]:
    if pattern is None:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise CalendarWriteError(f"Invalid regex: {exc}") from exc


def _matches(pattern: Optional[re.Pattern[str]], title: str, description: Optional[str]) -> bool:
    if pattern is None:
        return True
    if pattern.search(title):
        return True
    return bool(description and pattern.search(description))


def _line_from_row(row: sqlite3.Row) -> EventLine:
    start = from_ns(row["start_at"])
    end = from_ns(row["end_at"]) if row["end_at"] is not None else None
    return EventLine(
        on=start.date(),
        title=row["title"],
        start=start,
        end=end,
        description=row["description"],
        all_day=bool(row["all_day"]),
    )


def list_events(
    db_path: Path,
    pattern: Optional[str] = None,
    today: Optional[date] = None,
) -> list[EventLine]:
    today = today or now_uk().date()
    compiled = _compile(pattern)
    with closing(connect(db_path, write=False)) as conn:
        rows = load_upcoming(conn, today)
    return [
        _line_from_row(row)
        for row in rows
        if _matches(compiled, row["title"], row["description"])
    ]


def list_text(
    db_path: Path,
    pattern: Optional[str] = None,
    today: Optional[date] = None,
) -> str:
    return "\n".join(event.format() for event in list_events(db_path, pattern, today))


def _parse_on(on: date | str) -> date:
    if isinstance(on, datetime):
        raise CalendarWriteError("Date must look like 2026-09-26, as printed by list.")
    if isinstance(on, date):
        return on
    text = str(on).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise CalendarWriteError("Date must look like 2026-09-26, as printed by list.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CalendarWriteError("Date must look like 2026-09-26, as printed by list.") from exc


def _parse_count(count: int) -> int:
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_DUPLICATIONS:
        raise CalendarWriteError(f"Count must be from 1 to {MAX_DUPLICATIONS}.")
    return count


def _parse_period(period: str) -> str:
    text = (period or "").strip().lower()
    if text not in PERIOD_DAYS:
        raise CalendarWriteError("Period must be daily, weekly, or fortnightly.")
    return text


def _insert_copy(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    start_ns: int,
    end_ns: Optional[int],
    now_ns: int,
) -> None:
    row = dict(source)
    row["id"] = str(uuid.uuid4())
    row["start_at"] = start_ns
    row["end_at"] = end_ns
    row["rrule"] = None
    row["created_at"] = now_ns
    row["updated_at"] = now_ns
    columns = list(row.keys())
    names = ",".join(f'"{column}"' for column in columns)
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO calendar_event ({names}) VALUES ({placeholders})",
        [row[column] for column in columns],
    )


def duplicate_event(
    db_path: Path,
    on: date | str,
    title: str,
    count: int,
    period: str,
    today: Optional[date] = None,
) -> list[EventLine]:
    on = _parse_on(on)
    count = _parse_count(count)
    period = _parse_period(period)
    today = today or now_uk().date()
    step_days = PERIOD_DAYS[period]

    with closing(connect(db_path, write=True)) as conn:
        matches = [
            row
            for row in load_upcoming(conn, today)
            if from_ns(row["start_at"]).date() == on and row["title"] == title
        ]
        if not matches:
            raise CalendarWriteError(f"No event named '{title}' on {on.isoformat()}.")
        # Several rows can share a date and title. That is bad data, not a case
        # this tool resolves. Copy the first row; someone else removes the extras.
        source = matches[0]
        start = from_ns(source["start_at"])
        end = from_ns(source["end_at"]) if source["end_at"] is not None else None
        now_ns = time.time_ns()
        created: list[EventLine] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for step in range(1, count + 1):
                new_start = add_calendar_days(start, step_days * step)
                new_end = add_calendar_days(end, step_days * step) if end is not None else None
                _insert_copy(conn, source, to_ns(new_start), to_ns(new_end) if new_end else None, now_ns)
                created.append(
                    EventLine(
                        on=new_start.date(),
                        title=source["title"],
                        start=new_start,
                        end=new_end,
                        description=source["description"],
                        all_day=bool(source["all_day"]),
                    )
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return created


def duplicate_text(
    db_path: Path,
    on: date | str,
    title: str,
    count: int,
    period: str,
    today: Optional[date] = None,
) -> str:
    created = duplicate_event(db_path, on, title, count, period, today)
    lines = "\n".join(event.format() for event in created)
    noun = "event" if len(created) == 1 else "events"
    return f"Created {len(created)} {noun}:\n{lines}"


@dataclass(frozen=True)
class _NewEvent:
    title: str
    description: Optional[str]
    location: Optional[str]
    start: datetime
    end: datetime
    all_day: bool


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, clock(hour, minute), tzinfo=UK_TZ)


def _parse_when(value: str) -> datetime:
    text = (value or "").strip()
    match = EXACT_WHEN.fullmatch(text)
    if match is None:
        raise CalendarWriteError("Dates must look like 2026-10-05 14:30. Nothing was added.")
    try:
        day = date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise CalendarWriteError("Dates must look like 2026-10-05 14:30. Nothing was added.") from exc
    hour = int(match.group(2))
    minute = int(match.group(3))
    if hour > 23 or minute > 59:
        raise CalendarWriteError("Dates must look like 2026-10-05 14:30. Nothing was added.")
    return _at(day, hour, minute)


def _participants(who: str | list | tuple) -> list[str]:
    if isinstance(who, (list, tuple)):
        names: list[str] = []
        for item in who:
            names.extend(_participants(item))
        return names
    text = str(who or "").replace("&", " and ")
    parts = re.split(r",|\band\b", text, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _title_for(name: str, participants: list[str]) -> str:
    title = (name or "").strip()
    if not title:
        raise CalendarWriteError("Event name is required. Nothing was added.")
    if len(participants) == 1:
        person = participants[0]
        if not title.lower().startswith(person.lower()):
            title = f"{person} {title}"
    return title


def _description_for(description: str, participants: list[str]) -> Optional[str]:
    text = (description or "").strip()
    if len(participants) < 2:
        return text or None
    lowered = text.lower()
    if all(person.lower() in lowered for person in participants):
        return text or None
    names = _join_names(participants)
    if not text:
        return names
    return f"{text} ({names})"


def _blank(value: str) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def _parse_repeat(repeat: str) -> Optional[str]:
    text = (repeat or "").strip().lower()
    if text in {"", "none", "no"}:
        return None
    if text not in PERIOD_DAYS:
        raise CalendarWriteError("Repeat must be daily, weekly, or fortnightly, or left empty. Nothing was added.")
    return text


def _plus_year(day: date) -> date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, month=2, day=28)


def _day_slices(start: datetime, end: datetime) -> list[tuple[datetime, datetime, bool]]:
    if end < start:
        raise CalendarWriteError("End is before the start. Nothing was added.")
    if start.date() == end.date():
        if start.time() == end.time():
            day = start.date()
            return [(_at(day, 0, 0), _at(day, 23, 59), True)]
        return [(start, end, False)]

    slices: list[tuple[datetime, datetime, bool]] = [(start, _at(start.date(), 23, 59), False)]
    day = start.date() + timedelta(days=1)
    while day < end.date():
        slices.append((_at(day, 0, 0), _at(day, 23, 59), True))
        day += timedelta(days=1)
    last_start = _at(end.date(), 0, 0)
    if last_start.time() == end.time():
        slices.append((_at(end.date(), 0, 0), _at(end.date(), 23, 59), True))
    else:
        slices.append((last_start, end, False))
    return slices


def _eight_week_copies(period: str) -> int:
    return (EIGHT_WEEKS_DAYS - 1) // PERIOD_DAYS[period]


def _plan_events(
    who: str | list | tuple,
    name: str,
    start: str,
    end: str,
    location: str,
    description: str,
    repeat: str,
) -> list[_NewEvent]:
    participants = _participants(who)
    if not participants:
        raise CalendarWriteError("Say who it is for, for example Bob or Bob and Alice. Nothing was added.")
    title = _title_for(name, participants)
    details = _description_for(description, participants)
    place = _blank(location)
    start_at = _parse_when(start)
    end_at = _parse_when(end)
    period = _parse_repeat(repeat)
    planned = [
        _NewEvent(title, details, place, slice_start, slice_end, all_day)
        for slice_start, slice_end, all_day in _day_slices(start_at, end_at)
    ]
    if period is None:
        return planned
    copies = _eight_week_copies(period)
    step = PERIOD_DAYS[period]
    repeated = list(planned)
    for nth in range(1, copies + 1):
        delta = step * nth
        for event in planned:
            repeated.append(
                _NewEvent(
                    event.title,
                    event.description,
                    event.location,
                    add_calendar_days(event.start, delta),
                    add_calendar_days(event.end, delta),
                    event.all_day,
                )
            )
    repeated.sort(key=lambda event: (event.start, event.title))
    return repeated


def _check_dates(events: list[_NewEvent], today: date) -> None:
    limit = _plus_year(today)
    for event in events:
        for moment in (event.start, event.end):
            day = moment.astimezone(UK_TZ).date()
            if day < today:
                raise CalendarWriteError(f"{day.isoformat()} is in the past. Nothing was added.")
            if day > limit:
                raise CalendarWriteError(f"{day.isoformat()} is more than one year ahead. Nothing was added.")


def _insert_new(
    conn: sqlite3.Connection,
    calendar_id: str,
    user_id: str,
    event: _NewEvent,
    now_ns: int,
) -> None:
    conn.execute(
        """
        INSERT INTO calendar_event (
            id, calendar_id, user_id, title, description, start_at, end_at,
            all_day, rrule, color, location, data, meta, is_cancelled,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, 0, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            calendar_id,
            user_id,
            event.title,
            event.description,
            to_ns(event.start),
            to_ns(event.end),
            int(event.all_day),
            event.location,
            ALERT_META,
            now_ns,
            now_ns,
        ),
    )


def _line(event: _NewEvent) -> EventLine:
    return EventLine(
        on=event.start.date(),
        title=event.title,
        start=event.start,
        end=event.end,
        description=event.description,
        all_day=event.all_day,
    )


def add_event(
    db_path: Path,
    who: str | list | tuple,
    name: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
    repeat: str = "",
    today: Optional[date] = None,
    user_id: Optional[str] = None,
) -> list[EventLine]:
    today = today or now_uk().date()
    planned = _plan_events(who, name, start, end, location, description, repeat)
    _check_dates(planned, today)
    with closing(connect(db_path, write=True)) as conn:
        calendar = household_calendar(conn)
        owner = (user_id or "").strip() or calendar["user_id"]
        now_ns = time.time_ns()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for event in planned:
                _insert_new(conn, calendar["id"], owner, event, now_ns)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return [_line(event) for event in planned]


def format_added(events: list[EventLine]) -> str:
    noun = "event" if len(events) == 1 else "events"
    lines = [event.format() for event in events]
    if len(lines) <= MAX_DUPLICATIONS:
        return f"Added {len(lines)} {noun}:\n" + "\n".join(lines)
    return f"Added {len(lines)} {noun}, from {lines[0]} to {lines[-1]}."


def add_event_text(
    db_path: Path,
    who: str | list | tuple,
    name: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
    repeat: str = "",
    today: Optional[date] = None,
    user_id: Optional[str] = None,
) -> str:
    return format_added(
        add_event(db_path, who, name, start, end, location, description, repeat, today, user_id)
    )


def _period_arg(value: str) -> str:
    try:
        return _parse_period(value)
    except CalendarWriteError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calendar-write",
        description="List and duplicate events on the household calendar named Family.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Open WebUI sqlite file. Defaults to CALENDAR_DB, or webui.db beside this script.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="List upcoming events as date and name.")
    list_parser.add_argument(
        "regex",
        nargs="?",
        help="Case-insensitive regex matched against the event name and description.",
    )

    duplicate_parser = commands.add_parser(
        "duplicate",
        help="Copy one event onto later dates (at most 16).",
    )
    duplicate_parser.add_argument("date", help="UK date as printed by list, YYYY-MM-DD.")
    duplicate_parser.add_argument("title", help="Event name as printed by list.")
    duplicate_parser.add_argument("count", type=int, help=f"How many copies to add, from 1 to {MAX_DUPLICATIONS}.")
    duplicate_parser.add_argument("period", type=_period_arg, help="daily, weekly, or fortnightly.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    db_path = args.db if args.db is not None else default_db_path()
    try:
        if args.command == "list":
            text = list_text(db_path, args.regex)
            if text:
                print(text)
            return 0
        text = duplicate_text(db_path, args.date, args.title, args.count, args.period)
        print(text)
        return 0
    except CalendarWriteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Error: could not use the calendar database ({exc}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
