"""Library → Stats: the library over time, music by decade, and words in names.

Like the rest of the library, everything counts only what the user's own Plex account can
see in the libraries they picked.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Path, Query
from sqlalchemy import extract, func, select

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.library import _album_count, _artist_summary, plays_of, visible
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    AlbumColourOut,
    ArtistCountriesOut,
    ArtistSummary,
    ColourFamily,
    ColoursOut,
    CountryArtists,
    DecadeCount,
    GrowthPoint,
    LibraryStatsOut,
    ReleasedAlbum,
    ReleasedOut,
    WordCount,
)
from plekstender.artist_countries import ArtistKey, countries_for
from plekstender.cover_colours import ORDER, family
from plekstender.db.models import Album, AlbumColour, Artist, Track, UserServer
from plekstender.library_stats import words

router = APIRouter(prefix="/api/servers/{server_id}/library/stats", tags=["library"])


def _month(value: dt.datetime) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _months(first: str, last: str) -> list[str]:
    year, month = int(first[:4]), int(first[5:])
    out = []
    while f"{year:04d}-{month:02d}" <= last:
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


@router.get("", response_model=LibraryStatsOut)
async def library_stats(server_id: int, db: DbDep, user: UserDep) -> LibraryStatsOut:
    """Totals, the library's growth (by when things were added to Plex, month by month)
    and albums per decade (by release year)."""
    us = await get_user_server(db, user, server_id)
    added: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for name, model in (("artists", Artist), ("albums", Album), ("tracks", Track)):
        state, cond, where = visible(model, user.id, us)
        month = func.date_trunc("month", model.added_at)
        rows = await db.execute(
            select(month, func.count())
            .select_from(model)
            .join(state, cond)
            .where(*where)
            .group_by(month)
        )
        totals[name] = 0
        for when, count in rows.all():
            totals[name] += count
            if when is not None:
                added.setdefault(_month(when), {}).setdefault(name, 0)
                added[_month(when)][name] += count

    growth = []
    if added:
        running = {"artists": 0, "albums": 0, "tracks": 0}
        for month_key in _months(min(added), max(added)):
            for name, count in added.get(month_key, {}).items():
                running[name] += count
            growth.append(GrowthPoint(month=month_key, **running))

    state, cond, where = visible(Album, user.id, us)
    decade = (Album.year // 10) * 10
    rows = await db.execute(
        select(decade, func.count())
        .select_from(Album)
        .join(state, cond)
        .where(*where)
        .group_by(decade)
        .order_by(decade.nulls_last())
    )
    decades, undated = [], 0
    for start, count in rows.all():
        if start is None or start < 1000:
            undated += count
        else:
            decades.append(DecadeCount(decade=int(start), albums=count))
    return LibraryStatsOut(
        artists=totals["artists"],
        albums=totals["albums"],
        tracks=totals["tracks"],
        growth=growth,
        decades=decades,
        undated_albums=undated,
    )


@router.get("/released", response_model=ReleasedOut)
async def released(
    server_id: int,
    db: DbDep,
    user: UserDep,
    decade: int | None = Query(default=None, ge=1000, le=2990),
    year: int | None = Query(default=None, ge=1000, le=2999),
) -> ReleasedOut:
    """A decade's albums by year, or a year's by month (albums with only a year count as
    "month unknown", month 0), with the year's albums listed."""
    us = await get_user_server(db, user, server_id)
    state, cond, where = visible(Album, user.id, us)
    if year is not None:
        month = func.coalesce(extract("month", Album.released), 0)
        rows = await db.execute(
            select(month, func.count())
            .select_from(Album)
            .join(state, cond)
            .where(*where, Album.year == year)
            .group_by(month)
        )
        counts = {int(m): c for m, c in rows.all()}
        artist = Artist.__table__.alias("owner")
        albums = await db.execute(
            select(
                Album.rating_key,
                Album.title,
                Album.thumb,
                Album.released,
                artist.c.title,
                plays_of(state),
            )
            .join(state, cond)
            .outerjoin(
                artist,
                (artist.c.server_id == Album.server_id)
                & (artist.c.rating_key == Album.artist_rating_key),
            )
            .where(*where, Album.year == year)
            .order_by(Album.released.nulls_last(), Album.title)
            .limit(300)
        )
        return ReleasedOut(
            buckets=[DecadeCount(decade=m, albums=counts.get(m, 0)) for m in range(13)],
            albums=[
                ReleasedAlbum(rating_key=k, title=t, thumb=th, released=r, artist_title=a, plays=p)
                for k, t, th, r, a, p in albums.all()
            ],
        )
    start = decade if decade is not None else 1990
    rows = await db.execute(
        select(Album.year, func.count())
        .select_from(Album)
        .join(state, cond)
        .where(*where, Album.year >= start, Album.year < start + 10)
        .group_by(Album.year)
    )
    counts = dict(rows.all())
    return ReleasedOut(
        buckets=[DecadeCount(decade=y, albums=counts.get(y, 0)) for y in range(start, start + 10)],
        albums=[],
    )


@router.get("/words", response_model=list[WordCount])
async def name_words(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: Literal["artist", "album", "track"] = "track",
    limit: int = Query(default=100, ge=10, le=300),
) -> list[WordCount]:
    """The most common words in artist, album or song names (in how many names each is)."""
    us = await get_user_server(db, user, server_id)
    model: type[Artist] | type[Album] | type[Track] = (
        Artist if kind == "artist" else Album if kind == "album" else Track
    )
    state, cond, where = visible(model, user.id, us)
    query = select(model.title).join(state, cond).where(*where)
    if kind == "track":
        # A song released more than once (remasters, compilations) counts once.
        query = (
            select(func.min(Track.title))
            .join(state, cond)
            .where(*where)
            .group_by(Track.artist_rating_key, Track.title_norm)
        )
    titles = list(await db.scalars(query))
    return [WordCount(word=w, count=n) for w, n in words(titles, limit)]


async def _artist_countries(
    db: DbDep, user_id: int, us: UserServer
) -> tuple[dict[int, str], int, int]:
    """The visible artists' countries, how many have none (yet) and how many wait for a
    MusicBrainz lookup."""
    state, cond, where = visible(Artist, user_id, us)
    rows = await db.execute(
        select(Artist.rating_key, Artist.title, Artist.mbid, Artist.name_key)
        .join(state, cond)
        .where(*where)
    )
    artists = [ArtistKey(*r) for r in rows.all()]
    found, pending = await countries_for(db, us.server_id, artists)
    return found, len(artists) - len(found), pending


@router.get("/countries", response_model=ArtistCountriesOut)
async def artist_countries(server_id: int, db: DbDep, user: UserDep) -> ArtistCountriesOut:
    """Artists per country: Plex's country for the artist, else MusicBrainz's (looked up a
    few hundred an hour by the worker)."""
    us = await get_user_server(db, user, server_id)
    found, unknown, pending = await _artist_countries(db, user.id, us)
    per: dict[str, int] = {}
    for code in found.values():
        per[code] = per.get(code, 0) + 1
    return ArtistCountriesOut(
        countries=[
            CountryArtists(country_code=c, artists=n)
            for c, n in sorted(per.items(), key=lambda x: (-x[1], x[0]))
        ],
        unknown=unknown,
        pending=pending,
    )


@router.get("/countries/{code}", response_model=list[ArtistSummary])
async def country_artists(
    server_id: int,
    db: DbDep,
    user: UserDep,
    code: str = Path(pattern=r"^[A-Za-z]{2}$"),
) -> list[ArtistSummary]:
    """A country's artists, most played first."""
    us = await get_user_server(db, user, server_id)
    found, _unknown, _pending = await _artist_countries(db, user.id, us)
    keys = [k for k, c in found.items() if c == code.upper()]
    if not keys:
        return []
    state, cond, where = visible(Artist, user.id, us)
    rows = await db.execute(
        select(Artist, state.user_rating, plays_of(state), _album_count(user.id, us))
        .join(state, cond)
        .where(*where, Artist.rating_key.in_(keys))
        .order_by(plays_of(state).desc(), func.lower(Artist.title))
        .limit(500)
    )
    return [_artist_summary(r) for r in rows.all()]


ColourName = Literal[
    "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "white", "grey", "black"
]
Coloured = tuple[str, float, float, str, int, str, str | None, str | None]


async def _coloured(db: DbDep, user_id: int, us: UserServer) -> tuple[list[Coloured], int]:
    """Visible albums with a cover colour, as (family, hue, lightness, hex, album, artist),
    and how many covers are still to be looked at."""
    state, cond, where = visible(Album, user_id, us)
    owner = Artist.__table__.alias("owner")
    rows = await db.execute(
        select(
            AlbumColour.hex,
            AlbumColour.hue,
            AlbumColour.saturation,
            AlbumColour.lightness,
            AlbumColour.thumb == Album.thumb,
            Album.rating_key,
            Album.title,
            Album.thumb,
            owner.c.title,
        )
        .select_from(Album)
        .join(state, cond)
        .outerjoin(
            AlbumColour,
            (AlbumColour.server_id == Album.server_id)
            & (AlbumColour.rating_key == Album.rating_key),
        )
        .outerjoin(
            owner,
            (owner.c.server_id == Album.server_id)
            & (owner.c.rating_key == Album.artist_rating_key),
        )
        .where(*where, Album.thumb.is_not(None))
    )
    out: list[Coloured] = []
    waiting = 0
    for hex_, hue, sat, light, current, key, title, thumb, artist in rows.all():
        if hex_ is None:
            waiting += 0 if current else 1  # looked at and unreadable: not waiting
            continue
        if not current:
            waiting += 1
        out.append((family(hue, sat, light), hue, light, hex_, key, title, thumb, artist))
    rank = {name: i for i, name in enumerate(ORDER)}
    out.sort(
        key=lambda x: (rank[x[0]], x[1] if x[0] not in ("white", "grey", "black") else 0, -x[2])
    )
    return out, waiting


@router.get("/colours", response_model=ColoursOut)
async def cover_colours(server_id: int, db: DbDep, user: UserDep) -> ColoursOut:
    """Covers' main colours by family (reds to pinks, then white, grey and black), and a
    strip of up to 360 of them in spectrum order."""
    us = await get_user_server(db, user, server_id)
    coloured, waiting = await _coloured(db, user.id, us)
    families: dict[str, list[str]] = {}
    for fam, _hue, _light, hex_, *_ in coloured:
        families.setdefault(fam, []).append(hex_)
    step = max(1, len(coloured) / 360)
    strip = [coloured[int(i * step)][3] for i in range(min(360, len(coloured)))]
    return ColoursOut(
        families=[
            ColourFamily(name=name, albums=len(hexes), hex=hexes[len(hexes) // 2])
            for name in ORDER
            if (hexes := families.get(name))
        ],
        strip=strip,
        albums=len(coloured),
        waiting=waiting,
    )


@router.get("/colours/{name}", response_model=list[AlbumColourOut])
async def colour_family(
    server_id: int,
    db: DbDep,
    user: UserDep,
    name: ColourName,
) -> list[AlbumColourOut]:
    """A colour family's covers, in spectrum order (up to 400)."""
    us = await get_user_server(db, user, server_id)
    coloured, _waiting = await _coloured(db, user.id, us)
    return [
        AlbumColourOut(rating_key=key, title=title, thumb=thumb, artist_title=artist, hex=hex_)
        for fam, _hue, _light, hex_, key, title, thumb, artist in coloured
        if fam == name
    ][:400]
