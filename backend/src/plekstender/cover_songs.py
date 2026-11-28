"""Cover songs: whether a song in the library is a cover, and of whose song ("Cover of Nine
Inch Nails · Hurt"), from SecondHandSongs (https://secondhandsongs.com) and MusicBrainz.

An admin turns it on (Settings → Services → Cover songs) and picks the sources; a
SecondHandSongs API key is optional and only raises its rate limits. The worker looks songs
up gently, every ten minutes: songs people play and rate first, then the rest by popularity.
A song is looked up once (by its normalised artist and title, so other releases of it cost
nothing); one neither source knows is tried again after half a year, one that failed the
next day, and one only SecondHandSongs was asked about gets MusicBrainz's answer too once
MusicBrainz is turned on.

SecondHandSongs, within a budget of requests per minute, hour and day, an artist at a time:
a search for the artist (one request) and the list of everything SecondHandSongs has by them,
each marked original or not (a second request), settle all of the artist's songs at once.
Each cover found then costs one more request: its page names the original. So a library
takes about two requests per artist plus one per cover, not one or two per song.

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
from dataclasses import dataclass, replace
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import musicbrainz
from plekstender.config import Settings
from plekstender.db.instance import CoverSongsSettings, get_cover_songs
from plekstender.db.models import SongCover
from plekstender.sync.parse import normalise_artist, normalise_title

log = logging.getLogger(__name__)

API = "https://api.secondhandsongs.com"
SITE = "https://secondhandsongs.com"
UNKNOWN_AGAIN = dt.timedelta(days=180)
ERROR_AGAIN = dt.timedelta(days=1)
ARTIST_PAGE = 10  # artist search results looked at: exact names come first
NAMESAKES = 3  # artists of the same name whose lists are compared
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
        """The JSON object at an API path (or full API URL); None when it isn't there (404)."""
        body = await self.get_json(path_or_url, **params)
        return body if isinstance(body, dict) else None

    async def get_json(self, path_or_url: str, **params: Any) -> Any:
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
        return resp.json()


def _name(entry: dict[str, Any] | None) -> str:
    performer = (entry or {}).get("performer") or {}
    return str(performer.get("name") or "") if isinstance(performer, dict) else ""


def _by_title(listing: Any) -> dict[str, list[dict[str, Any]]]:
    """An artist's performances by normalised title."""
    out: dict[str, list[dict[str, Any]]] = {}
    for entry in listing if isinstance(listing, list) else []:
        if isinstance(entry, dict) and entry.get("uri"):
            out.setdefault(normalise_title(str(entry.get("title") or "")), []).append(entry)
    return out


async def look_up_artist(client: Client, artist: str, titles: list[str]) -> dict[str, Found]:
    """Whether each of the artist's songs (normalised titles) is an original or a cover, from
    everything SecondHandSongs lists for them. A cover's original comes later (`look_up_original`):
    until then it has no original_url. Raises Busy to stop the run."""
    before = client.requests

    def every(found: Found) -> dict[str, Found]:
        found.requests = 0
        out = {title: replace(found) for title in titles}
        if titles:
            out[titles[0]].requests = client.requests - before
        return out

    try:
        found = await client.get("/search/artist", commonName=artist, pageSize=ARTIST_PAGE)
        key = normalise_artist(artist)
        namesakes = [
            str(r["uri"])
            for r in (found or {}).get("resultPage") or []
            if isinstance(r, dict)
            and r.get("uri")
            and normalise_artist(str(r.get("commonName") or "")) == key
        ][:NAMESAKES]
        # Of artists sharing the name, the one with most of these songs.
        best: dict[str, list[dict[str, Any]]] = {}
        for uri in namesakes:
            listing = _by_title(await client.get_json(f"{uri}/performances"))
            if len(listing.keys() & set(titles)) > len(best.keys() & set(titles)):
                best = listing
    except (httpx.HTTPStatusError, ValueError) as exc:
        return every(Found("error", 0, error=str(exc)[:500]))
    out = every(Found("unknown", 0))
    for title in titles:
        performances = best.get(title)
        if not performances:
            continue
        mine = next((p for p in performances if p.get("isOriginal") is True), None)
        match = mine or performances[0]
        out[title].status = "original" if mine else "cover"
        out[title].performance_url = site_url(str(match["uri"]))
    return out


async def look_up_original(client: Client, performance_url: str, artist: str) -> Found:
    """Whose song a cover is, from the cover's page on SecondHandSongs. Raises Busy."""
    before = client.requests
    try:
        detail = await client.get(performance_url.replace(SITE, API, 1))
    except (httpx.HTTPStatusError, ValueError) as exc:
        return Found("error", client.requests - before, error=str(exc)[:500])
    originals: list[dict[str, Any]] = [
        o["original"]
        for o in (detail or {}).get("originals") or []
        if isinstance(o, dict) and isinstance(o.get("original"), dict)
    ]
    if not originals:
        return Found("unknown", client.requests - before)
    original = originals[0]
    if normalise_artist(_name(original)) == normalise_artist(artist):
        # Their own song, recorded again.
        return Found("original", client.requests - before, performance_url=performance_url)
    return Found(
        "cover",
        client.requests - before,
        original_artist=_name(original) or None,
        original_title=str(original.get("title") or "") or None,
        performance_url=performance_url,
        # Without the original's page, the cover's: either way no longer waiting for it.
        original_url=site_url(original.get("uri")) or performance_url,
    )


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

# Every song in the libraries, once (by normalised artist and title): its artist and title as
# the library has them, a recording MBID (the files' tag first, then Plex's), how much people
# play and rate it, and what's known about it. "fresh": never looked up, or due again (an
# error a day ago, nothing found half a year ago), so asked of every source again.
_SONGS = """
    WITH songs AS (
        SELECT t.artist_key, t.title_norm,
               min(coalesce(t.original_title, ar.title)) AS artist, min(t.title) AS title,
               coalesce(
                   (array_agg(m.tags ->> 'musicbrainz_trackid') FILTER (
                        WHERE m.tags ->> 'musicbrainz_trackid' ~ :uuid))[1],
                   (array_agg(t.mbid) FILTER (WHERE t.mbid ~ :uuid))[1]
               ) AS mbid,
               bool_or(coalesce(s.user_rating, 0) > 0
                       OR greatest(s.view_count, s.listens) > 0) AS liked,
               sum(greatest(s.view_count, s.listens)) AS plays,
               max(coalesce(s.user_rating, 0)) AS rating,
               max(t.popularity) AS popularity
        FROM tracks t
        LEFT JOIN artists ar
          ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
        LEFT JOIN user_item_state s ON s.server_id = t.server_id AND s.rating_key = t.rating_key
        LEFT JOIN media_files m ON m.server_id = t.server_id AND m.rating_key = t.rating_key
        WHERE t.artist_key IS NOT NULL AND t.artist_key <> ''
          AND coalesce(t.original_title, ar.title) IS NOT NULL
          AND lower(btrim(coalesce(t.original_title, ar.title)))
              NOT IN ('various artists', 'various', 'va', 'unknown artist')
        GROUP BY t.artist_key, t.title_norm
    ), known AS (
        SELECT songs.*, c.status, coalesce(c.tried, '') AS tried, c.source,
               c.performance_url, c.original_url,
               (c.artist_key IS NULL
                OR (c.status = 'error' AND c.checked_at < :error_before)
                OR (c.status = 'unknown' AND c.checked_at < :unknown_before)) AS fresh
        FROM songs LEFT JOIN song_covers c USING (artist_key, title_norm)
    )
"""
_FIRST = (
    "liked DESC NULLS LAST, plays DESC NULLS LAST, rating DESC NULLS LAST,"
    " popularity DESC NULLS LAST"
)

# Due on SecondHandSongs: fresh, or nothing found elsewhere before it was turned on.
_SHS_DUE = "(fresh OR (status = 'unknown' AND tried NOT LIKE '%secondhandsongs%'))"

# The artists with songs due on SecondHandSongs, the ones with songs people play first.
_ARTISTS = text(
    _SONGS  # noqa: S608 - constant SQL fragments
    + f"""
    SELECT artist_key, min(artist) AS artist,
           array_agg(title_norm ORDER BY {_FIRST}) AS titles,
           array_agg(tried ORDER BY {_FIRST}) AS tried,
           array_agg(fresh ORDER BY {_FIRST}) AS fresh
    FROM known WHERE {_SHS_DUE}
    GROUP BY artist_key
    ORDER BY bool_or(liked) DESC NULLS LAST, max(plays) DESC NULLS LAST,
             max(rating) DESC NULLS LAST, max(popularity) DESC NULLS LAST, artist_key
    LIMIT :limit
    """  # noqa: S608 - constant SQL fragments
)

# Covers found on SecondHandSongs whose original isn't known yet.
_PENDING = text(
    _SONGS  # noqa: S608 - constant SQL fragments
    + f"""
    SELECT artist_key, title_norm, artist, performance_url, tried FROM known
    WHERE status = 'cover' AND source = 'secondhandsongs' AND original_url IS NULL
      AND performance_url IS NOT NULL
    ORDER BY {_FIRST}, artist_key, title_norm
    LIMIT :limit
    """  # noqa: S608 - constant SQL fragments
)

# Songs due on MusicBrainz (those with a recording MBID): fresh ones when SecondHandSongs is
# off, and ones SecondHandSongs (or a source turned off since) didn't know.
_MB_DUE = text(
    _SONGS  # noqa: S608 - constant SQL fragments
    + f"""
    SELECT artist_key, title_norm, artist, mbid, tried, fresh FROM known
    WHERE mbid IS NOT NULL
      AND ((fresh AND NOT :secondhandsongs)
           OR (NOT fresh AND status = 'unknown' AND tried NOT LIKE '%musicbrainz%'
               AND (tried LIKE '%secondhandsongs%' OR NOT :secondhandsongs)))
    ORDER BY {_FIRST}, artist_key, title_norm
    LIMIT :limit
    """  # noqa: S608 - constant SQL fragments
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


def _tried(before: str, fresh: bool, source: str) -> list[str]:
    """The sources asked about a song: those before (unless it's asked about afresh) and this."""
    asked = [] if fresh else [t for t in before.split(",") if t]
    return [*dict.fromkeys([*asked, source])]


async def _save(
    db: AsyncSession,
    artist_key: str,
    title_norm: str,
    found: Found,
    tried: list[str],
    *,
    more_requests: bool = False,
) -> None:
    """Upsert what's found. `more_requests`: add found.requests to the row's (a cover's
    original, asked for after its artist), rather than replace them."""
    values: dict[str, Any] = {
        "status": found.status,
        "original_artist": found.original_artist,
        "original_title": found.original_title,
        "performance_url": found.performance_url,
        "original_url": found.original_url,
        "source": found.source,
        "tried": ",".join(tried),
        "requests": found.requests,
        "error": found.error,
        "checked_at": dt.datetime.now(dt.UTC),
    }
    stmt = insert(SongCover).values(artist_key=artist_key, title_norm=title_norm, **values)
    if more_requests:
        values["requests"] = SongCover.requests + found.requests
    await db.execute(
        stmt.on_conflict_do_update(index_elements=["artist_key", "title_norm"], set_=values)
    )
    await db.commit()


def _limits(chosen: CoverSongsSettings) -> Limits:
    return WITH_KEY if chosen.api_key else WITHOUT_KEY


async def _secondhandsongs(
    db: AsyncSession, client: Client, allowed: int, params: dict[str, Any]
) -> set[tuple[str, str]]:
    """Covers' originals left from earlier runs, then artists (and their covers' originals),
    until the run's budget is spent. Raises Busy."""
    done: set[tuple[str, str]] = set()

    async def original(artist_key: str, title: str, artist: str, url: str, tried: str) -> None:
        found = await look_up_original(client, url, artist)
        await _save(db, artist_key, title, found, tried.split(","), more_requests=True)
        done.add((artist_key, title))

    pending = (await db.execute(_PENDING, params | {"limit": allowed})).all()
    for row in pending:
        if client.requests + 1 > allowed:
            return done
        await original(row.artist_key, row.title_norm, row.artist, row.performance_url, row.tried)
    artists = (await db.execute(_ARTISTS, params | {"limit": allowed // 2 + 1})).all()
    for artist in artists:
        if client.requests + 2 > allowed:  # an artist takes two
            break
        # (Artists sharing a name take a request each more, a little past the budget maybe.)
        found_all = await look_up_artist(client, artist.artist, list(artist.titles))
        tried_now: dict[str, str] = {}
        for title, tried, fresh in zip(artist.titles, artist.tried, artist.fresh, strict=True):
            tried_now[title] = ",".join(_tried(tried, fresh, "secondhandsongs"))
            await _save(db, artist.artist_key, title, found_all[title], tried_now[title].split(","))
            done.add((artist.artist_key, title))
        for title in artist.titles:
            found = found_all[title]
            if found.status == "cover" and found.performance_url:
                if client.requests + 1 > allowed:
                    return done  # the next run's
                await original(artist.artist_key, title, artist.artist,
                               found.performance_url, tried_now[title])  # fmt: skip
    return done


async def run(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    now: dt.datetime | None = None,
) -> int:
    """Look up the songs due, within this run's budgets. Returns how many were looked up."""
    chosen = await get_cover_songs(db, settings)
    if not chosen.enabled or not (chosen.secondhandsongs or chosen.musicbrainz):
        return 0
    now = now or dt.datetime.now(dt.UTC)
    params: dict[str, Any] = {
        "error_before": now - ERROR_AGAIN,
        "unknown_before": now - UNKNOWN_AGAIN,
        "uuid": _MBID.pattern,
        "secondhandsongs": chosen.secondhandsongs,
    }
    done: set[tuple[str, str]] = set()
    if chosen.secondhandsongs:
        limits = _limits(chosen)
        allowed = await budget(db, limits, now)
        client = Client(http, chosen.api_key, limits)
        try:
            done |= await _secondhandsongs(db, client, allowed, params)
        except Busy as exc:
            log.info("cover songs on SecondHandSongs paused: %s", exc)
    if chosen.musicbrainz:
        rows = (await db.execute(_MB_DUE, params | {"limit": MB_PER_RUN})).all()
        for row in rows:
            try:
                found = await look_up_musicbrainz(http, row.mbid, row.artist)
            except musicbrainz.MusicBrainzError as exc:
                log.info("cover songs on MusicBrainz paused: %s", exc)
                break
            await _save(db, row.artist_key, row.title_norm, found,
                        _tried(row.tried, row.fresh, "musicbrainz"))  # fmt: skip
            done.add((row.artist_key, row.title_norm))
    return len(done)


# Songs, artists and covers left to look up on SecondHandSongs, for the estimate.
_LEFT = text(
    _SONGS  # noqa: S608 - constant SQL fragments
    + f"""
    SELECT count(*) AS songs,
           count(status) AS checked,
           count(*) FILTER (WHERE status = 'cover') AS covers,
           count(*) FILTER (WHERE {_SHS_DUE}) AS songs_due,
           count(DISTINCT artist_key) FILTER (WHERE {_SHS_DUE}) AS artists_due,
           count(*) FILTER (WHERE status = 'cover' AND source = 'secondhandsongs'
                            AND original_url IS NULL) AS pending
    FROM known
    """  # noqa: S608 - constant SQL fragments
)
COVER_SHARE = 0.05  # of songs, before enough are looked up to tell


async def progress(db: AsyncSession, now: dt.datetime | None = None) -> dict[str, int]:
    """Songs in the libraries, looked up, and covers found; and about how many SecondHandSongs
    requests the rest takes (two an artist, one a cover) (for the settings card)."""
    now = now or dt.datetime.now(dt.UTC)
    row = (
        await db.execute(
            _LEFT,
            {
                "error_before": now - ERROR_AGAIN,
                "unknown_before": now - UNKNOWN_AGAIN,
                "uuid": _MBID.pattern,
            },
        )
    ).one()
    share = row.covers / row.checked if row.checked >= 1000 else COVER_SHARE
    requests_left = 2 * row.artists_due + row.pending + round(row.songs_due * share)
    return {
        "songs": row.songs,
        "checked": row.checked,
        "covers": row.covers,
        "requests_left": requests_left,
    }
