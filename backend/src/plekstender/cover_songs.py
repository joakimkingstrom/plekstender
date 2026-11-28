"""Cover songs: whether a song in the library is a cover, and of whose song ("Cover of Nine
Inch Nails · Hurt"), from SecondHandSongs (https://secondhandsongs.com) and MusicBrainz.

An admin turns it on (Settings → Services → Cover songs) and picks the sources; a
SecondHandSongs API key is optional and only raises its rate limits. The worker looks songs
up gently, every ten minutes: songs people play and rate first, then the rest by popularity.
A song is looked up once (by its normalised artist and title, so other releases of it cost
nothing); one neither source knows is tried again after half a year, one that failed the
next day, and one only SecondHandSongs was asked about gets MusicBrainz's answer too once
MusicBrainz is turned on.

SecondHandSongs, within a budget of requests per minute, hour and day: a search by title and
performer (one request); for a match that isn't the original recording, its page names the
original (a second request).

MusicBrainz, for songs it doesn't know, when the files (or Plex) have the recording's
MusicBrainz ID: the recording's link to its song (the "work") says whether it's a cover; for
one that is, the work's plain recordings of the same title (not covers, live, medleys or
instrumentals) are the original artist's, and one of them names them (three requests, at
MusicBrainz's pace).

A re-recording of the artist's own song counts as an original, not a cover.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import musicbrainz
from plekstender.config import Settings
from plekstender.db.instance import CoverSongsSettings, get_cover_songs
from plekstender.db.models import SongCover
from plekstender.sync.parse import normalise_artist, normalise_title, without_suffixes

log = logging.getLogger(__name__)

API = "https://api.secondhandsongs.com"
SITE = "https://secondhandsongs.com"
UNKNOWN_AGAIN = dt.timedelta(days=180)
ERROR_AGAIN = dt.timedelta(days=1)
PAGE_SIZE = 20
MB_SITE = "https://musicbrainz.org"
MB_PER_RUN = 40  # songs asked about on MusicBrainz in one run (up to three requests each)
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


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
    requests: int  # to SecondHandSongs
    original_artist: str | None = None
    original_title: str | None = None
    performance_url: str | None = None
    original_url: str | None = None
    error: str | None = None
    source: str = "secondhandsongs"


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


# --- MusicBrainz -----------------------------------------------------------------------------


def _attributes(relation: dict[str, Any]) -> set[str]:
    return {str(a) for a in relation.get("attributes") or []}


async def look_up_musicbrainz(http: httpx.AsyncClient, mbid: str, artist: str) -> Found:
    """Whether the recording is a cover, by MusicBrainz's link to its song. Raises
    MusicBrainzError (not NotFound) when MusicBrainz is busy or unreachable."""
    page = f"{MB_SITE}/recording/{mbid}"
    try:
        recording = await musicbrainz.lookup(http, "recording", mbid, "work-rels")
    except musicbrainz.NotFound:
        return Found("unknown", 0, source="musicbrainz")
    performances = [
        r
        for r in recording.get("relations") or []
        if r.get("type") == "performance" and isinstance(r.get("work"), dict)
    ]
    if not performances:
        return Found("unknown", 0, source="musicbrainz")
    cover = next((r for r in performances if "cover" in _attributes(r)), None)
    if cover is None:
        return Found("original", 0, performance_url=page, source="musicbrainz")
    work = cover["work"]
    title = str(work.get("title") or "") or None
    found = Found(
        "cover",
        0,
        original_title=title,
        performance_url=page,
        original_url=f"{MB_SITE}/work/{work.get('id')}",
        source="musicbrainz",
    )
    try:
        song = await musicbrainz.lookup(http, "work", str(work.get("id")), "recording-rels")
    except musicbrainz.NotFound:
        return found
    # The original artist's own recordings: plain ones of the song (not a cover, live, a
    # medley or an instrumental), under its own title.
    plain = [
        r["recording"]
        for r in song.get("relations") or []
        if r.get("type") == "performance"
        and isinstance(r.get("recording"), dict)
        and not _attributes(r)
        and r["recording"].get("id") != mbid
        and normalise_title(str(r["recording"].get("title") or "")) == normalise_title(title or "")
    ]
    if not plain:
        return found
    try:
        original = await musicbrainz.lookup(
            http, "recording", str(plain[0]["id"]), "artist-credits"
        )
    except musicbrainz.NotFound:
        return found
    name = musicbrainz.credit(original.get("artist-credit")) or None
    if name and normalise_artist(name) == normalise_artist(artist):
        return Found("original", 0, performance_url=page, source="musicbrainz")
    found.original_artist = name
    found.original_url = f"{MB_SITE}/recording/{plain[0]['id']}"
    return found


# --- the worker's runs -----------------------------------------------------------------------

# Songs not looked up yet (or due again), people's played and rated ones first, then the most
# popular. One row per song: its artist and title as the library has them, a recording MBID
# (the files' tag first, then Plex's), and whether it's due for every source ("stale") or
# only for MusicBrainz (asked SecondHandSongs before MusicBrainz was turned on).
_DUE = text(
    """
    SELECT artist_key, title_norm, artist, title, mbid, stale FROM (
        SELECT t.artist_key, t.title_norm,
               min(coalesce(t.original_title, ar.title)) AS artist, min(t.title) AS title,
               (array_agg(m.tags ->> 'musicbrainz_trackid') FILTER (
                    WHERE m.tags ->> 'musicbrainz_trackid' ~ :uuid))[1]
                   AS file_mbid,
               (array_agg(t.mbid) FILTER (WHERE t.mbid ~ :uuid))[1] AS plex_mbid,
               bool_or(c.artist_key IS NULL OR c.status = 'error'
                       OR c.checked_at < :unknown_before) AS stale,
               max(coalesce(s.user_rating, 0)) AS rating,
               max(greatest(s.view_count, s.listens)) AS played,
               sum(greatest(s.view_count, s.listens)) AS plays,
               max(t.popularity) AS popularity
        FROM tracks t
        LEFT JOIN artists ar
          ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
        LEFT JOIN user_item_state s ON s.server_id = t.server_id AND s.rating_key = t.rating_key
        LEFT JOIN media_files m ON m.server_id = t.server_id AND m.rating_key = t.rating_key
        LEFT JOIN song_covers c ON c.artist_key = t.artist_key AND c.title_norm = t.title_norm
        WHERE t.artist_key IS NOT NULL AND t.artist_key <> ''
          AND coalesce(t.original_title, ar.title) IS NOT NULL
          AND lower(btrim(coalesce(t.original_title, ar.title)))
              NOT IN ('various artists', 'various', 'va', 'unknown artist')
          AND (c.artist_key IS NULL
               OR (c.status = 'error' AND c.checked_at < :error_before)
               OR (c.status = 'unknown' AND c.checked_at < :unknown_before)
               OR (:musicbrainz AND c.status = 'unknown'
                   AND c.tried NOT LIKE '%musicbrainz%'))
        GROUP BY t.artist_key, t.title_norm
    ) songs
    CROSS JOIN LATERAL (SELECT coalesce(file_mbid, plex_mbid) AS mbid) id
    WHERE (stale AND :secondhandsongs) OR (:musicbrainz AND mbid IS NOT NULL)
    ORDER BY (rating > 0 OR played > 0) DESC NULLS LAST, plays DESC NULLS LAST,
             rating DESC NULLS LAST, popularity DESC NULLS LAST, artist_key, title_norm
    LIMIT :limit
    """
)


async def requests_since(db: AsyncSession, since: dt.datetime) -> int:
    used = await db.scalar(
        select(func.coalesce(func.sum(SongCover.requests), 0)).where(SongCover.checked_at >= since)
    )
    return int(used or 0)


async def budget(db: AsyncSession, limits: Limits, now: dt.datetime) -> int:
    """SecondHandSongs requests this run may make: a share of the hour's, within what's left
    of the hour's and the day's."""
    hour = limits.per_hour - await requests_since(db, now - dt.timedelta(hours=1))
    day = limits.per_day - await requests_since(db, now - dt.timedelta(days=1))
    return max(0, min(hour, day, -(-limits.per_hour // RUNS_PER_HOUR)))


async def _save(
    db: AsyncSession,
    artist_key: str,
    title_norm: str,
    found: Found,
    tried: list[str],
    shs_requests: int,
    now: dt.datetime,
) -> None:
    values = {
        "status": found.status,
        "original_artist": found.original_artist,
        "original_title": found.original_title,
        "performance_url": found.performance_url,
        "original_url": found.original_url,
        "source": found.source,
        "tried": ",".join(tried),
        "requests": shs_requests,
        "error": found.error,
        "checked_at": now,
    }
    await db.execute(
        insert(SongCover)
        .values(artist_key=artist_key, title_norm=title_norm, **values)
        .on_conflict_do_update(index_elements=["artist_key", "title_norm"], set_=values)
    )
    await db.commit()


def _limits(chosen: CoverSongsSettings) -> Limits:
    return WITH_KEY if chosen.api_key else WITHOUT_KEY


async def run(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    now: dt.datetime | None = None,
) -> int:
    """Look up the songs due, within this run's budgets. Returns how many were looked up."""
    chosen = await get_cover_songs(db, settings)
    use_shs, use_mb = chosen.secondhandsongs, chosen.musicbrainz
    if not chosen.enabled or not (use_shs or use_mb):
        return 0
    limits = _limits(chosen)
    now = now or dt.datetime.now(dt.UTC)
    allowed = await budget(db, limits, now) if use_shs else 0
    if use_shs and allowed <= 0 and not use_mb:
        return 0
    rows = (
        await db.execute(
            _DUE,
            {
                "limit": max(allowed, MB_PER_RUN if use_mb else 0),
                "error_before": now - ERROR_AGAIN,
                "unknown_before": now - UNKNOWN_AGAIN,
                "uuid": _MBID.pattern,
                "secondhandsongs": use_shs,
                "musicbrainz": use_mb,
            },
        )
    ).all()
    client = Client(http, chosen.api_key, limits)
    done = mb_asked = 0
    for row in rows:
        before = client.requests
        found: Found | None = None
        # Asked SecondHandSongs before, when only MusicBrainz is due.
        tried = [] if row.stale else ["secondhandsongs"]
        if row.stale and chosen.secondhandsongs:
            # SecondHandSongs first; out of budget (or told to slow down): the next run.
            if not use_shs or client.requests + 2 > allowed:
                continue
            try:
                found = await look_up(client, row.artist, row.title)
            except Busy as exc:
                log.info("cover songs on SecondHandSongs paused: %s", exc)
                use_shs = False
                continue
            tried.append("secondhandsongs")
        decided = found is not None and found.status in ("cover", "original")
        if not decided and use_mb and row.mbid and mb_asked < MB_PER_RUN:
            mb_asked += 1
            try:
                mb = await look_up_musicbrainz(http, row.mbid, row.artist)
            except musicbrainz.MusicBrainzError as exc:
                log.info("cover songs on MusicBrainz paused: %s", exc)
                use_mb = False
            else:
                tried.append("musicbrainz")
                if found is None or mb.status != "unknown":
                    found = mb
        if found is None or ("musicbrainz" not in tried and not row.stale):
            continue  # nothing asked (a source paused): the next run
        await _save(
            db,
            row.artist_key,
            row.title_norm,
            found,
            tried,
            client.requests - before,
            dt.datetime.now(dt.UTC),
        )
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
