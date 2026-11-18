"""Rating queues: which songs, albums or artists to rate next.

Pools: *listened* (played at least once, in Plex or in the listening history), *unrated*,
or *all*. Orders:

- *weighted*: random, weighted towards what's been played more (and, among unplayed
  items, what's more popular), so a queue isn't ten unknown songs in a row.
- *plays* / *popular*: most played or most popular first, but artists are drawn at
  random (weighted by their best remaining item) so one artist doesn't fill the queue.

Songs released several times (remasters, compilations) appear once, as the earliest
release; a rating on any release counts as rated.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Kind = Literal["track", "album", "artist"]
Pool = Literal["listened", "unrated", "all"]
Order = Literal["weighted", "plays", "popular"]

# Weighted random: weight = (plays + 1)^ALPHA x (1 + ln(1 + popularity / 1000)).
ALPHA = 0.7
# Interleaving: an artist's chance = (its best remaining metric + 1)^BETA.
BETA = 0.6
# Candidates fetched per page for the plays / popular orders.
CANDIDATES = 400
# No artist twice within this many items (when there are enough artists).
ARTIST_GAP = 5

_POOLS = {
    "listened": "plays > 0",
    "unrated": "user_rating IS NULL",
    "all": "TRUE",
}
_ORDER_SQL = {
    "weighted": (
        "-ln(random() + 1e-12) / (power(plays + 1, :alpha) "
        "* (1 + ln(1 + coalesce(popularity, 0) / 1000.0)))"
    ),
    "plays": "plays DESC, coalesce(popularity, 0) DESC, rating_key",
    "popular": "coalesce(popularity, 0) DESC, plays DESC, rating_key",
}

# One row per song (earliest release); rating and plays from the song's best release.
_TRACKS = """
    SELECT * FROM (
        SELECT t.rating_key, t.title, t.artist_rating_key AS artist_key,
               coalesce(t.original_title, ar.title) AS subtitle,
               al.title AS album_title, t.album_rating_key, al.thumb, al.year,
               t.duration_ms, t.popularity, NULL::text AS summary,
               NULL::float AS critic_rating,
               max(s.user_rating) OVER song AS user_rating,
               max(greatest(s.view_count, s.listens)) OVER song AS plays,
               row_number() OVER (song ORDER BY al.year NULLS LAST, t.rating_key) AS release
        FROM tracks t
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = t.server_id AND s.rating_key = t.rating_key
        LEFT JOIN albums al ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
        LEFT JOIN artists ar
          ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
        WHERE t.server_id = :server_id AND t.section_key = ANY(:sections)
        WINDOW song AS (PARTITION BY t.artist_rating_key, t.title_norm)
    ) songs
    WHERE release = 1
"""
_ALBUMS = """
    SELECT al.rating_key, al.title, al.artist_rating_key AS artist_key,
           ar.title AS subtitle, NULL::text AS album_title, NULL::bigint AS album_rating_key,
           al.thumb, al.year, NULL::int AS duration_ms, al.popularity, al.summary,
           al.critic_rating, s.user_rating, greatest(s.view_count, s.listens) AS plays
    FROM albums al
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = al.server_id AND s.rating_key = al.rating_key
    LEFT JOIN artists ar ON ar.server_id = al.server_id AND ar.rating_key = al.artist_rating_key
    WHERE al.server_id = :server_id AND al.section_key = ANY(:sections)
"""
_ARTISTS = """
    SELECT ar.rating_key, ar.title, ar.rating_key AS artist_key, NULL::text AS subtitle,
           NULL::text AS album_title, NULL::bigint AS album_rating_key, ar.thumb,
           NULL::int AS year, NULL::int AS duration_ms, ar.popularity, ar.summary,
           NULL::float AS critic_rating, s.user_rating,
           greatest(s.view_count, s.listens) AS plays
    FROM artists ar
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = ar.server_id AND s.rating_key = ar.rating_key
    WHERE ar.server_id = :server_id AND ar.section_key = ANY(:sections)
      AND lower(btrim(ar.title)) NOT IN ('various artists', 'various', 'va')
"""
_BASE: dict[str, str] = {"track": _TRACKS, "album": _ALBUMS, "artist": _ARTISTS}


@dataclass
class QueueItem:
    rating_key: int
    kind: Kind
    title: str
    artist_key: int | None
    subtitle: str | None
    album_title: str | None
    album_rating_key: int | None
    thumb: str | None
    year: int | None
    duration_ms: int | None
    popularity: int | None
    summary: str | None
    critic_rating: float | None
    user_rating: float | None
    plays: int


def interleave(
    items: Sequence[QueueItem],
    metric: Callable[[QueueItem], float],
    limit: int,
    rng: random.Random,
) -> list[QueueItem]:
    """Best-first per artist, artists drawn at random weighted by their best remaining item.

    `items` must already be sorted best first.
    """
    by_artist: dict[Any, deque[QueueItem]] = {}
    for item in items:
        by_artist.setdefault(item.artist_key or item.rating_key, deque()).append(item)
    recent: deque[Any] = deque(maxlen=min(ARTIST_GAP, max(0, len(by_artist) - 1)))
    out: list[QueueItem] = []
    while by_artist and len(out) < limit:
        choices = [a for a in by_artist if a not in recent] or list(by_artist)
        weights = [(metric(by_artist[a][0]) + 1) ** BETA for a in choices]
        artist = rng.choices(choices, weights=weights)[0]
        out.append(by_artist[artist].popleft())
        if not by_artist[artist]:
            del by_artist[artist]
        if recent.maxlen:
            recent.append(artist)
    return out


async def build_queue(
    db: AsyncSession,
    *,
    user_id: int,
    server_id: int,
    sections: list[str],
    kind: Kind,
    pool: Pool,
    order: Order,
    exclude: list[int],
    limit: int,
    rng: random.Random | None = None,
) -> list[QueueItem]:
    """The next `limit` items to rate, leaving out `exclude` (already shown this session)."""
    rng = rng or random.Random()  # noqa: S311 - shuffling, not security
    fetch = limit if order == "weighted" else max(CANDIDATES, limit)
    query = text(
        f"SELECT * FROM ({_BASE[kind]}) items "  # noqa: S608 - constant SQL fragments
        f"WHERE {_POOLS[pool]} AND NOT (rating_key = ANY(:exclude)) "
        f"ORDER BY {_ORDER_SQL[order]} LIMIT :fetch"
    )
    rows = await db.execute(
        query,
        {
            "user_id": user_id,
            "server_id": server_id,
            "sections": sections,
            "exclude": exclude,
            "fetch": fetch,
            "alpha": ALPHA,
        },
    )
    items = [
        QueueItem(
            rating_key=r.rating_key,
            kind=kind,
            title=r.title,
            artist_key=r.artist_key,
            subtitle=r.subtitle,
            album_title=r.album_title,
            album_rating_key=r.album_rating_key,
            thumb=r.thumb,
            year=r.year,
            duration_ms=r.duration_ms,
            popularity=r.popularity,
            summary=r.summary,
            critic_rating=r.critic_rating,
            user_rating=r.user_rating,
            plays=int(r.plays or 0),
        )
        for r in rows.all()
    ]
    if order == "weighted":
        return items
    if order == "plays":
        return interleave(items, lambda i: float(i.plays), limit, rng)
    return interleave(items, lambda i: float(i.popularity or 0), limit, rng)
