"""Starred shows as an iCalendar (RFC 5545) feed or file, for calendar apps.

Times are the venue's local time, written as "floating" times (no time zone), which
calendar apps show as written: right for shows near the user. Shows without a time are
all-day events.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

# A show's length when the source doesn't say (calendar apps need an end).
SHOW_HOURS = 3


@dataclass
class CalendarShow:
    uid: str
    title: str
    start_date: dt.date
    start_time: str | None
    end_date: dt.date | None
    status: str
    location: str | None
    url: str | None
    description: str
    updated: dt.datetime
    got_ticket: bool = False


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Lines of at most 75 octets, continued with a leading space."""
    out: list[str] = []
    current = ""
    for char in line:
        if len((current + char).encode()) > (75 if not out else 74):
            out.append(current)
            current = char
        else:
            current += char
    out.append(current)
    return [out[0], *(" " + part for part in out[1:])]


def _stamp(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _event(show: CalendarShow, now: dt.datetime) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{show.uid}", f"DTSTAMP:{_stamp(now)}"]
    lines.append(f"LAST-MODIFIED:{_stamp(show.updated)}")
    if show.start_time:
        hour, minute = (int(x) for x in show.start_time.split(":"))
        start = dt.datetime.combine(show.start_date, dt.time(hour, minute))
        end = start + dt.timedelta(hours=SHOW_HOURS)
        if show.end_date and show.end_date > show.start_date:
            end = dt.datetime.combine(show.end_date, dt.time(23, 0))
        lines += [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}"]
    else:
        last = show.end_date or show.start_date
        lines += [
            f"DTSTART;VALUE=DATE:{show.start_date:%Y%m%d}",
            f"DTEND;VALUE=DATE:{last + dt.timedelta(days=1):%Y%m%d}",
        ]
    lines.append(f"SUMMARY:{_escape(show.title)}")
    if show.location:
        lines.append(f"LOCATION:{_escape(show.location)}")
    if show.url:
        lines.append(f"URL:{show.url}")
    if show.description:
        lines.append(f"DESCRIPTION:{_escape(show.description)}")
    # A show with a ticket is confirmed; one that's only starred is tentative.
    confirmed = "CONFIRMED" if show.got_ticket else "TENTATIVE"
    lines.append(f"STATUS:{'CANCELLED' if show.status == 'cancelled' else confirmed}")
    lines.append("TRANSP:OPAQUE")
    lines.append("END:VEVENT")
    return lines


def calendar(shows: Iterable[CalendarShow], name: str, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Plekstender//Live//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        # Calendar apps that honour it look again every few hours.
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for show in shows:
        lines += _event(show, now)
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"
