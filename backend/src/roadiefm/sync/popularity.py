"""Derived popularity for albums and artists.

Plex only has popularity (`ratingCount`, Last.fm listeners) on tracks, so:
- album popularity  = sum of its 3 most popular tracks
- artist popularity = sum of its 10 most popular *songs*, where releases of the same song
  (remasters, compilations...) count once, using the most popular release.

Summing the top tracks (rather than all) keeps artists with huge discographies or many
compilations from outranking artists with a few very popular songs.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ALBUM_TOP_N = 3
ARTIST_TOP_N = 10

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

_ARTISTS = text(
    """
    UPDATE artists a SET popularity = s.p
    FROM (
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
    ) s
    WHERE a.server_id = :server_id
      AND a.rating_key = s.artist_rating_key
      AND a.popularity IS DISTINCT FROM s.p
    """
)


async def recompute_popularity(db: AsyncSession, server_id: int) -> None:
    await db.execute(_ALBUMS, {"server_id": server_id, "top_n": ALBUM_TOP_N})
    await db.execute(_ARTISTS, {"server_id": server_id, "top_n": ARTIST_TOP_N})
