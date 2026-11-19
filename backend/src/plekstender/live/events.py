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
    # When the source gives a distance but no coordinates (Data Thistle).
    distance_km: float | None = None
    # Ticket prices, when the source has them (0 is free).
    price_min: float | None = None
    price_max: float | None = None
    currency: str | None = None


def prices(
    items: list[tuple[object, object, object]],
) -> tuple[float | None, float | None, str | None]:
    """The lowest and highest of (min, max, currency) prices, in the first currency given."""
    currency = next((str(c).upper()[:3] for _, _, c in items if isinstance(c, str) and c), None)
    numbers: list[float] = []
    for low, high, c in items:
        if currency and isinstance(c, str) and c and c.upper()[:3] != currency:
            continue
        numbers += [
            float(v)
            for v in (low, high)
            if isinstance(v, int | float) and not isinstance(v, bool) and v >= 0
        ]
    if not numbers:
        return None, None, None
    return min(numbers), max(numbers), currency
