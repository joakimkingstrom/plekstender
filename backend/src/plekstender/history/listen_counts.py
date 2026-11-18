"""Per-item listen counts: a user's history (Plex plays + imports) matched to the library.

Stored on `user_item_state.listens`, so rating queues, sorting and playlists can use the
whole history, not only the plays Plex has recorded. Matching is by normalised names:
tracks by title and track or album artist; albums by album title too; artists by name.
Remasters and other releases of a song share its listens.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_GROUPED = """
    la AS (
        SELECT artist_key, album_key, track_key, count(*) AS n
        FROM listens
        WHERE user_id = :user_id AND duplicate_of IS NULL
        GROUP BY artist_key, album_key, track_key
    )
"""

_RESET = text("UPDATE user_item_state SET listens = 0 WHERE user_id = :user_id AND listens <> 0")

_TRACKS = text(
    f"""
    WITH {_GROUPED},
    lt AS (SELECT artist_key, track_key, sum(n) AS n FROM la GROUP BY artist_key, track_key),
    counts AS (
        SELECT t.server_id, t.rating_key, sum(lt.n) AS n
        FROM user_item_state s
        JOIN tracks t ON t.server_id = s.server_id AND t.rating_key = s.rating_key
        LEFT JOIN artists ar
          ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
        JOIN lt ON lt.track_key = t.title_norm AND lt.artist_key IN (t.artist_key, ar.name_key)
        WHERE s.user_id = :user_id AND s.item_type = 'track'
        GROUP BY t.server_id, t.rating_key
    )
    UPDATE user_item_state s SET listens = counts.n
    FROM counts
    WHERE s.user_id = :user_id AND s.server_id = counts.server_id
      AND s.rating_key = counts.rating_key
    """  # noqa: S608 - constant SQL fragment
)

_ALBUMS = text(
    f"""
    WITH {_GROUPED},
    counts AS (
        SELECT al.server_id, al.rating_key, sum(la.n) AS n
        FROM user_item_state s
        JOIN albums al ON al.server_id = s.server_id AND al.rating_key = s.rating_key
        JOIN tracks t ON t.server_id = al.server_id AND t.album_rating_key = al.rating_key
        LEFT JOIN artists ar
          ON ar.server_id = al.server_id AND ar.rating_key = al.artist_rating_key
        JOIN la
          ON la.album_key = al.title_norm
         AND la.track_key = t.title_norm
         AND la.artist_key IN (t.artist_key, ar.name_key)
        WHERE s.user_id = :user_id AND s.item_type = 'album'
        GROUP BY al.server_id, al.rating_key
    )
    UPDATE user_item_state s SET listens = counts.n
    FROM counts
    WHERE s.user_id = :user_id AND s.server_id = counts.server_id
      AND s.rating_key = counts.rating_key
    """  # noqa: S608 - constant SQL fragment
)

_ARTISTS = text(
    """
    WITH counts AS (
        SELECT ar.server_id, ar.rating_key, l.n
        FROM user_item_state s
        JOIN artists ar ON ar.server_id = s.server_id AND ar.rating_key = s.rating_key
        JOIN (
            SELECT artist_key, count(*) AS n FROM listens
            WHERE user_id = :user_id AND duplicate_of IS NULL
            GROUP BY artist_key
        ) l ON l.artist_key = ar.name_key
        WHERE s.user_id = :user_id AND s.item_type = 'artist'
    )
    UPDATE user_item_state s SET listens = counts.n
    FROM counts
    WHERE s.user_id = :user_id AND s.server_id = counts.server_id
      AND s.rating_key = counts.rating_key
    """
)


async def refresh_listen_counts(db: AsyncSession, user_id: int) -> None:
    """Recompute a user's listen counts on every server they've synced. Caller commits."""
    params = {"user_id": user_id}
    await db.execute(_RESET, params)
    for statement in (_TRACKS, _ALBUMS, _ARTISTS):
        await db.execute(statement, params)
