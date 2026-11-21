"""Rewind (a look back at any period), and recording time spent in Plekstender for it."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

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
from plekstender.db.models import AppTime, Artist, Track
from plekstender.history import rewind, stats
from plekstender.history.stats import Top
from plekstender.sync.parse import normalise_artist, normalise_title

router = APIRouter(prefix="/api", tags=["stats"])
MAX_DAYS = 366 * 10
DAY_SECONDS = 86_400


def _repeat(song: rewind.RepeatSong, track_key: int | None = None) -> RewindRepeat:
    return RewindRepeat(
        name=song.name,
        artist=song.artist,
        runs=song.runs,
        longest=song.longest,
        shortest=song.shortest,
        track_key=track_key,
    )


async def _song_keys(
    db: AsyncSession, server_id: int | None, songs: list[tuple[str | None, str, int | None]]
) -> list[int | None]:
    """The library songs for (artist, title, album) triples, so Rewind can play them: the
    same title on that album, or else by that artist (as album or song artist)."""
    if server_id is None or not songs:
        return [None] * len(songs)
    titles = {normalise_title(title) for _, title, _ in songs}
    rows = await db.execute(
        select(
            Track.rating_key,
            Track.title_norm,
            Track.album_rating_key,
            Track.original_title,
            Artist.title,
        )
        .outerjoin(
            Artist,
            (Artist.server_id == Track.server_id) & (Artist.rating_key == Track.artist_rating_key),
        )
        .where(
            Track.server_id == server_id,
            Track.title_norm.in_(titles),
            Track.part_key.is_not(None),
        )
        .order_by(Track.popularity.desc().nulls_last(), Track.rating_key)
    )
    found = rows.all()
    out: list[int | None] = []
    for artist, title, album in songs:
        norm, who = normalise_title(title), normalise_artist(artist or "")
        same = [r for r in found if r[1] == norm]
        on_album = next((r[0] for r in same if album is not None and r[2] == album), None)
        by_artist = next(
            (
                r[0]
                for r in same
                if who and who in {normalise_artist(r[3] or ""), normalise_artist(r[4] or "")}
            ),
            None,
        )
        out.append(on_album or by_artist)
    return out


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
    # The songs Rewind can play: its top songs and the ones on repeat.
    keys = await _song_keys(
        db,
        library,
        [(t.artist, t.name, t.rating_key) for t in r.top_tracks]
        + [(s.artist, s.name, None) for s in r.repeats]
        + ([(r.longest_repeat.artist, r.longest_repeat.name, None)] if r.longest_repeat else []),
    )
    top_tracks = _items(r.top_tracks, r.colours)
    for item, key in zip(top_tracks, keys, strict=False):
        item.track_key = key
    repeat_keys = keys[len(top_tracks) :]
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
        top_tracks=top_tracks,
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
        repeats=[_repeat(s, k) for s, k in zip(r.repeats, repeat_keys, strict=False)],
        longest_repeat=_repeat(r.longest_repeat, repeat_keys[len(r.repeats)])
        if r.longest_repeat
        else None,
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
