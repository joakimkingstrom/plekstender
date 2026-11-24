"""Looking back over a user's whole listening history: on this day in earlier years, how
their taste changed year by year, and songs whose ratings and plays disagree.

Listens are matched to the library by normalised names, the way `listen_counts` does it, so
imported plays (Last.fm, ListenBrainz) count as well as Plex's own.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# --- on this day ---------------------------------------------------------------------------


@dataclass
class DayArtist:
    name: str
    listens: int
    rating_key: int | None = None
    thumb: str | None = None


@dataclass
class DayTrack:
    name: str
    artist: str
    listens: int


@dataclass
class DayYear:
    year: int
    listens: int
    artists: list[DayArtist]
    tracks: list[DayTrack]
    album: str | None = None
    album_rating_key: int | None = None
    album_thumb: str | None = None
    # Normalised names, to find these in the library.
    artist_keys: list[str] = field(default_factory=list, repr=False)
    album_match: tuple[str, str] | None = field(default=None, repr=False)


_DAY = text(
    """
    SELECT extract(year FROM timezone(:tz, listened_at))::int AS year,
           artist_name, artist_key, track_name, track_key, album_name, album_key
    FROM listens
    WHERE user_id = :user_id AND duplicate_of IS NULL
      AND listened_at < :before
      AND extract(month FROM timezone(:tz, listened_at)) = :month
      AND extract(day FROM timezone(:tz, listened_at)) = :day
    """
)

# A library artist per normalised name (the user can see it), for links and pictures.
_ARTISTS_BY_KEY = text(
    """
    SELECT DISTINCT ON (ar.name_key) ar.name_key, ar.rating_key, ar.thumb
    FROM artists ar
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = ar.server_id AND s.rating_key = ar.rating_key
    WHERE ar.server_id = :server_id AND ar.name_key = ANY(:keys)
    ORDER BY ar.name_key, ar.popularity DESC NULLS LAST, ar.rating_key
    """
)

_ALBUM_BY_KEY = text(
    """
    SELECT al.rating_key, al.thumb
    FROM albums al
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = al.server_id AND s.rating_key = al.rating_key
    JOIN artists ar ON ar.server_id = al.server_id AND ar.rating_key = al.artist_rating_key
    WHERE al.server_id = :server_id AND al.title_norm = :album_key AND ar.name_key = :artist_key
    ORDER BY al.year NULLS LAST, al.rating_key
    LIMIT 1
    """
)


def _most(names: list[str]) -> str:
    """The spelling seen most often (names differ in case and punctuation between sources)."""
    return Counter(names).most_common(1)[0][0]


async def on_this_day(
    db: AsyncSession,
    user_id: int,
    server_id: int | None,
    tz: ZoneInfo,
    today: dt.date,
    max_years: int = 10,
) -> list[DayYear]:
    """What the user played on today's date in earlier years, most recent year first."""
    before = dt.datetime(today.year, 1, 1, tzinfo=tz)
    rows = (
        await db.execute(
            _DAY,
            {
                "user_id": user_id,
                "tz": tz.key,
                "before": before,
                "month": today.month,
                "day": today.day,
            },
        )
    ).all()
    by_year: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        by_year[row.year].append(row)

    years: list[DayYear] = []
    for year in sorted(by_year, reverse=True)[:max_years]:
        plays = by_year[year]
        artist_names: dict[str, list[str]] = defaultdict(list)
        track_names: dict[tuple[str, str], list[str]] = defaultdict(list)
        album_names: dict[tuple[str, str], list[str]] = defaultdict(list)
        for p in plays:
            artist_names[p.artist_key].append(p.artist_name)
            track_names[(p.artist_key, p.track_key)].append(p.track_name)
            if p.album_key:
                album_names[(p.artist_key, p.album_key)].append(p.album_name)
        top_artists = sorted(artist_names.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:3]
        top_tracks = sorted(track_names.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]
        entry = DayYear(
            year=year,
            listens=len(plays),
            artists=[DayArtist(_most(names), len(names)) for _, names in top_artists],
            tracks=[
                DayTrack(_most(names), _most(artist_names[key[0]]), len(names))
                for key, names in top_tracks
            ],
            artist_keys=[key for key, _ in top_artists],
        )
        if album_names:
            (artist_key, album_key), names = max(
                album_names.items(), key=lambda kv: (len(kv[1]), kv[0])
            )
            entry.album = _most(names)
            entry.album_match = (artist_key, album_key)
        years.append(entry)

    if server_id is not None and years:
        keys = sorted({k for y in years for k in y.artist_keys})
        found = {
            r.name_key: r
            for r in (
                await db.execute(
                    _ARTISTS_BY_KEY, {"user_id": user_id, "server_id": server_id, "keys": keys}
                )
            ).all()
        }
        for y in years:
            for artist, key in zip(y.artists, y.artist_keys, strict=True):
                if (match := found.get(key)) is not None:
                    artist.rating_key, artist.thumb = match.rating_key, match.thumb
            if (match_keys := y.album_match) is not None:
                album = (
                    await db.execute(
                        _ALBUM_BY_KEY,
                        {
                            "user_id": user_id,
                            "server_id": server_id,
                            "artist_key": match_keys[0],
                            "album_key": match_keys[1],
                        },
                    )
                ).first()
                if album is not None:
                    y.album_rating_key, y.album_thumb = album.rating_key, album.thumb
    return years


# --- taste over time -----------------------------------------------------------------------


@dataclass
class Share:
    name: str
    # Per listening year (same order as `Taste.years`): the share of that year's matched
    # listens, 0-1.
    shares: list[float]


@dataclass
class Taste:
    years: list[int]
    listens: list[int]  # all listens per year
    genres: list[Share]
    genre_matched: list[int]  # listens per year whose artist has a genre
    decades: list[Share]
    decade_matched: list[int]  # listens per year matched to a song with a release year
    median_release: list[int | None]  # the median release year of what was played


_YEAR_LISTENS = """
    yl AS (
        SELECT extract(year FROM timezone(:tz, listened_at))::int AS year,
               artist_key, track_key, count(*) AS n
        FROM listens
        WHERE user_id = :user_id AND duplicate_of IS NULL
        GROUP BY 1, 2, 3
    )
"""

# Each listen counts towards its artist's genres, split evenly when an artist has several.
_GENRES = text(
    f"""
    WITH {_YEAR_LISTENS},
    ya AS (SELECT year, artist_key, sum(n) AS n FROM yl GROUP BY 1, 2),
    artist_genres AS (
        SELECT DISTINCT ar.name_key, tg.name
        FROM artists ar
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = ar.server_id AND s.rating_key = ar.rating_key
        JOIN item_tags it ON it.server_id = ar.server_id AND it.rating_key = ar.rating_key
        JOIN tags tg ON tg.id = it.tag_id AND tg.kind = :tag_kind
        WHERE ar.server_id = :server_id AND ar.name_key IS NOT NULL
    ),
    weights AS (
        SELECT name_key, name, 1.0 / count(*) OVER (PARTITION BY name_key) AS w
        FROM artist_genres
    )
    SELECT ya.year, w.name, sum(ya.n * w.w)::float AS listens
    FROM ya JOIN weights w ON w.name_key = ya.artist_key
    GROUP BY 1, 2
    """  # noqa: S608 - constant SQL fragment
)

_YEAR_TOTALS = text(
    f"""
    WITH {_YEAR_LISTENS},
    genred AS (
        SELECT DISTINCT ar.name_key
        FROM artists ar
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = ar.server_id AND s.rating_key = ar.rating_key
        JOIN item_tags it ON it.server_id = ar.server_id AND it.rating_key = ar.rating_key
        JOIN tags tg ON tg.id = it.tag_id AND tg.kind = :tag_kind
        WHERE ar.server_id = :server_id AND ar.name_key IS NOT NULL
    )
    SELECT year, sum(n)::int AS listens,
           coalesce(sum(n) FILTER (WHERE artist_key IN (SELECT name_key FROM genred)), 0)::int
               AS with_genre
    FROM yl GROUP BY year ORDER BY year
    """  # noqa: S608 - constant SQL fragment
)

# A song's release year: the earliest year of the albums it's on (a remaster's album says
# when the remaster came out).
_RELEASES = text(
    f"""
    WITH {_YEAR_LISTENS},
    songs AS (SELECT DISTINCT artist_key, track_key FROM yl),
    released AS (
        SELECT so.artist_key, so.track_key, min(al.year) AS released
        FROM songs so
        JOIN tracks t ON t.server_id = :server_id AND t.title_norm = so.track_key
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = t.server_id AND s.rating_key = t.rating_key
        LEFT JOIN artists ar ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
        JOIN albums al ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
        WHERE so.artist_key IN (t.artist_key, ar.name_key) AND al.year > 1000
        GROUP BY 1, 2
    )
    SELECT yl.year, r.released, sum(yl.n)::int AS listens
    FROM yl JOIN released r USING (artist_key, track_key)
    GROUP BY 1, 2
    """  # noqa: S608 - constant SQL fragment
)


def _median(counts: dict[int, int]) -> int | None:
    """The median of values weighted by how often each occurs."""
    total = sum(counts.values())
    if not total:
        return None
    seen = 0
    for value in sorted(counts):
        seen += counts[value]
        if seen * 2 >= total:
            return value
    return None  # pragma: no cover - unreachable


TagKind = Literal["genre", "style", "mood"]


async def taste(
    db: AsyncSession,
    user_id: int,
    server_id: int,
    tz: ZoneInfo,
    top_genres: int = 8,
    tag_kind: TagKind = "genre",
) -> Taste:
    """The user's genres (or the artists' styles or moods, in Plex) and the decades their
    music is from, per listening year."""
    params = {"user_id": user_id, "server_id": server_id, "tz": tz.key, "tag_kind": tag_kind}
    totals = (await db.execute(_YEAR_TOTALS, params)).all()
    years = [r.year for r in totals]
    index = {year: i for i, year in enumerate(years)}

    genre_rows = (await db.execute(_GENRES, params)).all()
    genre_matched = [r.with_genre for r in totals]
    overall: Counter[str] = Counter()
    for r in genre_rows:
        overall[r.name] += r.listens
    top = [name for name, _ in overall.most_common(top_genres)]
    genres = {name: [0.0] * len(years) for name in top}
    for r in genre_rows:
        if r.name in genres and genre_matched[index[r.year]]:
            genres[r.name][index[r.year]] = r.listens / genre_matched[index[r.year]]

    release_rows = (await db.execute(_RELEASES, params)).all()
    per_year: dict[int, dict[int, int]] = defaultdict(dict)
    for r in release_rows:
        per_year[r.year][r.released] = r.listens
    decade_matched = [sum(per_year[y].values()) for y in years]
    decade_counts: dict[int, list[int]] = defaultdict(lambda: [0] * len(years))
    for year, released in per_year.items():
        for release_year, n in released.items():
            decade_counts[release_year // 10 * 10][index[year]] += n
    decades = [
        Share(
            f"{decade}s",
            [n / decade_matched[i] if decade_matched[i] else 0.0 for i, n in enumerate(counts)],
        )
        for decade, counts in sorted(decade_counts.items())
    ]
    return Taste(
        years=years,
        listens=[r.listens for r in totals],
        genres=[Share(name, genres[name]) for name in top],
        genre_matched=genre_matched,
        decades=decades,
        decade_matched=decade_matched,
        median_release=[_median(per_year[y]) for y in years],
    )


# --- rediscover ----------------------------------------------------------------------------

LOVED = 9  # 4½ stars or more
DISLIKED = 4  # 2 stars or less
FORGOTTEN_AFTER = dt.timedelta(days=365)
HARDLY_PLAYED = 2  # plays at most, for "loved but hardly played"
PLAYED_A_LOT = 5  # plays at least, for "played a lot but rated low"

RediscoverKind = Literal["forgotten", "unplayed", "disliked"]


@dataclass
class RediscoverSong:
    rating_key: int
    title: str
    artist: str | None
    album: str | None
    album_rating_key: int | None
    thumb: str | None
    rating: float | None
    plays: int
    last_played_at: dt.datetime | None


# One row per song: releases of the same song (by artist and normalised title) count
# together, shown as the best-rated release. Plays: Plex's count or the whole history's,
# whichever is higher (the history includes Plex's plays, when they were captured).
_SONGS = """
    WITH songs AS (
        SELECT (array_agg(t.rating_key ORDER BY s.user_rating DESC NULLS LAST,
                          al.year NULLS LAST, t.rating_key))[1] AS rating_key,
               max(s.user_rating) AS rating,
               greatest(sum(s.view_count), max(s.listens))::int AS plays,
               greatest(max(s.last_viewed_at), max(s.last_listened_at)) AS last_played_at,
               max(t.popularity) AS popularity
        FROM tracks t
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = t.server_id AND s.rating_key = t.rating_key
        LEFT JOIN albums al ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
        WHERE t.server_id = :server_id AND t.section_key = ANY(:sections)
        GROUP BY t.artist_rating_key, t.title_norm
    )
"""

_REDISCOVER_WHERE: dict[RediscoverKind, tuple[str, str]] = {
    # Loved, played before, but not for a year.
    "forgotten": (
        "so.rating >= :loved AND so.last_played_at < :cutoff",
        "so.plays DESC, so.last_played_at, so.rating_key",
    ),
    # Loved, but hardly ever played (and not already a forgotten favourite).
    "unplayed": (
        "so.rating >= :loved AND so.plays <= :hardly"
        " AND (so.last_played_at IS NULL OR so.last_played_at >= :cutoff)",
        "so.rating DESC, so.popularity DESC NULLS LAST, so.rating_key",
    ),
    # Played a lot, but rated low.
    "disliked": (
        "so.rating <= :disliked AND so.plays >= :a_lot",
        "so.plays DESC, so.rating, so.rating_key",
    ),
}


def _params(user_id: int, server_id: int, sections: list[str], now: dt.datetime) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "server_id": server_id,
        "sections": sections,
        "loved": LOVED,
        "disliked": DISLIKED,
        "hardly": HARDLY_PLAYED,
        "a_lot": PLAYED_A_LOT,
        "cutoff": now - FORGOTTEN_AFTER,
    }


async def rediscover(
    db: AsyncSession,
    user_id: int,
    server_id: int,
    sections: list[str],
    kind: RediscoverKind,
    limit: int = 20,
    now: dt.datetime | None = None,
) -> tuple[list[RediscoverSong], int]:
    """Songs in one of the Rediscover lists, and how many there are in all."""
    where, order = _REDISCOVER_WHERE[kind]
    query = text(
        f"""{_SONGS}
        SELECT so.rating_key, so.rating, so.plays, so.last_played_at,
               t.title, coalesce(t.original_title, ar.title) AS artist,
               al.title AS album, al.rating_key AS album_rating_key, al.thumb,
               count(*) OVER () AS total
        FROM songs so
        JOIN tracks t ON t.server_id = :server_id AND t.rating_key = so.rating_key
        LEFT JOIN albums al ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
        LEFT JOIN artists ar ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
        WHERE {where}
        ORDER BY {order}
        LIMIT :limit
        """  # noqa: S608 - constant SQL fragments
    )
    params = _params(user_id, server_id, sections, now or dt.datetime.now(dt.UTC))
    rows = (await db.execute(query, params | {"limit": limit})).all()
    songs = [
        RediscoverSong(
            rating_key=r.rating_key,
            title=r.title,
            artist=r.artist,
            album=r.album,
            album_rating_key=r.album_rating_key,
            thumb=r.thumb,
            rating=r.rating,
            plays=r.plays,
            last_played_at=r.last_played_at,
        )
        for r in rows
    ]
    return songs, rows[0].total if rows else 0


async def forgotten_keys(
    db: AsyncSession,
    user_id: int,
    server_id: int,
    sections: list[str],
    now: dt.datetime | None = None,
) -> list[int]:
    """Every forgotten favourite (one release per song), for the playlist source."""
    where, _ = _REDISCOVER_WHERE["forgotten"]
    query = text(f"{_SONGS} SELECT so.rating_key FROM songs so WHERE {where}")  # noqa: S608
    params = _params(user_id, server_id, sections, now or dt.datetime.now(dt.UTC))
    return list((await db.scalars(query, params)).all())
