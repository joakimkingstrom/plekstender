"""Cover songs, from SecondHandSongs (https://secondhandsongs.com): whether a song in the
library is a cover, and of whose song ("Cover of Hurt by Nine Inch Nails").

An admin turns it on (Settings → Services → Cover songs); an API key is optional and only
raises the rate limits. The worker looks songs up gently, every ten minutes, within a budget
of requests per minute, hour and day: songs people play and rate first, then the rest by
popularity. A song is looked up once (by its normalised artist and title, so other releases
of it cost nothing); one not on SecondHandSongs is tried again after half a year, one that
failed the next day.

A lookup is a search by title and performer (one request); for a match that isn't the
original recording, its page names the original (a second request). A re-recording of the
artist's own song counts as an original, not a cover.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.instance import get_secondhandsongs
from plekstender.db.models import SongCover
from plekstender.sync.parse import normalise_artist, normalise_title, without_suffixes

log = logging.getLogger(__name__)

API = "https://api.secondhandsongs.com"
SITE = "https://secondhandsongs.com"
UNKNOWN_AGAIN = dt.timedelta(days=180)
ERROR_AGAIN = dt.timedelta(days=1)
PAGE_SIZE = 20


@dataclass(frozen=True)
class Limits:
    """Requests allowed (a little under SecondHandSongs' own limits)."""

    per_minute: int
    per_hour: int
    per_day: int


WITHOUT_KEY = Limits(per_minute=9, per_hour=90, per_day=900)
WITH_KEY = Limits(per_minute=60, per_hour=900, per_day=9000)
RUNS_PER_HOUR = 6  # the worker's schedule: every ten minutes


class Busy(Exception):
    """SecondHandSongs asked us to slow down (or is down): stop this run."""


@dataclass
class Found:
    status: str  # cover | original | unknown | error
    requests: int
    original_artist: str | None = None
    original_title: str | None = None
    performance_url: str | None = None
    original_url: str | None = None
    error: str | None = None


def site_url(uri: str | None) -> str | None:
    """An API address as the page people can open."""
    return uri.replace(API, SITE, 1) if uri else None


class Client:
    """SecondHandSongs' API, a request at a time at `per_minute`."""

    def __init__(self, http: httpx.AsyncClient, api_key: str | None, limits: Limits) -> None:
        self.http = http
        self.headers = {"Accept": "application/json"}
        if api_key:
            self.headers["X-API-Key"] = api_key
        self.interval = 60.0 / limits.per_minute
        self._last = 0.0
        self.requests = 0

    async def get(self, path_or_url: str, **params: Any) -> dict[str, Any] | None:
        """The JSON at an API path (or full API URL); None when it isn't there (404)."""
        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        url = path_or_url if path_or_url.startswith("http") else f"{API}{path_or_url}"
        if not url.startswith(f"{API}/"):
            raise ValueError(f"not a SecondHandSongs API address: {url}")
        self.requests += 1
        try:
            resp = await self.http.get(url, params=params or None, headers=self.headers, timeout=30)
        except httpx.HTTPError as exc:
            raise Busy(f"SecondHandSongs couldn't be reached: {exc}") from exc
        finally:
            self._last = time.monotonic()
        if resp.status_code == 404:
            return None
        if resp.status_code in (403, 429) or resp.status_code >= 500:
            raise Busy(f"SecondHandSongs answered {resp.status_code}")
        resp.raise_for_status()
        if "json" not in resp.headers.get("content-type", ""):
            raise Busy("SecondHandSongs didn't answer with JSON")
        body = resp.json()
        return body if isinstance(body, dict) else None


def _name(entry: dict[str, Any] | None) -> str:
    performer = (entry or {}).get("performer") or {}
    return str(performer.get("name") or "") if isinstance(performer, dict) else ""


async def look_up(client: Client, artist: str, title: str) -> Found:
    """Whether the artist's song is a cover, and of whose. Raises Busy to stop the run."""
    before = client.requests
    artist_key, title_key = normalise_artist(artist), normalise_title(title)
    try:
        found = await client.get(
            "/search/performance",
            title=without_suffixes(title),
            performer=artist,
            pageSize=PAGE_SIZE,
        )
        results = [r for r in (found or {}).get("resultPage") or [] if isinstance(r, dict)]
        same = [
            r
            for r in results
            if normalise_title(str(r.get("title") or "")) == title_key
            and normalise_artist(_name(r)) == artist_key
        ]
        if not same:
            return Found("unknown", client.requests - before)
        mine = next((r for r in same if r.get("isOriginal") is True), None)
        if mine is not None:
            return Found(
                "original",
                client.requests - before,
                performance_url=site_url(mine.get("uri")),
            )
        match = same[0]
        detail = await client.get(str(match.get("uri") or ""))
        originals: list[dict[str, Any]] = [
            o["original"]
            for o in (detail or {}).get("originals") or []
            if isinstance(o, dict) and isinstance(o.get("original"), dict)
        ]
        if not originals:
            return Found("unknown", client.requests - before)
        original = originals[0]
        url = site_url(match.get("uri"))
        if normalise_artist(_name(original)) == artist_key:  # their own song, recorded again
            return Found("original", client.requests - before, performance_url=url)
        return Found(
            "cover",
            client.requests - before,
            original_artist=_name(original) or None,
            original_title=str(original.get("title") or "") or None,
            performance_url=url,
            original_url=site_url(original.get("uri")),
        )
    except (httpx.HTTPStatusError, ValueError) as exc:
        return Found("error", client.requests - before, error=str(exc)[:500])


# --- the worker's runs -----------------------------------------------------------------------

# Songs not looked up yet (or due again), people's played and rated ones first, then the most
# popular. One row per song: its artist and title as the library has them.
_DUE = text(
    """
    SELECT t.artist_key, t.title_norm,
           min(coalesce(t.original_title, ar.title)) AS artist, min(t.title) AS title
    FROM tracks t
    LEFT JOIN artists ar ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
    LEFT JOIN user_item_state s ON s.server_id = t.server_id AND s.rating_key = t.rating_key
    LEFT JOIN song_covers c ON c.artist_key = t.artist_key AND c.title_norm = t.title_norm
    WHERE t.artist_key IS NOT NULL AND t.artist_key <> ''
      AND coalesce(t.original_title, ar.title) IS NOT NULL
      AND lower(btrim(coalesce(t.original_title, ar.title)))
          NOT IN ('various artists', 'various', 'va', 'unknown artist')
      AND (c.artist_key IS NULL
           OR (c.status = 'error' AND c.checked_at < :error_before)
           OR (c.status = 'unknown' AND c.checked_at < :unknown_before))
    GROUP BY t.artist_key, t.title_norm
    ORDER BY (max(coalesce(s.user_rating, 0)) > 0
              OR max(greatest(s.view_count, s.listens)) > 0) DESC NULLS LAST,
             sum(greatest(s.view_count, s.listens)) DESC NULLS LAST,
             max(s.user_rating) DESC NULLS LAST,
             max(t.popularity) DESC NULLS LAST,
             t.artist_key, t.title_norm
    LIMIT :limit
    """
)


async def requests_since(db: AsyncSession, since: dt.datetime) -> int:
    used = await db.scalar(
        select(func.coalesce(func.sum(SongCover.requests), 0)).where(SongCover.checked_at >= since)
    )
    return int(used or 0)


async def budget(db: AsyncSession, limits: Limits, now: dt.datetime) -> int:
    """Requests this run may make: a share of the hour's, within what's left of the hour's
    and the day's."""
    hour = limits.per_hour - await requests_since(db, now - dt.timedelta(hours=1))
    day = limits.per_day - await requests_since(db, now - dt.timedelta(days=1))
    return max(0, min(hour, day, -(-limits.per_hour // RUNS_PER_HOUR)))


async def _save(
    db: AsyncSession, artist_key: str, title_norm: str, found: Found, now: dt.datetime
) -> None:
    values = {
        "status": found.status,
        "original_artist": found.original_artist,
        "original_title": found.original_title,
        "performance_url": found.performance_url,
        "original_url": found.original_url,
        "source": "secondhandsongs",
        "requests": max(1, found.requests),
        "error": found.error,
        "checked_at": now,
    }
    await db.execute(
        insert(SongCover)
        .values(artist_key=artist_key, title_norm=title_norm, **values)
        .on_conflict_do_update(index_elements=["artist_key", "title_norm"], set_=values)
    )
    await db.commit()


async def run(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    now: dt.datetime | None = None,
) -> int:
    """Look up the songs due, within this run's budget. Returns how many were looked up."""
    chosen = await get_secondhandsongs(db, settings)
    if not chosen.enabled:
        return 0
    limits = WITH_KEY if chosen.api_key else WITHOUT_KEY
    now = now or dt.datetime.now(dt.UTC)
    allowed = await budget(db, limits, now)
    if allowed <= 0:
        return 0
    # A cover takes two requests, anything else one.
    rows = (
        await db.execute(
            _DUE,
            {
                "limit": allowed,
                "error_before": now - ERROR_AGAIN,
                "unknown_before": now - UNKNOWN_AGAIN,
            },
        )
    ).all()
    client = Client(http, chosen.api_key, limits)
    done = 0
    for row in rows:
        if client.requests + 2 > allowed:
            break
        try:
            found = await look_up(client, row.artist, row.title)
        except Busy as exc:
            log.info("cover songs paused: %s", exc)
            break
        await _save(db, row.artist_key, row.title_norm, found, dt.datetime.now(dt.UTC))
        done += 1
    return done


async def progress(db: AsyncSession) -> dict[str, int]:
    """Songs in the libraries, looked up, and covers found (for the settings card)."""
    row = (
        await db.execute(
            text(
                """
                SELECT count(*) AS songs,
                       count(c.status) AS checked,
                       count(*) FILTER (WHERE c.status = 'cover') AS covers
                FROM (SELECT DISTINCT artist_key, title_norm FROM tracks
                      WHERE artist_key IS NOT NULL AND artist_key <> '') t
                LEFT JOIN song_covers c USING (artist_key, title_norm)
                """
            )
        )
    ).one()
    return {"songs": row.songs, "checked": row.checked, "covers": row.covers}
