"""A show as the Live sources describe it (JamBase, Ticketmaster), before it's stored."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Performer:
    name: str
    headliner: bool
    # When the source says (Ticketmaster's attractions sometimes link MusicBrainz).
    mbid: str | None = None


@dataclass
class Event:
    id: str
    name: str
    festival: bool
    start_date: dt.date
    start_time: str | None
    end_date: dt.date | None
    status: str
    venue: str | None
    city: str | None
    region: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    ticket_url: str | None
    seller: str | None
    page_url: str | None
    performers: list[Performer] = field(default_factory=list)
