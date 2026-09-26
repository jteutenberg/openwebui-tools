"""
title: Household Calendar Add
author: Family
description: Add an event to the household calendar. Use add_event with who, name, start, and end.
required_open_webui_version: 0.6.0
version: 0.1.0
license: MIT
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


def _load_writer(writer_dir: str):
    candidates: list[str] = []
    configured = (writer_dir or "").strip()
    if configured:
        candidates.append(configured)
    try:
        candidates.append(str(Path(__file__).resolve().parent))
    except NameError:
        pass
    for directory in candidates:
        if directory not in sys.path:
            sys.path.insert(0, directory)
    from household_calendar_write import add_event_text, default_db_path

    return add_event_text, default_db_path


def _user_id(user: object) -> Optional[str]:
    if not user:
        return None
    if isinstance(user, dict):
        value = user.get("id")
    else:
        value = getattr(user, "id", None)
    text = str(value or "").strip()
    return text or None


class Tools:
    class Valves(BaseModel):
        database_path: str = Field(
            default="",
            description="Path to the Open WebUI webui.db. Leave empty to use CALENDAR_DB, or webui.db beside the writer.",
        )
        writer_dir: str = Field(
            default="",
            description="Directory containing household_calendar_write.py. Leave empty if that file sits beside this tool or is on PYTHONPATH.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def add_event(
        self,
        who: str,
        name: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
        repeat: str = "",
        __user__: Optional[dict] = None,
    ) -> str:
        """
        Add one event to the household calendar.

        :param who: Account names. One name, or names joined by "and". Example: Bob. Example: Bob and Alice.
        :param name: Event name, such as rugby or parents evening.
        :param start: Start time, exactly like 2026-10-05 14:00.
        :param end: End time, exactly like 2026-10-05 16:00. Same time as start means all day.
        :param location: Place, or leave empty.
        :param description: Extra detail, or leave empty.
        :param repeat: Leave empty. Or daily, weekly, or fortnightly, which repeats for 8 weeks.
        """
        try:
            add_event_text, default_db_path = _load_writer(self.valves.writer_dir)
        except ImportError:
            return (
                "Error: calendar writer is not available. "
                "Set the writer_dir valve to the calendar-write-tool directory."
            )
        database = (self.valves.database_path or "").strip()
        db_path = Path(database) if database else default_db_path()
        try:
            return add_event_text(
                db_path,
                who,
                name,
                start,
                end,
                location,
                description,
                repeat,
                user_id=_user_id(__user__),
            )
        except Exception as exc:
            if isinstance(exc, sqlite3.Error):
                return f"Error: could not use the calendar database ({exc})."
            message = str(exc).strip()
            if message.startswith("Error:"):
                return message
            return f"Error: {message}"
