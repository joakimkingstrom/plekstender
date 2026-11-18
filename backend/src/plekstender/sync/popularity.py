"""Derived popularity for albums and artists.

Plex only has popularity (`ratingCount`, Last.fm listeners) on tracks, so:
- album popularity  = sum of its N most popular tracks (default 3)
- artist popularity = sum of its N most popular *songs* (default 10), where releases of
  the same song (remasters, compilations...) count once, using the most popular release.

Summing the top tracks (rather than all) keeps artists with huge discographies or many
compilations from outranking artists with a few very popular songs. N is set by admins
on the Settings page (see plekstender.db.instance.LibrarySettings).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.instance import LibrarySettings, get_library_settings

_ALBUMS = text(
    """
    UPDATE albums a SET popularity = s.p
    FROM (
        SELECT album_rating_key, sum(popularity) AS p
        FROM (
            SELECT album_rating_key, popularity,
                   row_number() OVER (PARTITION BY album_rating_key
                                      ORDER BY popularity DESC) AS rn
            FROM tracks
            WHERE server_id = :server_id AND popularity IS NOT NULL
        ) ranked
        WHERE rn <= :top_n
        GROUP BY album_rating_key
    ) s
    WHERE a.server_id = :server_id
      AND a.rating_key = s.album_rating_key
      AND a.popularity IS DISTINCT FROM s.p
    """
)

# Per-artist sum of the top N songs; shared by the update and the settings preview.
_ARTIST_SCORES = """
    SELECT artist_rating_key, sum(p) AS p
    FROM (
        SELECT artist_rating_key, p,
               row_number() OVER (PARTITION BY artist_rating_key ORDER BY p DESC) AS rn
        FROM (
            SELECT artist_rating_key, title_norm, max(popularity) AS p
            FROM tracks
            WHERE server_id = :server_id AND popularity IS NOT NULL
            GROUP BY artist_rating_key, title_norm
        ) songs
    ) ranked
    WHERE rn <= :top_n
    GROUP BY artist_rating_key
"""

_ARTISTS = text(
    f"""
    UPDATE artists a SET popularity = s.p
    FROM ({_ARTIST_SCORES}) s
    WHERE a.server_id = :server_id
      AND a.rating_key = s.artist_rating_key
      AND a.popularity IS DISTINCT FROM s.p
    """  # noqa: S608 - constant SQL fragment, values are bound parameters
)

# Top artists the user can see, scored with a candidate N, next to their current rank.
_PREVIEW = text(
    f"""
    WITH visible AS (
        SELECT a.rating_key, a.title, a.popularity
        FROM artists a
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = a.server_id AND s.rating_key = a.rating_key
        WHERE a.server_id = :server_id AND a.section_key = ANY(:sections)
    ),
    current AS (
        SELECT rating_key, rank() OVER (ORDER BY popularity DESC NULLS LAST) AS current_rank
        FROM visible
    )
    SELECT v.rating_key, v.title, scores.p AS popularity, c.current_rank
    FROM visible v
    JOIN ({_ARTIST_SCORES}) scores ON scores.artist_rating_key = v.rating_key
    JOIN current c ON c.rating_key = v.rating_key
    ORDER BY scores.p DESC, v.title
    LIMIT :limit
    """  # noqa: S608 - constant SQL fragment, values are bound parameters
)


@dataclass(frozen=True)
class PreviewRow:
    rating_key: int
    title: str
    popularity: int
    current_rank: int


async def recompute_popularity(
    db: AsyncSession, server_id: int, settings: LibrarySettings | None = None
) -> None:
    settings = settings or await get_library_settings(db)
    await db.execute(_ALBUMS, {"server_id": server_id, "top_n": settings.album_top_n})
    await db.execute(_ARTISTS, {"server_id": server_id, "top_n": settings.artist_top_n})


async def preview_artist_ranking(
    db: AsyncSession,
    *,
    user_id: int,
    server_id: int,
    sections: list[str],
    artist_top_n: int,
    limit: int = 20,
) -> list[PreviewRow]:
    rows = await db.execute(
        _PREVIEW,
        {
            "user_id": user_id,
            "server_id": server_id,
            "sections": sections,
            "top_n": artist_top_n,
            "limit": limit,
        },
    )
    return [PreviewRow(int(k), str(t), int(p), int(r)) for k, t, p, r in rows.all()]
