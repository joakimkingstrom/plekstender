"""Rewind: a "wrapped"-style look back at any period of a person's listening.

Built from the listening history (duplicates across sources excluded, like the other stats),
linked to the library where it can be (artwork, cover colours, genres, release dates).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, case, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import (
    Album,
    AlbumColour,
    AppTime,
    GameRound,
    ItemTag,
    Listen,
    Tag,
)
from plekstender.history import stats
from plekstender.history.stats import ASSUMED_DURATION_MS, Top, in_period

TOP = 5
# Played at least this often in the period to count as a new obsession.
OBSESSION_MIN = 10
PARTS_OF_DAY = (("night", 0, 6), ("morning", 6, 12), ("afternoon", 12, 18), ("evening", 18, 24))


@dataclass
class RepeatSong:
    name: str
    artist: str
    runs: int  # times it was played two or more times in a row
    longest: int  # the most times in a row


@dataclass
class GenreTime:
    when: str  # a weekday (0 = Monday) as a string, or a part of the day
    genre: str
    share: float  # of that time's listens linked to a genre


@dataclass
class Rewind:
    listens: int
    listening_ms: int
    days: int  # days with listens
    artists: int
    tracks: int
    top_artists: list[Top]
    top_albums: list[Top]
    top_tracks: list[Top]
    new_artists: list[Top]
    new_albums: list[Top]
    new_releases: list[Top]
    weekdays: list[int]  # listens per weekday, Monday first
    hours: list[int]  # listens per hour of the day
    genres: list[tuple[str, int]]  # the period's top genres (listens)
    genre_days: list[GenreTime]
    genre_parts: list[GenreTime]
    repeats: list[RepeatSong]
    longest_repeat: RepeatSong | None
    game: dict[str, int] | None
    app_seconds: int | None  # time in Plekstender (recorded from 21 Nov 2026)
    colours: dict[int, str] = field(default_factory=dict)  # album rating key -> cover colour


def _local(column: Any, tz: ZoneInfo) -> Any:
    return func.timezone(tz.key, column)


async def _firsts_in(
    db: AsyncSession,
    user_id: int,
    start: dt.datetime,
    end: dt.datetime,
    kind: str,
) -> list[Top]:
    """Artists or albums first played in the period, played at least OBSESSION_MIN times
    in it, most played first."""
    group = (Listen.artist_key,) if kind == "artist" else (Listen.artist_key, Listen.album_key)
    base = [Listen.user_id == user_id, Listen.duplicate_of.is_(None)]
    if kind == "album":
        base.append(Listen.album_key.is_not(None))
    firsts = (
        select(*group, func.min(Listen.listened_at).label("first"))
        .where(*base, Listen.listened_at < end)
        .group_by(*group)
        .subquery()
    )
    new = select(*[firsts.c[c.key] for c in group]).where(
        firsts.c.first >= start, firsts.c.first < end
    )
    found = await stats.top(
        db,
        user_id,
        "artist" if kind == "artist" else "album",
        start,
        end,
        TOP * 4,
        where=[
            (
                Listen.artist_key.in_(new)
                if kind == "artist"
                else func.row(Listen.artist_key, Listen.album_key).in_(new)
            )
        ],
    )
    return [t for t in found if t.listens >= OBSESSION_MIN][:TOP]


async def _new_releases(
    db: AsyncSession,
    user_id: int,
    server_id: int | None,
    start: dt.datetime,
    end: dt.datetime,
) -> list[Top]:
    """Albums released in the period (by the library's release date, else its year when the
    period covers whole years) that were played most."""
    if server_id is None:
        return []
    played = await stats.top(db, user_id, "album", start, end, 300)
    await stats.link_library(db, user_id, server_id, "album", played)
    keys = [t.rating_key for t in played if t.rating_key]
    if not keys:
        return []
    rows = await db.execute(
        select(Album.rating_key, Album.released, Album.year).where(
            Album.server_id == server_id, Album.rating_key.in_(keys)
        )
    )
    first, last = start.date(), (end - dt.timedelta(microseconds=1)).date()
    whole_years = first.month == 1 and first.day == 1 and (last.month, last.day) == (12, 31)
    fresh = set()
    for key, released, year in rows.all():
        if released is not None:
            if first <= released <= last:
                fresh.add(key)
        elif year is not None and whole_years and first.year <= year <= last.year:
            fresh.add(key)
    return [t for t in played if t.rating_key in fresh][:TOP]


async def _repeats(
    db: AsyncSession, user_id: int, start: dt.datetime, end: dt.datetime
) -> tuple[list[RepeatSong], RepeatSong | None]:
    """Songs played two or more times in a row: how often, and the longest run."""
    rows = await db.execute(
        select(Listen.artist_key, Listen.track_key, Listen.track_name, Listen.artist_name)
        .where(*in_period(user_id, start, end))
        .order_by(Listen.listened_at, Listen.id)
    )
    songs: dict[tuple[str, str], RepeatSong] = {}
    previous: tuple[str, str] | None = None
    run = 0
    longest: RepeatSong | None = None

    def close(key: tuple[str, str] | None, length: int, names: tuple[str, str]) -> None:
        nonlocal longest
        if key is None or length < 2:
            return
        song = songs.setdefault(key, RepeatSong(names[0], names[1], 0, 0))
        song.runs += 1
        song.longest = max(song.longest, length)
        if longest is None or length > longest.longest:
            longest = RepeatSong(names[0], names[1], 1, length)

    names: tuple[str, str] = ("", "")
    for artist_key, track_key, track, artist in rows.all():
        key = (artist_key, track_key)
        if key == previous:
            run += 1
            continue
        close(previous, run, names)
        previous, run, names = key, 1, (track, artist)
    close(previous, run, names)
    ranked = sorted(songs.values(), key=lambda s: (-s.runs, -s.longest, s.name))
    return ranked[:TOP], longest


async def _when(
    db: AsyncSession, user_id: int, start: dt.datetime, end: dt.datetime, tz: ZoneInfo
) -> tuple[list[int], list[int], dict[tuple[int, int], Counter[str]]]:
    """Listens per weekday and hour (in the person's time zone), and per artist key."""
    local = _local(Listen.listened_at, tz)
    weekday = cast(extract("isodow", local), Integer) - 1
    hour = cast(extract("hour", local), Integer)
    rows = await db.execute(
        select(weekday, hour, Listen.artist_key, func.count())
        .where(*in_period(user_id, start, end))
        .group_by(weekday, hour, Listen.artist_key)
    )
    weekdays, hours = [0] * 7, [0] * 24
    by_artist: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    for day, hr, artist_key, count in rows.all():
        weekdays[day] += count
        hours[hr] += count
        by_artist[(day, hr)][artist_key] += count
    return weekdays, hours, by_artist


async def _artist_genres(
    db: AsyncSession, user_id: int, server_id: int | None, artist_keys: list[str]
) -> dict[str, list[str]]:
    """Library artists' genres (their own and their albums'), by normalised name."""
    if server_id is None or not artist_keys:
        return {}
    tops = [Top((k,), k, None, 0) for k in artist_keys]
    await stats.link_library(db, user_id, server_id, "artist", tops)
    by_key = {t.rating_key: t.keys[0] for t in tops if t.rating_key}
    if not by_key:
        return {}
    album_owner = select(Album.rating_key, Album.artist_rating_key).where(
        Album.server_id == server_id, Album.artist_rating_key.in_(list(by_key))
    )
    owners = {k: a for k, a in (await db.execute(album_owner)).all()}
    rows = await db.execute(
        select(ItemTag.rating_key, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(
            ItemTag.server_id == server_id,
            Tag.kind == "genre",
            ItemTag.rating_key.in_([*by_key, *owners]),
        )
    )
    genres: dict[str, set[str]] = defaultdict(set)
    for key, name in rows.all():
        artist = by_key.get(key) or by_key.get(owners.get(key) or -1)
        if artist:
            genres[artist].add(name)
    return {k: sorted(v) for k, v in genres.items()}


def _genre_times(
    by_artist: dict[tuple[int, int], Counter[str]], genres: dict[str, list[str]]
) -> tuple[list[tuple[str, int]], list[GenreTime], list[GenreTime]]:
    """The top genres overall, and the top genre of each weekday and part of the day (an
    artist's listens split evenly over their genres)."""
    total: defaultdict[str, float] = defaultdict(float)
    days: dict[int, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
    parts: dict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
    for (day, hour), artists in by_artist.items():
        part = next(name for name, a, b in PARTS_OF_DAY if a <= hour < b)
        for artist, count in artists.items():
            names = genres.get(artist)
            if not names:
                continue
            for genre in names:
                share = count / len(names)
                total[genre] += share
                days[day][genre] += share
                parts[part][genre] += share

    def top_of(counter: dict[str, float], when: str) -> GenreTime | None:
        if not counter:
            return None
        genre, n = max(counter.items(), key=lambda x: (x[1], x[0]))
        return GenreTime(when, genre, round(n / sum(counter.values()), 3))

    genre_days = [g for d in range(7) if (g := top_of(days[d], str(d)))]
    genre_parts = [g for name, _, _ in PARTS_OF_DAY if (g := top_of(parts[name], name))]
    overall = [(g, round(n)) for g, n in sorted(total.items(), key=lambda x: -x[1])[:TOP]]
    return overall, genre_days, genre_parts


async def _game(
    db: AsyncSession, user_id: int, start: dt.datetime, end: dt.datetime
) -> dict[str, int] | None:
    row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((GameRound.correct, 1), else_=0)), 0),
                func.coalesce(func.sum(GameRound.points), 0),
                func.coalesce(func.max(GameRound.streak), 0),
                func.count(func.distinct(func.date(GameRound.created_at))),
            ).where(
                GameRound.user_id == user_id,
                GameRound.created_at >= start,
                GameRound.created_at < end,
            )
        )
    ).one()
    if not row[0]:
        return None
    return {
        "rounds": row[0],
        "correct": int(row[1]),
        "points": int(row[2]),
        "best_streak": int(row[3]),
        "days": row[4],
    }


async def _app_seconds(
    db: AsyncSession, user_id: int, start: dt.datetime, end: dt.datetime, tz: ZoneInfo
) -> int | None:
    first, last = (
        start.astimezone(tz).date(),
        (end - dt.timedelta(microseconds=1)).astimezone(tz).date(),
    )
    total = await db.scalar(
        select(func.sum(AppTime.seconds)).where(
            AppTime.user_id == user_id, AppTime.day >= first, AppTime.day <= last
        )
    )
    return int(total) if total else None


async def build(
    db: AsyncSession,
    user_id: int,
    server_id: int | None,
    start: dt.datetime,
    end: dt.datetime,
    tz: ZoneInfo,
) -> Rewind:
    totals = await stats.totals(db, user_id, start, end)
    day = func.date(_local(Listen.listened_at, tz))
    days = await db.scalar(
        select(func.count(func.distinct(day))).where(*in_period(user_id, start, end))
    )
    tops = {
        kind: await stats.top(db, user_id, kind, start, end, TOP)
        for kind in ("artist", "album", "track")
    }
    new_artists = await _firsts_in(db, user_id, start, end, "artist")
    new_albums = await _firsts_in(db, user_id, start, end, "album")
    if server_id is not None:
        for kind, items in (*tops.items(), ("artist", new_artists), ("album", new_albums)):
            await stats.link_library(db, user_id, server_id, kind, items)
    new_releases = await _new_releases(db, user_id, server_id, start, end)
    weekdays, hours, by_artist = await _when(db, user_id, start, end, tz)
    played_artists = list({a for counter in by_artist.values() for a in counter})
    genres = await _artist_genres(db, user_id, server_id, played_artists[:3000])
    overall, genre_days, genre_parts = _genre_times(by_artist, genres)
    repeats, longest = await _repeats(db, user_id, start, end)

    # Cover colours of the albums shown (for the cards' colours).
    album_keys = {
        t.rating_key
        for t in (*tops["album"], *tops["track"], *new_albums, *new_releases)
        if t.rating_key
    }
    colours: dict[int, str] = {}
    if server_id is not None and album_keys:
        rows = await db.execute(
            select(AlbumColour.rating_key, AlbumColour.hex).where(
                and_(
                    AlbumColour.server_id == server_id,
                    AlbumColour.rating_key.in_(album_keys),
                    AlbumColour.hex.is_not(None),
                )
            )
        )
        colours = {k: h for k, h in rows.all() if h}

    return Rewind(
        listens=totals["listens"],
        listening_ms=totals["listening_ms"],
        days=days or 0,
        artists=totals["artists"],
        tracks=totals["tracks"],
        top_artists=tops["artist"],
        top_albums=tops["album"],
        top_tracks=tops["track"],
        new_artists=new_artists,
        new_albums=new_albums,
        new_releases=new_releases,
        weekdays=weekdays,
        hours=hours,
        genres=overall,
        genre_days=genre_days,
        genre_parts=genre_parts,
        repeats=repeats,
        longest_repeat=longest,
        game=await _game(db, user_id, start, end),
        app_seconds=await _app_seconds(db, user_id, start, end, tz),
        colours=colours,
    )


__all__ = ["ASSUMED_DURATION_MS", "Rewind", "build"]
