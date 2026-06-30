"""Human-friendly dates for trips and dashboard."""

from datetime import date
from typing import Optional


def format_day(d: date, *, short_month: bool = False) -> str:
    month = d.strftime("%b" if short_month else "%B")
    return f"{month} {d.day} — {d.strftime('%a')}"


def format_range(start: str, end: str) -> str:
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except (TypeError, ValueError):
        return start or ""

    if s == e:
        return format_day(s)
    if s.month == e.month and s.year == e.year:
        return f"{s.strftime('%B')} {s.day}–{e.day} — {s.strftime('%a')}–{e.strftime('%a')}"
    return f"{format_day(s)} → {format_day(e)}"


def trip_labels(trip: dict) -> dict:
    start = trip.get("start", "")
    end = trip.get("end", "") or start
    label = format_range(start, end) if start else ""
    return {
        "start": start,
        "end": end,
        "date_label": label,
    }
