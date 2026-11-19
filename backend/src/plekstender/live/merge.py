"""The same show found by several sources is listed once.

Two shows are the same when they're on the same day, share one of the user's artists, and
are at the same venue: the names match (one within the other, or most words in common),
or the venues are within a kilometre and a half of each other.
"""

from __future__ import annotations

import re
from collections import defaultdict

from plekstender.db.models import LiveShow
from plekstender.live.geocode import distance_km
from plekstender.sync.parse import fold, normalise_artist

# The source listed first when a show has several (JamBase's data is credited).
PRIORITY = {"jambase": 0, "ticketmaster": 1, "calendar": 2}
NEARBY_KM = 1.5
_WORD = re.compile(r"[a-z0-9]+")
_FILLER = {"the", "at", "and", "of", "live", "venue"}


def _words(venue: str | None) -> set[str]:
    return {w for w in _WORD.findall(fold(venue or "")) if w not in _FILLER}


def same_venue(a: LiveShow, b: LiveShow) -> bool:
    if None not in (a.latitude, a.longitude, b.latitude, b.longitude):
        far = distance_km(a.latitude, a.longitude, b.latitude, b.longitude)  # type: ignore[arg-type]
        if far <= NEARBY_KM:
            return True
    wa, wb = _words(a.venue), _words(b.venue)
    if wa and wb:
        if wa <= wb or wb <= wa:
            return True
        return len(wa & wb) / len(wa | wb) >= 0.5
    # No venue name on one side: the same city will do.
    return bool(a.city and b.city and fold(a.city) == fold(b.city))


def _artists(show: LiveShow) -> set[str]:
    """Everyone on the bill (the user's artists and the rest)."""
    names = [a["name"] for a in show.artists] + [p["name"] for p in show.performers]
    return {normalise_artist(n) for n in names if n}


def same_show(a: LiveShow, b: LiveShow) -> bool:
    return (
        a.start_date == b.start_date
        and a.source != b.source
        and bool(_artists(a) & _artists(b))
        and same_venue(a, b)
    )


def merge(rows: list[LiveShow]) -> list[list[LiveShow]]:
    """Groups of rows that are the same show, the preferred source first, in the order of
    each group's earliest row in `rows`."""
    ordered = sorted(rows, key=lambda r: PRIORITY.get(r.source, 9))
    groups: list[list[LiveShow]] = []
    by_day: dict[object, list[list[LiveShow]]] = defaultdict(list)
    for row in ordered:
        for group in by_day[row.start_date]:
            if all(r.source != row.source for r in group) and same_show(group[0], row):
                group.append(row)
                break
        else:
            group = [row]
            groups.append(group)
            by_day[row.start_date].append(group)
    first = {id(r): i for i, r in enumerate(rows)}
    groups.sort(key=lambda g: min(first[id(r)] for r in g))
    return groups
