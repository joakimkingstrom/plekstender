"""Rewind (a look back at any period), and recording time spent in Plekstender for it."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    AppTimeIn,
    RewindGame,
    RewindGenre,
    RewindGenreTime,
    RewindItem,
    RewindOut,
    RewindRepeat,
)
from plekstender.db.models import AppTime
from plekstender.history import rewind, stats
from plekstender.history.stats import Top

router = APIRouter(prefix="/api", tags=["stats"])
MAX_DAYS = 366 * 10
DAY_SECONDS = 86_400


def _repeat(song: rewind.RepeatSong) -> RewindRepeat:
    return RewindRepeat(name=song.name, artist=song.artist, runs=song.runs, longest=song.longest)


def _items(items: list[Top], colours: dict[int, str]) -> list[RewindItem]:
    return [
        RewindItem(
            name=t.name,
            artist=t.artist,
            listens=t.listens,
            listening_ms=t.listening_ms,
            rating_key=t.rating_key,
            thumb=t.thumb,
            colour=colours.get(t.rating_key) if t.rating_key else None,
        )
        for t in items
    ]


@router.get("/rewind", response_model=RewindOut)
async def get_rewind(
    db: DbDep,
    user: UserDep,
    start: dt.date,
    end: Annotated[dt.date, Query(description="The last day (inclusive).")],
    tz: Annotated[str, Query(max_length=64)] = "UTC",
    server_id: Annotated[int | None, Query(description="Link items to this server.")] = None,
) -> RewindOut:
    """Your Rewind for the days from `start` to `end` (in your time zone)."""
    try:
        zone = stats.zone(tz)
    except stats.StatsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if end < start or (end - start).days >= MAX_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick up to ten years, start first."
        )
    library = (await get_user_server(db, user, server_id)).server_id if server_id else None
    begin = dt.datetime.combine(start, dt.time(), zone)
    finish = min(
        dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), zone), dt.datetime.now(dt.UTC)
    )
    r = await rewind.build(db, user.id, library, begin, finish, zone)
    return RewindOut(
        start=start,
        end=end,
        listens=r.listens,
        listening_ms=r.listening_ms,
        days=r.days,
        period_days=(end - start).days + 1,
        artists=r.artists,
        tracks=r.tracks,
        top_artists=_items(r.top_artists, r.colours),
        top_albums=_items(r.top_albums, r.colours),
        top_tracks=_items(r.top_tracks, r.colours),
        new_artists=_items(r.new_artists, r.colours),
        new_albums=_items(r.new_albums, r.colours),
        new_releases=_items(r.new_releases, r.colours),
        weekdays=r.weekdays,
        hours=r.hours,
        genres=[RewindGenre(genre=g, listens=n) for g, n in r.genres],
        genre_days=[
            RewindGenreTime(when=g.when, genre=g.genre, share=g.share) for g in r.genre_days
        ],
        genre_parts=[
            RewindGenreTime(when=g.when, genre=g.genre, share=g.share) for g in r.genre_parts
        ],
        repeats=[_repeat(s) for s in r.repeats],
        longest_repeat=_repeat(r.longest_repeat) if r.longest_repeat else None,
        game=RewindGame(**r.game) if r.game else None,
        app_seconds=r.app_seconds,
    )


@router.post("/app-time", status_code=status.HTTP_204_NO_CONTENT)
async def record_app_time(
    body: AppTimeIn,
    db: DbDep,
    user: UserDep,
    tz: Annotated[str, Query(max_length=64)] = "UTC",
) -> None:
    """Time spent in Plekstender (the app reports it each minute it's open and in use)."""
    try:
        today = dt.datetime.now(stats.zone(tz)).date()
    except stats.StatsError:
        today = dt.datetime.now(dt.UTC).date()
    stmt = insert(AppTime).values(user_id=user.id, day=today, seconds=body.seconds)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "day"],
            set_={"seconds": func.least(AppTime.seconds + stmt.excluded.seconds, DAY_SECONDS)},
        )
    )
    await db.commit()
