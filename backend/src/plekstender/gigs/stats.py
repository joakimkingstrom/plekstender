"""Stats over the gigs someone went to: who, where, when."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from plekstender.db.models import Gig
from plekstender.sync.parse import fold, normalise_artist

SEASONS = ("winter", "spring", "summer", "autumn")


def season(month: int, latitude: float | None) -> str:
    """Meteorological seasons (December to February is winter), flipped south of the equator."""
    index = (month % 12) // 3  # 0 winter, 1 spring, 2 summer, 3 autumn (north)
    if latitude is not None and latitude < 0:
        index = (index + 2) % 4
    return SEASONS[index]


@dataclass
class GigStats:
    total: int = 0
    artists: int = 0
    venues: int = 0
    first: Any = None
    last: Any = None
    top_artists: list[dict[str, Any]] = field(default_factory=list)
    top_venues: list[dict[str, Any]] = field(default_factory=list)
    years: list[dict[str, int]] = field(default_factory=list)
    months: list[int] = field(default_factory=lambda: [0] * 12)
    seasons: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SEASONS, 0))


def gig_stats(gigs: list[Gig], top: int = 10) -> GigStats:
    stats = GigStats(total=len(gigs))
    if not gigs:
        return stats
    artist_counts: Counter[str] = Counter()
    artist_ref: dict[str, dict[str, Any]] = {}
    venue_counts: Counter[str] = Counter()
    venue_name: dict[str, tuple[str, str | None]] = {}
    year_counts: Counter[int] = Counter()
    for gig in gigs:
        for a in {normalise_artist(a["name"]): a for a in gig.artists}.values():
            key = normalise_artist(a["name"])
            artist_counts[key] += 1
            if key not in artist_ref or (
                a.get("rating_key") and not artist_ref[key].get("rating_key")
            ):
                artist_ref[key] = a
        if gig.venue:
            vkey = f"{fold(gig.venue)}|{fold(gig.city or '')}"
            venue_counts[vkey] += 1
            venue_name.setdefault(vkey, (gig.venue, gig.city))
        year_counts[gig.date.year] += 1
        stats.months[gig.date.month - 1] += 1
        stats.seasons[season(gig.date.month, gig.latitude)] += 1
    stats.artists = len(artist_counts)
    stats.venues = len(venue_counts)
    stats.first = min(g.date for g in gigs)
    stats.last = max(g.date for g in gigs)
    stats.top_artists = [
        {
            "name": artist_ref[k]["name"],
            "gigs": n,
            "server_id": artist_ref[k].get("server_id"),
            "rating_key": artist_ref[k].get("rating_key"),
        }
        for k, n in sorted(
            artist_counts.items(), key=lambda x: (-x[1], artist_ref[x[0]]["name"].lower())
        )[:top]
    ]
    stats.top_venues = [
        {"name": venue_name[k][0], "city": venue_name[k][1], "gigs": n}
        for k, n in sorted(
            venue_counts.items(), key=lambda x: (-x[1], venue_name[x[0]][0].lower())
        )[:top]
    ]
    first_year, last_year = stats.first.year, stats.last.year
    stats.years = [
        {"year": y, "gigs": year_counts.get(y, 0)} for y in range(first_year, last_year + 1)
    ]
    return stats
