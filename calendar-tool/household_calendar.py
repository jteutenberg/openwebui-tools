"""
title: Household Calendar
author: Family
description: Read events from the household calendar. Use when_is to find when a named event happens. Use whats_on to list events in a time period.
required_open_webui_version: 0.6.0
requirements: python-dateutil
version: 0.1.0
license: MIT
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

UK_TZ = ZoneInfo("Europe/London")
EIGHT_WEEKS = timedelta(weeks=8)
MAX_LINES = 50

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_SUPPORTED = (
    "today, tomorrow, this week, next week, this weekend, next weekend, "
    "this month, next month, a weekday (Tuesday), a date (29 August or 2026-08-29), "
    "this term, after this term, after half term"
)


class WindowError(ValueError):
    pass


@dataclass(frozen=True)
class TermDates:
    term_start: Optional[date]
    term_end: Optional[date]
    half_term_start: Optional[date]
    half_term_end: Optional[date]


def now_uk(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(UK_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=UK_TZ)
    return now.astimezone(UK_TZ)


def start_of_day(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UK_TZ)


def end_of_day(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=UK_TZ)


def to_ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def from_ns(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UK_TZ)


def parse_valve_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def split_who(who: str) -> list[str]:
    text = (who or "").replace("&", " and ")
    parts = re.split(r",|\band\b", text, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def matches_text(title: str, description: Optional[str], needle: str) -> bool:
    blob = f"{title} {description or ''}".lower()
    return needle.lower() in blob


def matches_who(title: str, description: Optional[str], names: list[str]) -> bool:
    if not names:
        return True
    return any(matches_text(title, description, name) for name in names)


def _monday(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def _week(d: date) -> tuple[date, date]:
    monday = _monday(d)
    return monday, monday + timedelta(days=6)


def _weekend(d: date) -> tuple[date, date]:
    monday = _monday(d)
    return monday + timedelta(days=5), monday + timedelta(days=6)


def _month_bounds(d: date) -> tuple[date, date]:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, 1), date(d.year, d.month, last)


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def _need(term: TermDates, *fields: str) -> None:
    missing = [name for name in fields if getattr(term, name) is None]
    if missing:
        raise WindowError(f"Error: Valve{'s' if len(missing) > 1 else ''} {', '.join(missing)} not set.")


def _single_day(d: date) -> tuple[datetime, datetime]:
    return start_of_day(d), end_of_day(d)


def _span(a: date, b: date) -> tuple[datetime, datetime]:
    return start_of_day(a), end_of_day(b)


def _named_date(text: str, today: date) -> Optional[date]:
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    named = re.fullmatch(r"(\d{1,2})\s+([a-z.]+)(?:\s+(\d{4}))?", text)
    if named:
        month = _MONTHS.get(named.group(2).rstrip("."))
        if not month:
            return None
        day = int(named.group(1))
        year = int(named.group(3)) if named.group(3) else today.year
        resolved = date(year, month, day)
        if named.group(3) is None and resolved < today:
            resolved = date(year + 1, month, day)
        return resolved
    return None


def parse_when(phrase: str, now: datetime, term: TermDates) -> tuple[datetime, datetime]:
    now = now_uk(now)
    today = now.date()
    text = re.sub(r"\s+", " ", (phrase or "").strip().lower())
    text = re.sub(r"^on ", "", text)

    if not text:
        raise WindowError(f"Error: missing time. Use: {_SUPPORTED}.")

    if text == "today":
        return _single_day(today)
    if text == "tomorrow":
        return _single_day(today + timedelta(days=1))
    if text == "this week":
        return _span(*_week(today))
    if text == "next week":
        return _span(*_week(today + timedelta(days=7)))
    if text == "this weekend":
        return _span(*_weekend(today))
    if text == "next weekend":
        return _span(*_weekend(today + timedelta(days=7)))
    if text == "this month":
        return _span(*_month_bounds(today))
    if text == "next month":
        return _span(*_month_bounds(_add_months(today, 1)))
    if text == "this term":
        _need(term, "term_start", "term_end")
        return _span(term.term_start, term.term_end)
    if text == "after this term":
        _need(term, "term_end")
        start = term.term_end + timedelta(days=1)
        end = start + EIGHT_WEEKS - timedelta(days=1)
        return _span(start, end)
    if text == "after half term":
        _need(term, "half_term_end", "term_end")
        return _span(term.half_term_end + timedelta(days=1), term.term_end)

    weekday_text = re.sub(r"^next ", "", text)
    if weekday_text in _WEEKDAYS:
        target = _WEEKDAYS[weekday_text]
        ahead = (target - today.weekday()) % 7
        if text.startswith("next ") and ahead == 0:
            ahead = 7
        return _single_day(today + timedelta(days=ahead))

    named = _named_date(text, today)
    if named:
        return _single_day(named)

    raise WindowError(f"Error: unknown time '{phrase}'. Use: {_SUPPORTED}.")


def resolve_window(
    when: str,
    now: datetime,
    term: TermDates,
    default: str,
) -> tuple[datetime, datetime]:
    text = (when or "").strip()
    if not text:
        if default == "eight_weeks":
            now = now_uk(now)
            return now, now + EIGHT_WEEKS
        return parse_when(default, now, term)
    return parse_when(text, now, term)


def occurrence_end(start: datetime, end: Optional[datetime], all_day: bool) -> datetime:
    if all_day:
        return end_of_day(start.astimezone(UK_TZ).date())
    if end is not None:
        return end
    return start


def still_upcoming(start: datetime, end: Optional[datetime], all_day: bool, now: datetime) -> bool:
    return occurrence_end(start, end, all_day) > now_uk(now)


def format_occurrence(title: str, start: datetime, end: Optional[datetime], all_day: bool) -> str:
    start = start.astimezone(UK_TZ)
    weekday = start.strftime("%A")
    if all_day:
        return f"{start:%Y-%m-%d} {weekday} all-day {title}"
    if end is None:
        return f"{start:%Y-%m-%d} {weekday} {start:%H:%M} {title}"
    end = end.astimezone(UK_TZ)
    if start.date() == end.date():
        return f"{start:%Y-%m-%d} {weekday} {start:%H:%M}-{end:%H:%M} {title}"
    return f"{start:%Y-%m-%d} {weekday} {start:%H:%M} to {end:%Y-%m-%d} {end:%H:%M} {title}"


def expand_recurring(event: dict, range_start_ns: int, range_end_ns: int) -> list[dict]:
    rrule_str = event.get("rrule")
    if not rrule_str:
        return [event]

    from dateutil.rrule import rrulestr

    def local(ns: int) -> datetime:
        return from_ns(ns).replace(tzinfo=None)

    range_start = local(range_start_ns)
    range_end = local(range_end_ns)
    original_start_ns = event["start_at"]
    original_start = local(original_start_ns)
    try:
        rule = rrulestr(rrule_str, dtstart=original_start, ignoretz=True)
    except Exception:
        return [event]

    original_end_ns = event.get("end_at")
    duration_ns = (original_end_ns - original_start_ns) if original_end_ns else None
    instances = []
    occurrence_start = rule.after(range_start - timedelta(days=1), inc=True)
    while occurrence_start and occurrence_start < range_end and len(instances) < 5000:
        instance_start = occurrence_start.replace(tzinfo=UK_TZ)
        instance_start_ns = to_ns(instance_start)
        if instance_start_ns >= range_start_ns:
            instances.append(
                {
                    **event,
                    "start_at": instance_start_ns,
                    "end_at": (instance_start_ns + duration_ns) if duration_ns else None,
                }
            )
        occurrence_start = rule.after(occurrence_start)
    return instances


def render_lines(rows: list[str]) -> str:
    if not rows:
        return "No events."
    if len(rows) <= MAX_LINES:
        return "\n".join(rows)
    hidden = len(rows) - MAX_LINES
    return "\n".join(rows[:MAX_LINES]) + f"\n({hidden} more)"


class Tools:
    class Valves(BaseModel):
        household_calendar_name: str = Field(
            default="Family",
            description="Name of the shared household calendar",
        )
        term_start: str = Field(default="", description="Current or upcoming term start, YYYY-MM-DD")
        term_end: str = Field(default="", description="Current or upcoming term end, YYYY-MM-DD")
        half_term_start: str = Field(default="", description="Half term start, YYYY-MM-DD")
        half_term_end: str = Field(default="", description="Half term end, YYYY-MM-DD")

    def __init__(self) -> None:
        self.valves = self.Valves()

    def _term(self) -> TermDates:
        return TermDates(
            term_start=parse_valve_date(self.valves.term_start),
            term_end=parse_valve_date(self.valves.term_end),
            half_term_start=parse_valve_date(self.valves.half_term_start),
            half_term_end=parse_valve_date(self.valves.half_term_end),
        )

    async def when_is(
        self,
        query: str,
        when: str = "",
        include_personal: bool = False,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        Find when named events happen. Use for "when is parents' evening" or "next swimming".

        :param query: Words from the event name or description, e.g. parents evening or Tom swimming.
        :param when: Optional time phrase from the user, e.g. this week, Tuesday, this term. Leave empty to search the next 8 weeks.
        :param include_personal: Set true only if the user asked to include their Personal calendar.
        """
        return await self._list(
            query=query,
            when=when,
            who="",
            include_personal=include_personal,
            default_when="eight_weeks",
            require_query=True,
            __user__=__user__,
        )

    async def whats_on(
        self,
        when: str = "",
        who: str = "",
        include_personal: bool = False,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        List events in a time period. Use for "what's on this weekend" or "what is Tom doing this week".

        :param when: Time phrase from the user, e.g. tomorrow, this weekend, this term. Leave empty for this week.
        :param who: Optional family member account names, e.g. Tom or Tom and Mum. Leave empty for everyone.
        :param include_personal: Set true only if the user asked to include their Personal calendar.
        """
        return await self._list(
            query="",
            when=when,
            who=who,
            include_personal=include_personal,
            default_when="this week",
            require_query=False,
            __user__=__user__,
        )

    async def _list(
        self,
        query: str,
        when: str,
        who: str,
        include_personal: Any,
        default_when: str,
        require_query: bool,
        __user__: Optional[dict],
    ) -> str:
        query = (query or "").strip()
        if require_query and not query:
            return "Error: query is required, e.g. parents evening."

        user_id = (__user__ or {}).get("id")
        if not user_id:
            return "Error: no user."

        now = now_uk()
        try:
            window_start, window_end = resolve_window(when, now, self._term(), default_when)
        except WindowError as exc:
            return str(exc)
        except ValueError as exc:
            return f"Error: {exc}"

        try:
            calendar_ids = await self._calendar_ids(user_id, as_bool(include_personal))
        except RuntimeError as exc:
            return str(exc)

        try:
            events = await self._load_events(user_id, to_ns(window_start), to_ns(window_end), calendar_ids)
        except Exception as exc:
            return f"Error: could not read calendar ({exc})."

        names = split_who(who)
        rows: list[tuple[datetime, str]] = []
        for event in events:
            if event.get("is_cancelled"):
                continue
            title = event.get("title") or ""
            description = event.get("description") or ""
            if query and not matches_text(title, description, query):
                continue
            if not matches_who(title, description, names):
                continue
            for instance in expand_recurring(event, to_ns(window_start), to_ns(window_end)):
                start = from_ns(instance["start_at"])
                end = from_ns(instance["end_at"]) if instance.get("end_at") else None
                all_day = bool(instance.get("all_day"))
                if not still_upcoming(start, end, all_day, now):
                    continue
                instance_end = occurrence_end(start, end, all_day)
                if start > window_end or instance_end < window_start:
                    continue
                rows.append((start, format_occurrence(title, start, end, all_day)))

        rows.sort(key=lambda item: item[0])
        return render_lines([line for _start, line in rows])

    async def _calendar_ids(self, user_id: str, include_personal: bool) -> list[str]:
        from open_webui.models.calendar import Calendars

        calendars = await Calendars.get_calendars_by_user(user_id)
        household_name = (self.valves.household_calendar_name or "Family").strip().lower()
        household = next((cal for cal in calendars if (cal.name or "").strip().lower() == household_name), None)
        if household is None:
            raise RuntimeError(f"Error: household calendar '{self.valves.household_calendar_name}' not found.")

        ids = [household.id]
        if include_personal:
            personal = next((cal for cal in calendars if (cal.name or "").strip().lower() == "personal"), None)
            if personal is not None and personal.id not in ids:
                ids.append(personal.id)
        return ids

    async def _load_events(
        self,
        user_id: str,
        start_ns: int,
        end_ns: int,
        calendar_ids: list[str],
    ) -> list[dict]:
        from open_webui.models.calendar import CalendarEvents

        events = await CalendarEvents.get_events_by_range(
            user_id=user_id,
            start=start_ns,
            end=end_ns,
            calendar_ids=calendar_ids,
        )
        rows = []
        for event in events:
            if hasattr(event, "model_dump"):
                rows.append(event.model_dump())
            else:
                rows.append(dict(event))
        return rows
