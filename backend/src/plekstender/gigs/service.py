"""Gigs: linking their artists to the library, and building them from Live's gigs."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.api.routes.library import visible
from plekstender.db.models import Artist, Gig, LiveShow, UserServer
from plekstender.live.refresh import SKIP_NAMES, Followed, Matcher
from plekstender.sync.parse import fold, normalise_artist

MAX_ARTISTS = 50


async def library_matcher(db: AsyncSession, user_id: int) -> Matcher:
    """Matches names to the library's artists the user can see (on any of their servers)."""
    servers = list(await db.scalars(select(UserServer).where(UserServer.user_id == user_id)))
    merged: dict[str, Followed] = {}
    for us in servers:
        state, cond, where = visible(Artist, user_id, us)
        rows = await db.execute(
            select(Artist.title, Artist.server_id, Artist.rating_key)
            .join(state, cond)
            .where(*where)
        )
        for title, server_id, rating_key in rows.all():
            name = (title or "").strip()
            if not name or name.lower() in SKIP_NAMES:
                continue
            key = normalise_artist(name)
            if key in merged:
                merged[key].refs.append((server_id, rating_key))
            else:
                merged[key] = Followed(name, None, 0, [(server_id, rating_key)])
    return Matcher(list(merged.values()), fuzzy=False)


def link_artists(names: list[str], match: Matcher) -> list[dict[str, Any]]:
    """The bill as stored: each name, and the library artist it is (on the first server)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in names[:MAX_ARTISTS]:
        name = " ".join(raw.split())[:300]
        key = normalise_artist(name) if name else ""
        if not name or key in seen:
            continue
        seen.add(key)
        artist = match(name)
        ref = artist.refs[0] if artist else None
        out.append(
            {
                "name": name,
                "server_id": ref[0] if ref else None,
                "rating_key": ref[1] if ref else None,
            }
        )
    return out


def place_key(venue: str | None, city: str | None, country: str | None) -> str:
    return "|".join(fold(x or "").strip() for x in (venue, city, country))


def gig_from_live(group: list[LiveShow], user_id: int) -> Gig:
    """A gig from a Live gig's rows (the first row is preferred; ids from all of them)."""
    row = group[0]
    names = [a["name"] for r in group for a in r.artists] + [
        p["name"] for r in group for p in r.performers
    ]
    linked: dict[str, dict[str, Any]] = {}
    for r in group:
        for a in r.artists:
            linked.setdefault(
                normalise_artist(a["name"]),
                {
                    "name": a["name"],
                    "server_id": a.get("server_id"),
                    "rating_key": a.get("rating_key"),
                },
            )
    for name in names:
        linked.setdefault(
            normalise_artist(name), {"name": name, "server_id": None, "rating_key": None}
        )
    located = next((r for r in group if r.latitude is not None), None)
    return Gig(
        user_id=user_id,
        date=row.start_date,
        name=row.name if row.festival else None,
        venue=row.venue,
        city=row.city,
        country=row.country,
        latitude=located.latitude if located else None,
        longitude=located.longitude if located else None,
        place_precision="venue" if located else None,
        artists=list(linked.values())[:MAX_ARTISTS],
        source="live",
        source_ref=f"{row.source}:{row.source_id}"[:128],
        url=row.ticket_url or row.page_url,
    )


def live_refs(group: list[LiveShow]) -> set[str]:
    return {f"{r.source}:{r.source_id}"[:128] for r in group}


def today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()
