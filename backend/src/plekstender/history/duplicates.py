"""Spotting the same play recorded by two sources (e.g. Plexamp, which also scrobbles)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# A Last.fm (or ListenBrainz) listen and a Plex listen of the same song are one play when
# they start within the song's duration + 5 minutes of each other (15 minutes when the
# duration is unknown). Pairs are matched one-to-one, nearest first: a song played twice
# in a row keeps both plays.
_MARK = text(
    """
    WITH candidates AS (
        SELECT o.id AS other_id, p.id AS plex_id,
               abs(extract(epoch FROM o.listened_at - p.listened_at)) AS gap
        FROM listens o
        JOIN listens p
          ON p.user_id = o.user_id
         AND p.source = 'plex'
         AND p.artist_key = o.artist_key
         AND p.track_key = o.track_key
         AND abs(extract(epoch FROM o.listened_at - p.listened_at))
             <= coalesce(p.duration_ms / 1000, 600) + 300
        WHERE o.user_id = :user_id
          AND o.source <> 'plex'
          AND o.duplicate_of IS NULL
          AND NOT EXISTS (SELECT 1 FROM listens d WHERE d.duplicate_of = p.id)
          AND (CAST(:since AS timestamptz) IS NULL OR p.listened_at >= :since)
    ),
    nearest_plex AS (
        SELECT DISTINCT ON (other_id) other_id, plex_id, gap
        FROM candidates ORDER BY other_id, gap, plex_id
    ),
    pairs AS (
        SELECT DISTINCT ON (plex_id) other_id, plex_id
        FROM nearest_plex ORDER BY plex_id, gap, other_id
    )
    UPDATE listens SET duplicate_of = pairs.plex_id
    FROM pairs WHERE listens.id = pairs.other_id
    """
)


async def mark_duplicates(db: AsyncSession, user_id: int, since: dt.datetime | None = None) -> int:
    """Mark a user's imported listens that duplicate a Plex listen. Returns how many.

    `since` limits the check to Plex listens from that time on (e.g. just captured ones).
    """
    marked = 0
    while True:  # each pass pairs one listen per Plex play; repeat for the rest
        result = await db.execute(_MARK, {"user_id": user_id, "since": since})
        count = int(getattr(result, "rowcount", 0) or 0)
        marked += count
        if count == 0:
            return marked
