"""Человекочитаемые даты для UI."""

from __future__ import annotations

from datetime import datetime, timezone


def format_when(value: str | None) -> str:
    """ISO UTC → локальное «26.07.2026, 23:14»; мусор возвращаем как есть."""
    if not value:
        return ""
    raw = str(value).strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    return local.strftime("%d.%m.%Y, %H:%M")
