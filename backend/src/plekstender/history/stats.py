"""Listening stats over a user's listens (duplicates across sources excluded)."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import ColumnElement, and_, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Album, Artist, Listen, Track, UserItemState
from plekstender.sync.parse import normalise_artist, normalise_title

# Last.fm listens have no duration; listening time counts them at a typical song length.
ASSUMED_DURATION_MS = 215_000
RANGE_PATTERN = r"^(7d|30d|365d|all|y\d{4})$"

Bucket = Literal["day", "week", "month"]


class StatsError(ValueError):
    pass


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise StatsError(f"Unknown time zone {name!r}.") from exc


@dataclass(frozen=True)
class Period:
    key: str
    start: dt.datetime | None  # None = all time
    end: dt.datetime
    previous_start: dt.datetime | None
    bucket: Bucket


def resolve_period(key: str, tz: ZoneInfo, now: dt.datetime | None = None) -> Period:
    """A range key ("7d", "30d", "365d", "y2024", "all") as times in the user's zone."""
    now = now or dt.datetime.now(dt.UTC)
    local_now = now.astimezone(tz)
    today = dt.datetime.combine(local_now.date(), dt.time(), tz)
    if match := re.fullmatch(r"(\d+)d", key):
        days = int(match.group(1))
        start = today - dt.timedelta(days=days - 1)
        bucket: Bucket = "day" if days <= 31 else "week"
        return Period(key, start, now, start - dt.timedelta(days=days), bucket)
    if match := re.fullmatch(r"y(\d{4})", key):
        year = int(match.group(1))
        if not 1990 <= year <= local_now.year:
            raise StatsError(f"No stats for {year}.")
        start = dt.datetime(year, 1, 1, tzinfo=tz)
        end = min(dt.datetime(year + 1, 1, 1, tzinfo=tz), now)
        return Period(key, start, end, dt.datetime(year - 1, 1, 1, tzinfo=tz), "week")
    if key == "all":
        return Period(key, None, now, None, "month")
    raise StatsError(f"Unknown range {key!r}.")


def _in(user_id: int, start: dt.datetime | None, end: dt.datetime) -> list[ColumnElement[bool]]:
    conditions = [
        Listen.user_id == user_id,
        Listen.duplicate_of.is_(None),
        Listen.listened_at < end,
    ]
    if start is not None:
        conditions.append(Listen.listened_at >= start)
    return conditions


async def totals(
    db: AsyncSession, user_id: int, start: dt.datetime | None, end: dt.datetime
) -> dict[str, int]:
    row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(func.coalesce(Listen.duration_ms, ASSUMED_DURATION_MS)), 0),
                func.count(func.distinct(Listen.artist_key)),
                func.count(func.distinct(tuple_(Listen.artist_key, Listen.track_key))),
            ).where(*_in(user_id, start, end))
        )
    ).one()
    return {"listens": row[0], "listening_ms": int(row[1]), "artists": row[2], "tracks": row[3]}


async def new_artists(
    db: AsyncSession, user_id: int, start: dt.datetime | None, end: dt.datetime
) -> int:
    """Artists first listened to in the period."""
    if start is None:
        return 0
    firsts = (
        select(func.min(Listen.listened_at).label("first"))
        .where(Listen.user_id == user_id, Listen.duplicate_of.is_(None))
        .group_by(Listen.artist_key)
        .subquery()
    )
    return (
        await db.scalar(select(func.count()).where(firsts.c.first >= start, firsts.c.first < end))
        or 0
    )


@dataclass
class Top:
    keys: tuple[str, ...]
    name: str
    artist: str | None
    listens: int
    previous_rank: int | None = None
    rating_key: int | None = None
    thumb: str | None = None


_GROUPS: dict[str, tuple[Any, ...]] = {
    "artist": (Listen.artist_key,),
    "album": (Listen.artist_key, Listen.album_key),
    "track": (Listen.artist_key, Listen.track_key),
}
_NAMES = {"artist": Listen.artist_name, "album": Listen.album_name, "track": Listen.track_name}


async def top(
    db: AsyncSession,
    user_id: int,
    kind: Literal["artist", "album", "track"],
    start: dt.datetime | None,
    end: dt.datetime,
    limit: int,
) -> list[Top]:
    """Most-played artists, albums or tracks, named by their most common spelling."""
    group = _GROUPS[kind]
    conditions = _in(user_id, start, end)
    if kind == "album":
        conditions.append(Listen.album_key.is_not(None))
    listens = func.count().label("listens")
    rows = await db.execute(
        select(
            *group,
            func.mode().within_group(_NAMES[kind]),
            func.mode().within_group(Listen.artist_name),
            listens,
        )
        .where(*conditions)
        .group_by(*group)
        .order_by(listens.desc(), *group)
        .limit(limit)
    )
    out = []
    for row in rows.all():
        keys = tuple(row[: len(group)])
        name, artist, count = row[len(group) :]
        out.append(Top(keys, name, None if kind == "artist" else artist, count))
    return out


async def previous_ranks(
    db: AsyncSession,
    user_id: int,
    kind: Literal["artist", "album", "track"],
    start: dt.datetime,
    end: dt.datetime,
    items: list[Top],
) -> None:
    """Fill in where each item ranked in the previous period (None: not played then)."""
    if not items:
        return
    group = _GROUPS[kind]
    counts = (
        select(*group, func.count().label("n"))
        .where(*_in(user_id, start, end))
        .group_by(*group)
        .subquery()
    )
    key_columns = [counts.c[c.key] for c in group]
    ranked = select(
        *key_columns, func.rank().over(order_by=counts.c.n.desc()).label("rank")
    ).subquery()
    wanted = [i.keys for i in items]
    filter_cols = [ranked.c[c.key] for c in group]
    target = (
        filter_cols[0].in_([k[0] for k in wanted])
        if len(group) == 1
        else tuple_(*filter_cols).in_(wanted)
    )
    rows = await db.execute(select(*filter_cols, ranked.c.rank).where(target))
    ranks = {tuple(r[:-1]): int(r[-1]) for r in rows.all()}
    for item in items:
        item.previous_rank = ranks.get(item.keys)


async def link_library(
    db: AsyncSession, user_id: int, server_id: int, kind: str, items: list[Top]
) -> None:
    """Attach the matching library item (and its artwork) where one exists for this user."""
    if not items:
        return
    visible = and_(
        UserItemState.user_id == user_id,
        UserItemState.server_id == server_id,
    )
    if kind == "artist":
        rows = await db.execute(
            select(Artist.rating_key, Artist.title, Artist.thumb)
            .join(
                UserItemState,
                and_(visible, UserItemState.rating_key == Artist.rating_key),
            )
            .where(Artist.server_id == server_id)
        )
        artists = {normalise_artist(t): (k, th) for k, t, th in rows.all()}
        for item in items:
            item.rating_key, item.thumb = artists.get(item.keys[0], (None, None))
        return
    album_artist = Artist.__table__.alias("album_artist")
    if kind == "album":
        album_rows = await db.execute(
            select(Album.rating_key, Album.title, album_artist.c.title, Album.thumb)
            .join(
                UserItemState,
                and_(visible, UserItemState.rating_key == Album.rating_key),
            )
            .outerjoin(
                album_artist,
                and_(
                    album_artist.c.server_id == Album.server_id,
                    album_artist.c.rating_key == Album.artist_rating_key,
                ),
            )
            .where(Album.server_id == server_id)
        )
        albums: dict[tuple[str, ...], tuple[int, str | None]] = {}
        for key, title, artist, thumb in album_rows.all():
            albums.setdefault(
                (normalise_artist(artist or ""), normalise_title(title)), (key, thumb)
            )
        for item in items:
            item.rating_key, item.thumb = albums.get(item.keys, (None, None))
        return
    # Tracks: only look at tracks with the wanted titles; link to their album.
    track_rows = await db.execute(
        select(
            Track.title_norm,
            Track.original_title,
            album_artist.c.title,
            Track.album_rating_key,
            Album.thumb,
        )
        .join(UserItemState, and_(visible, UserItemState.rating_key == Track.rating_key))
        .outerjoin(
            Album,
            and_(Album.server_id == Track.server_id, Album.rating_key == Track.album_rating_key),
        )
        .outerjoin(
            album_artist,
            and_(
                album_artist.c.server_id == Track.server_id,
                album_artist.c.rating_key == Track.artist_rating_key,
            ),
        )
        .where(Track.server_id == server_id, Track.title_norm.in_({i.keys[1] for i in items}))
        .order_by(Track.album_rating_key)
    )
    tracks: dict[tuple[str, ...], tuple[int | None, str | None]] = {}
    for title_norm, original, artist, album_key, thumb in track_rows.all():
        for name in (original, artist):
            if name:
                tracks.setdefault((normalise_artist(name), title_norm), (album_key, thumb))
    for item in items:
        item.rating_key, item.thumb = tracks.get(item.keys, (None, None))


async def listening_days(db: AsyncSession, user_id: int, tz: ZoneInfo) -> list[dt.date]:
    rows = await db.scalars(
        select(func.distinct(func.date(func.timezone(tz.key, Listen.listened_at)))).where(
            Listen.user_id == user_id, Listen.duplicate_of.is_(None)
        )
    )
    return sorted(rows)


def streaks(days: Iterable[dt.date], today: dt.date) -> tuple[int, int]:
    """(current, longest) runs of consecutive listening days. Current counts up to today,
    or yesterday if nothing has been played yet today."""
    longest = run = 0
    previous: dt.date | None = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == dt.timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    current = run if previous is not None and (today - previous).days <= 1 else 0
    return current, longest


async def activity(
    db: AsyncSession, user_id: int, period: Period, tz: ZoneInfo
) -> tuple[list[tuple[dt.date, int]], list[list[int]]]:
    """Listens per bucket (zeros included) and a weekday x hour grid (Monday first)."""
    local = func.timezone(tz.key, Listen.listened_at)
    bucket = func.date_trunc(period.bucket, local)
    conditions = _in(user_id, period.start, period.end)
    rows = await db.execute(
        select(bucket, func.count()).where(*conditions).group_by(bucket).order_by(bucket)
    )
    counts = {r[0].date(): r[1] for r in rows.all()}
    grid = [[0] * 24 for _ in range(7)]
    cells = await db.execute(
        select(
            func.extract("isodow", local).label("dow"),
            func.extract("hour", local).label("hour"),
            func.count(),
        )
        .where(*conditions)
        .group_by(text("dow"), text("hour"))
    )
    for dow, hour, n in cells.all():
        grid[int(dow) - 1][int(hour)] = n

    first = period.start.astimezone(tz).date() if period.start else min(counts, default=None)
    if first is None:
        return [], grid
    last = period.end.astimezone(tz).date()
    points = []
    cursor = _truncate(first, period.bucket)
    while cursor <= last:
        points.append((cursor, counts.get(cursor, 0)))
        cursor = _next(cursor, period.bucket)
    return points, grid


def _truncate(day: dt.date, bucket: Bucket) -> dt.date:
    if bucket == "week":
        return day - dt.timedelta(days=day.weekday())
    if bucket == "month":
        return day.replace(day=1)
    return day


def _next(day: dt.date, bucket: Bucket) -> dt.date:
    if bucket == "day":
        return day + dt.timedelta(days=1)
    if bucket == "week":
        return day + dt.timedelta(days=7)
    return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


@dataclass
class ItemListens:
    listens: int
    first_at: dt.datetime | None
    last_at: dt.datetime | None
    years: list[tuple[int, int]]
    songs: list[tuple[str, int]]
    by_track_key: dict[str, int]


async def item_listens(
    db: AsyncSession,
    user_id: int,
    tz: ZoneInfo,
    artist_keys: set[str],
    album_key: str | None = None,
) -> ItemListens:
    """A user's listens of one artist (or one of their albums), matched by normalised names."""
    conditions = [
        Listen.user_id == user_id,
        Listen.duplicate_of.is_(None),
        Listen.artist_key.in_(artist_keys),
    ]
    if album_key is not None:
        conditions.append(Listen.album_key == album_key)
    total, first, last = (
        await db.execute(
            select(func.count(), func.min(Listen.listened_at), func.max(Listen.listened_at)).where(
                *conditions
            )
        )
    ).one()
    if not total:
        return ItemListens(0, None, None, [], [], {})

    year = func.extract("year", func.timezone(tz.key, Listen.listened_at))
    per_year = dict(
        (int(y), n)
        for y, n in (
            await db.execute(select(year, func.count()).where(*conditions).group_by(year))
        ).all()
    )
    years = [(y, per_year.get(y, 0)) for y in range(min(per_year), max(per_year) + 1)]

    count = func.count().label("n")
    tracks = (
        await db.execute(
            select(Listen.track_key, func.mode().within_group(Listen.track_name), count)
            .where(*conditions)
            .group_by(Listen.track_key)
            .order_by(count.desc(), Listen.track_key)
        )
    ).all()
    return ItemListens(
        listens=total,
        first_at=first,
        last_at=last,
        years=years,
        songs=[(name, n) for _, name, n in tracks[:10]],
        by_track_key={key: n for key, _, n in tracks},
    )
