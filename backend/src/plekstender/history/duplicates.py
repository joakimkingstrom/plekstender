"""Spotting the same play recorded by two sources.

Plexamp plays can also reach Last.fm, and Last.fm history can be copied into ListenBrainz
(by ListenBrainz's own importer or by scrobbling to both). Each play should count once.
Precedence: a Plex listen is kept over the others, then Last.fm over ListenBrainz.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession


def _pairing(primary: str, others: tuple[str, ...], window: str) -> TextClause:
    """Mark `others` listens that duplicate a `primary` listen of the same song.

    Pairs are matched one-to-one, nearest first, so a song played twice in a row keeps
    both plays. A primary listen may itself be a duplicate (of a Plex play); the other
    listen then points at it and is still skipped by stats.
    """
    other_sources = ", ".join(f"'{s}'" for s in others)
    return text(
        f"""
        WITH candidates AS (
            SELECT o.id AS other_id, p.id AS primary_id,
                   abs(extract(epoch FROM o.listened_at - p.listened_at)) AS gap
            FROM listens o
            JOIN listens p
              ON p.user_id = o.user_id
             AND p.source = '{primary}'
             AND p.artist_key = o.artist_key
             AND p.track_key = o.track_key
             AND abs(extract(epoch FROM o.listened_at - p.listened_at)) <= {window}
            WHERE o.user_id = :user_id
              AND o.source IN ({other_sources})
              AND o.duplicate_of IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM listens d
                  WHERE d.duplicate_of = p.id AND d.source IN ({other_sources})
              )
              AND (CAST(:since AS timestamptz) IS NULL OR p.listened_at >= :since)
        ),
        nearest AS (
            SELECT DISTINCT ON (other_id) other_id, primary_id, gap
            FROM candidates ORDER BY other_id, gap, primary_id
        ),
        pairs AS (
            SELECT DISTINCT ON (primary_id) other_id, primary_id
            FROM nearest ORDER BY primary_id, gap, other_id
        )
        UPDATE listens SET duplicate_of = pairs.primary_id
        FROM pairs WHERE listens.id = pairs.other_id
        """  # noqa: S608 - only constant source names are interpolated
    )


_PASSES = (
    # Plex records the end of a play and we store end minus duration; scrobblers record the
    # start. Allow the song's duration + 5 minutes (15 minutes when it's unknown).
    _pairing("plex", ("lastfm", "listenbrainz"), "coalesce(p.duration_ms / 1000, 600) + 300"),
    # The same scrobble in both services keeps its timestamp: allow 2 minutes.
    _pairing("lastfm", ("listenbrainz",), "120"),
)


async def mark_duplicates(db: AsyncSession, user_id: int, since: dt.datetime | None = None) -> int:
    """Mark a user's listens that duplicate another source's listen. Returns how many.

    `since` limits the check to primary listens from that time on (e.g. just captured).
    """
    marked = 0
    for statement in _PASSES:
        while True:  # each pass pairs one listen per primary listen; repeat for the rest
            result = await db.execute(statement, {"user_id": user_id, "since": since})
            count = int(getattr(result, "rowcount", 0) or 0)
            marked += count
            if count == 0:
                break
    return marked
