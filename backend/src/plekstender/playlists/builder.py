"""Turning a playlist definition into songs, plus the helpers the builder UI uses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.playlists.definition import Definition
from plekstender.sync.parse import normalise_artist

# Songs released more than once count once (earliest release); a song's rating is its
# best-rated release's. Popularity rank is within each artist (0 = most popular).
_SONGS = """
    WITH songs AS (
        SELECT t.rating_key, t.title, t.artist_rating_key, t.album_rating_key,
               t.duration_ms, coalesce(t.popularity, 0) AS popularity,
               coalesce(t.original_title, ar.title) AS artist_name, al.title AS album_title,
               al.thumb,
               max(s.user_rating) OVER song AS rating,
               row_number() OVER (song ORDER BY al.year NULLS LAST, t.rating_key) AS release,
               t.rating_key = ANY(:tracks) AS chosen,
               cardinality(CAST(:tags AS integer[])) = 0 OR EXISTS (
                   SELECT 1 FROM item_tags it
                   WHERE it.server_id = t.server_id AND it.tag_id = ANY(CAST(:tags AS integer[]))
                     AND it.rating_key IN (t.album_rating_key, t.artist_rating_key)
               ) AS tagged
        FROM tracks t
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = t.server_id AND s.rating_key = t.rating_key
        LEFT JOIN albums al ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
        LEFT JOIN artists ar ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
        WHERE t.server_id = :server_id AND t.section_key = ANY(:sections)
          AND (t.artist_rating_key = ANY(:artists) OR t.rating_key = ANY(:tracks))
        WINDOW song AS (PARTITION BY t.artist_rating_key, t.title_norm)
    ),
    ranked AS (
        SELECT *, percent_rank() OVER (
            PARTITION BY artist_rating_key ORDER BY popularity DESC, rating_key
        ) AS pct
        FROM songs WHERE release = 1 OR chosen
    )
    SELECT * FROM ranked
"""


@dataclass
class Song:
    rating_key: int
    title: str
    artist_key: int | None
    artist: str | None
    album: str | None
    thumb: str | None
    duration_ms: int | None
    popularity: int
    rating: float | None


def _passes(row: Any, d: Definition) -> bool:
    if row.chosen:
        return True
    r = d.rules
    if not row.tagged:
        return False
    checks = []
    if r.min_rating is not None:
        checks.append(
            (row.rating is not None and row.rating >= r.min_rating)
            or (row.rating is None and r.include_unrated)
        )
    if r.top_percent is not None:
        # percent_rank is 0 for the most popular song, 1 for the least.
        checks.append(row.pct * 100 < r.top_percent or row.pct == 0)
    if not checks:
        return True
    return all(checks) if r.op == "AND" else any(checks)


async def matching_songs(
    db: AsyncSession, *, user_id: int, server_id: int, sections: list[str], d: Definition
) -> list[Song]:
    """Every song the definition selects (before the limit), most popular first."""
    rows = await db.execute(
        text(_SONGS),
        {
            "user_id": user_id,
            "server_id": server_id,
            "sections": sections,
            "artists": d.artists,
            "tracks": d.tracks,
            "tags": d.rules.tags,
        },
    )
    songs = [
        Song(
            rating_key=r.rating_key,
            title=r.title,
            artist_key=r.artist_rating_key,
            artist=r.artist_name,
            album=r.album_title,
            thumb=r.thumb,
            duration_ms=r.duration_ms,
            popularity=r.popularity,
            rating=r.rating,
        )
        for r in rows.all()
        if _passes(r, d)
    ]
    songs.sort(key=lambda s: (-s.popularity, s.rating_key))
    return songs


def pick(songs: list[Song], d: Definition, rng: Any) -> list[Song]:
    """Apply the order and the limit: the most popular N, or a random N in random order."""
    if d.order == "popular":
        return songs[: d.limit] if d.limit else songs
    chosen = rng.sample(songs, min(len(songs), d.limit)) if d.limit else list(songs)
    rng.shuffle(chosen)
    return chosen


# --- builder helpers -------------------------------------------------------------------------


_SEARCH_ARTISTS = """
    SELECT ar.rating_key, ar.title, ar.thumb, ar.popularity
    FROM artists ar
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = ar.server_id AND s.rating_key = ar.rating_key
    WHERE ar.server_id = :server_id AND ar.section_key = ANY(:sections)
      AND ar.title ILIKE :like ESCAPE '\\'
    ORDER BY (lower(ar.title) = lower(:q)) DESC, ar.popularity DESC NULLS LAST, ar.title
    LIMIT 8
"""
_SEARCH_TRACKS = """
    SELECT DISTINCT ON (t.artist_rating_key, t.title_norm)
           t.rating_key, t.title, coalesce(t.original_title, ar.title) AS artist,
           al.thumb, t.popularity
    FROM tracks t
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = t.server_id AND s.rating_key = t.rating_key
    LEFT JOIN albums al ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
    LEFT JOIN artists ar ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
    WHERE t.server_id = :server_id AND t.section_key = ANY(:sections)
      AND t.title ILIKE :like ESCAPE '\\'
    ORDER BY t.artist_rating_key, t.title_norm, al.year NULLS LAST
"""


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search(
    db: AsyncSession, *, user_id: int, server_id: int, sections: list[str], q: str
) -> tuple[list[Any], list[Any]]:
    params = {
        "user_id": user_id,
        "server_id": server_id,
        "sections": sections,
        "like": _like(q),
        "q": q,
    }
    artists = (await db.execute(text(_SEARCH_ARTISTS), params)).all()
    tracks = (await db.execute(text(_SEARCH_TRACKS), params)).all()
    tracks = sorted(tracks, key=lambda t: -(t.popularity or 0))[:12]
    return list(artists), tracks


async def top_artists_for_tag(
    db: AsyncSession, *, user_id: int, server_id: int, sections: list[str], tag_id: int
) -> list[Any]:
    """A genre's, style's or mood's top 10 artists (tagged themselves or on an album), by your
    plays and then popularity."""
    rows = await db.execute(
        text(
            """
            SELECT ar.rating_key, ar.title, ar.thumb, ar.popularity,
                   greatest(s.view_count, s.listens) AS plays
            FROM artists ar
            JOIN user_item_state s
              ON s.user_id = :user_id AND s.server_id = ar.server_id
             AND s.rating_key = ar.rating_key
            WHERE ar.server_id = :server_id AND ar.section_key = ANY(:sections)
              AND lower(btrim(ar.title)) NOT IN ('various artists', 'various', 'va')
              AND EXISTS (
                  SELECT 1 FROM item_tags it
                  WHERE it.server_id = ar.server_id AND it.tag_id = :tag_id
                    AND (it.rating_key = ar.rating_key OR it.rating_key IN (
                        SELECT al.rating_key FROM albums al
                        WHERE al.server_id = ar.server_id
                          AND al.artist_rating_key = ar.rating_key
                    ))
              )
            ORDER BY plays DESC, ar.popularity DESC NULLS LAST, ar.title
            LIMIT 10
            """
        ),
        {"user_id": user_id, "server_id": server_id, "sections": sections, "tag_id": tag_id},
    )
    return list(rows.all())


async def similar_artists(
    db: AsyncSession,
    *,
    user_id: int,
    server_id: int,
    sections: list[str],
    artists: list[int],
    limit: int = 20,
) -> list[Any]:
    """Library artists Plex lists as similar to the chosen ones (shared by most first)."""
    if not artists:
        return []
    rows = await db.execute(
        text(
            "SELECT name, position FROM similar_artists "
            "WHERE server_id = :server_id AND artist_rating_key = ANY(:artists)"
        ),
        {"server_id": server_id, "artists": artists},
    )
    score: dict[str, tuple[int, int]] = {}
    for name, position in rows.all():
        key = normalise_artist(name)
        shared, best = score.get(key, (0, position))
        score[key] = (shared + 1, min(best, position))
    if not score:
        return []
    found = await db.execute(
        text(
            """
            SELECT DISTINCT ON (ar.name_key) ar.rating_key, ar.title, ar.thumb, ar.name_key
            FROM artists ar
            JOIN user_item_state s
              ON s.user_id = :user_id AND s.server_id = ar.server_id
             AND s.rating_key = ar.rating_key
            WHERE ar.server_id = :server_id AND ar.section_key = ANY(:sections)
              AND ar.name_key = ANY(:keys) AND NOT (ar.rating_key = ANY(:artists))
            ORDER BY ar.name_key, ar.popularity DESC NULLS LAST
            """
        ),
        {
            "user_id": user_id,
            "server_id": server_id,
            "sections": sections,
            "keys": list(score),
            "artists": artists,
        },
    )
    out = sorted(found.all(), key=lambda r: (-score[r.name_key][0], score[r.name_key][1], r.title))
    return out[:limit]


async def visible_items(
    db: AsyncSession,
    *,
    user_id: int,
    server_id: int,
    sections: list[str],
    artist_keys: list[int],
    track_keys: list[int],
) -> tuple[list[Any], list[Any]]:
    """The given artists and songs, as far as the user can see them (for names and art)."""
    params = {
        "user_id": user_id,
        "server_id": server_id,
        "sections": sections,
        "artists": artist_keys,
        "tracks": track_keys,
    }
    artists = await db.execute(
        text(
            """
            SELECT ar.rating_key, ar.title, ar.thumb, ar.popularity
            FROM artists ar
            JOIN user_item_state s
              ON s.user_id = :user_id AND s.server_id = ar.server_id
             AND s.rating_key = ar.rating_key
            WHERE ar.server_id = :server_id AND ar.section_key = ANY(:sections)
              AND ar.rating_key = ANY(:artists)
            """
        ),
        params,
    )
    tracks = await db.execute(
        text(
            """
            SELECT t.rating_key, t.title, coalesce(t.original_title, ar.title) AS artist,
                   t.artist_rating_key, al.thumb, t.popularity
            FROM tracks t
            JOIN user_item_state s
              ON s.user_id = :user_id AND s.server_id = t.server_id
             AND s.rating_key = t.rating_key
            LEFT JOIN albums al
              ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
            LEFT JOIN artists ar
              ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
            WHERE t.server_id = :server_id AND t.section_key = ANY(:sections)
              AND t.rating_key = ANY(:tracks)
            """
        ),
        params,
    )
    return list(artists.all()), list(tracks.all())
