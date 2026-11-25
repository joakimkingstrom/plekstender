"""Playlists for gigs, for people who turn them on (their profile):

- **Before** a gig they've starred, in the 30 days before it: the songs the artists have
  been playing lately: their last ten setlists on setlist.fm, the songs played at a quarter
  of those shows or more, in the order they usually come. Support acts first, as on the
  night. Refreshed weekly until the gig.
- **After** a gig they went to, in the two weeks after it: what was played, from the gig's
  setlists (looked up on setlist.fm, by artist and date, when the gig has none yet; they're
  kept on the gig, which also counts the songs as seen live).

Songs are matched to the library by title (a cover, if the artist's own version isn't
there, as the original artist's), and saved as a smart playlist of those songs in that
order, which the worker builds in Plex. A playlist that's deleted isn't made again.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.instance import get_setlistfm_api_key
from plekstender.db.models import Artist, Gig, GigPlaylist, LiveShow, Playlist, User, UserServer
from plekstender.gigs.imports import ImportError_, setlistfm_search
from plekstender.live.merge import merge
from plekstender.playlists.definition import Definition
from plekstender.sync.parse import normalise_artist, normalise_title

log = logging.getLogger(__name__)

BEFORE_DAYS = 30  # starred gigs this close get a playlist
REFRESH_EVERY = dt.timedelta(days=7)
AFTER_DAYS = 14  # gigs this recent get one, once their setlist is on setlist.fm
RECENT_SETLISTS = 10
MIN_SHARE = 0.25  # of those shows, to count as "been playing"
MIN_SONGS = 8  # fewer than this: the most played ones instead
FALLBACK_SONGS = 15
MAX_ARTISTS = 4  # per gig (a festival's bill can be long)
SETLISTFM_INTERVAL = 0.6  # its limit is about two requests a second

_last_request = 0.0


@dataclass
class SetSong:
    name: str
    cover: str | None = None  # the original artist, when it's a cover


@dataclass
class Part:
    """One artist's songs for the playlist, in order."""

    artist: str
    songs: list[SetSong]
    rating_key: int | None = None  # the library artist, if known


@dataclass
class Made:
    """What a run did, and the playlists the worker should build."""

    made: int = 0
    refreshed: int = 0
    to_build: list[int] = field(default_factory=list)


def wants(user: User) -> tuple[bool, bool]:
    """(before, after): which gig playlists the user has turned on."""
    chosen = (user.preferences or {}).get("gig_playlists") or {}
    return chosen.get("before") is True, chosen.get("after") is True


def nice_date(day: dt.date) -> str:
    return f"{day.day} {day:%b %Y}"


# --- setlist.fm -----------------------------------------------------------------------------


async def _search(http: httpx.AsyncClient, settings: Settings, key: str, **query: Any) -> list[Any]:
    """setlist.fm's setlist search, a request at a time at its pace."""
    global _last_request
    wait = _last_request + SETLISTFM_INTERVAL - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    try:
        found, _ = await setlistfm_search(http, settings.setlistfm_api_url, key, **query)
    finally:
        _last_request = time.monotonic()
    return found


def _set_songs(songs: list[dict[str, Any]]) -> list[SetSong]:
    """A setlist's songs, without the ones played from tape (intros, outros)."""
    return [
        SetSong(str(s["name"]), s.get("cover"))
        for s in songs
        if isinstance(s, dict) and s.get("name") and not s.get("tape")
    ]


def usual_setlist(setlists: list[list[SetSong]]) -> list[SetSong]:
    """The songs of recent shows that come up at MIN_SHARE of them or more (else the most
    played), in the order they usually come."""
    if not setlists:
        return []
    count: dict[str, int] = defaultdict(int)
    where: dict[str, list[float]] = defaultdict(list)
    first: dict[str, SetSong] = {}
    for songs in setlists:
        seen: set[str] = set()
        for i, song in enumerate(songs):
            key = normalise_title(song.name)
            if key in seen:
                continue
            seen.add(key)
            count[key] += 1
            where[key].append(i / max(1, len(songs) - 1))
            first.setdefault(key, song)
    keys = [k for k in count if count[k] / len(setlists) >= MIN_SHARE]
    if len(keys) < MIN_SONGS:
        keys = sorted(count, key=lambda k: -count[k])[:FALLBACK_SONGS]
    keys.sort(key=lambda k: sum(where[k]) / len(where[k]))
    return [first[k] for k in keys]


async def recent_setlist(
    http: httpx.AsyncClient,
    settings: Settings,
    key: str,
    artist: str,
    mbid: str | None,
    today: dt.date,
) -> list[SetSong]:
    """What an artist has been playing lately (their last RECENT_SETLISTS shows)."""
    found = await _search(http, settings, key, artist=artist, mbid=mbid)
    setlists = [
        _set_songs(f.draft.setlists[0]["songs"])
        for f in found
        if f.draft.date < today and f.draft.setlists
    ][:RECENT_SETLISTS]
    return usual_setlist([s for s in setlists if s])


async def gig_setlists(
    http: httpx.AsyncClient,
    settings: Settings,
    key: str,
    gig: Gig,
    mbids: dict[str, str],
) -> list[dict[str, Any]]:
    """The gig's setlists on setlist.fm: each artist on the bill's, that day."""
    out = []
    for artist in gig.artists[:MAX_ARTISTS]:
        name = str(artist.get("name") or "")
        if not name:
            continue
        found = await _search(
            http, settings, key, artist=name, mbid=mbids.get(normalise_artist(name)), date=gig.date
        )
        for f in found:
            if f.draft.date == gig.date and f.draft.setlists:
                out.append(f.draft.setlists[0])
                break
    return out


# --- the library ----------------------------------------------------------------------------

# One release per song: the best-rated, else the earliest album (not a later live one).
_MATCH = text(
    """
    SELECT DISTINCT ON (t.title_norm) t.title_norm, t.rating_key
    FROM tracks t
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = t.server_id AND s.rating_key = t.rating_key
    LEFT JOIN artists ar ON ar.server_id = t.server_id AND ar.rating_key = t.artist_rating_key
    LEFT JOIN albums al ON al.server_id = t.server_id AND al.rating_key = t.album_rating_key
    WHERE t.server_id = :server_id AND t.section_key = ANY(:sections)
      AND t.title_norm = ANY(:titles)
      AND (t.artist_key = :artist OR ar.name_key = :artist
           OR t.artist_rating_key = CAST(:artist_rating_key AS bigint))
    ORDER BY t.title_norm, s.user_rating DESC NULLS LAST, al.year NULLS LAST, t.rating_key
    """
)


async def _match(
    db: AsyncSession, us: UserServer, artist: str, rating_key: int | None, titles: list[str]
) -> dict[str, int]:
    if not titles:
        return {}
    rows = await db.execute(
        _MATCH,
        {
            "user_id": us.user_id,
            "server_id": us.server_id,
            "sections": list(us.music_section_keys),
            "titles": titles,
            "artist": normalise_artist(artist),
            "artist_rating_key": rating_key,
        },
    )
    return {title: key for title, key in rows.all()}


async def library_songs(db: AsyncSession, us: UserServer, parts: list[Part]) -> list[int]:
    """The parts' songs in this server's library, in order (each once)."""
    out: list[int] = []
    for part in parts:
        titles = [normalise_title(s.name) for s in part.songs]
        own = await _match(db, us, part.artist, part.rating_key, titles)
        covers: dict[tuple[str, str], int] = {}
        by_original: dict[str, list[str]] = defaultdict(list)
        for song, title in zip(part.songs, titles, strict=True):
            if title not in own and song.cover:
                by_original[song.cover].append(title)
        for original, wanted in by_original.items():
            for title, key in (await _match(db, us, original, None, wanted)).items():
                covers[(original, title)] = key
        for song, title in zip(part.songs, titles, strict=True):
            found = own.get(title) or (covers.get((song.cover, title)) if song.cover else None)
            if found is not None and found not in out:
                out.append(found)
    return out


async def _best_server(
    db: AsyncSession, servers: list[UserServer], parts: list[Part]
) -> tuple[UserServer | None, list[int]]:
    """The user's server whose library has the most of the songs."""
    best: tuple[UserServer | None, list[int]] = (None, [])
    for us in servers:
        songs = await library_songs(db, us, parts)
        if len(songs) > len(best[1]):
            best = (us, songs)
    return best


# --- making them ----------------------------------------------------------------------------


async def _save(
    db: AsyncSession,
    *,
    record: GigPlaylist | None,
    user: User,
    kind: str,
    ref: str,
    gig_date: dt.date,
    us: UserServer | None,
    songs: list[int],
    name: str,
    description: str,
    now: dt.datetime,
    result: Made,
) -> None:
    """Make or update the playlist (when songs matched), and remember it."""
    if record is None:
        record = GigPlaylist(user_id=user.id, kind=kind, ref=ref, made_at=now, songs=0)
        db.add(record)
    record.gig_date, record.songs, record.refreshed_at = gig_date, len(songs), now
    if not songs or us is None:
        await db.flush()
        return
    playlist = await db.get(Playlist, record.playlist_id) if record.playlist_id else None
    if playlist is None:
        playlist = Playlist(user_server_id=us.id, schedule="manual")
        db.add(playlist)
        result.made += 1
    else:
        result.refreshed += 1
    playlist.name, playlist.description = name[:200], description
    playlist.definition = Definition(tracks=songs, order="listed", limit=None).model_dump()
    playlist.display = {"gig": {"kind": kind, "date": gig_date.isoformat()}}
    playlist.status, playlist.error = "queued", None
    await db.flush()
    record.playlist_id = playlist.id
    result.to_build.append(playlist.id)


def _deleted(record: GigPlaylist | None) -> bool:
    """Made once and since deleted by the user: not made again."""
    return record is not None and record.playlist_id is None and record.songs > 0


async def _mbids(db: AsyncSession, servers: list[UserServer], names: list[str]) -> dict[str, str]:
    """MusicBrainz IDs of library artists by normalised name (to find their setlists)."""
    keys = [normalise_artist(n) for n in names]
    rows = await db.execute(
        select(Artist.name_key, Artist.mbid).where(
            Artist.server_id.in_([us.server_id for us in servers]),
            Artist.name_key.in_(keys),
            Artist.mbid.is_not(None),
        )
    )
    return {key: mbid for key, mbid in rows.all() if key and mbid}


async def make_gig_playlists(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    user: User,
    now: dt.datetime | None = None,
) -> Made:
    """Make (or refresh) the user's gig playlists that are due. Commits as it goes."""
    result = Made()
    before, after = wants(user)
    if not (before or after):
        return result
    key, _ = await get_setlistfm_api_key(db, settings)
    if not key:
        return result
    servers = [
        us
        for us in await db.scalars(select(UserServer).where(UserServer.user_id == user.id))
        if us.music_section_keys
    ]
    if not servers:
        return result
    now = now or dt.datetime.now(dt.UTC)
    today = now.date()
    try:
        if before:
            await _before(db, http, settings, key, user, servers, today, now, result)
        if after:
            await _after(db, http, settings, key, user, servers, today, now, result)
    except ImportError_ as exc:  # setlist.fm unreachable or busy: next run
        log.info("gig playlists for user %s paused: %s", user.id, exc)
    await db.commit()
    return result


async def _before(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    key: str,
    user: User,
    servers: list[UserServer],
    today: dt.date,
    now: dt.datetime,
    result: Made,
) -> None:
    rows = list(
        await db.scalars(
            select(LiveShow)
            .where(
                LiveShow.user_id == user.id,
                LiveShow.starred_at.is_not(None),
                LiveShow.start_date >= today,
                LiveShow.start_date <= today + dt.timedelta(days=BEFORE_DAYS),
            )
            .order_by(LiveShow.start_date, LiveShow.id)
        )
    )
    known = {
        r.ref: r
        for r in await db.scalars(
            select(GigPlaylist).where(GigPlaylist.user_id == user.id, GigPlaylist.kind == "before")
        )
    }
    for group in merge(rows):
        refs = [f"{r.source}:{r.source_id}"[:128] for r in sorted(group, key=lambda r: r.id)]
        record = next((known[r] for r in refs if r in known), None)
        if _deleted(record) or (record and now - record.refreshed_at < REFRESH_EVERY):
            continue
        # The user's artists on the bill (headliners last: they play last).
        artists: dict[str, dict[str, Any]] = {}
        for row in group:
            for a in row.artists:
                artists.setdefault(normalise_artist(a["name"]), a)
        bill = sorted(artists.values(), key=lambda a: bool(a.get("headliner")))[-MAX_ARTISTS:]
        if not bill:
            continue
        mbids = await _mbids(db, servers, [a["name"] for a in bill])
        parts = []
        for a in bill:
            usual = await recent_setlist(
                http, settings, key, a["name"], mbids.get(normalise_artist(a["name"])), today
            )
            parts.append(Part(a["name"], usual, a.get("rating_key")))
        us, songs = await _best_server(db, servers, parts)
        show = group[0]
        names = ", ".join(reversed([a["name"] for a in bill]))  # headliners first
        place = ", ".join(x for x in (show.venue, show.city) if x)
        await _save(
            db,
            record=record,
            user=user,
            kind="before",
            ref=record.ref if record else refs[0],
            gig_date=show.start_date,
            us=us,
            songs=songs,
            name=f"Before the gig: {names}, {nice_date(show.start_date)}",
            description=(
                f"What {names} {'has' if len(bill) == 1 else 'have'} been playing lately "
                f"(recent setlists on setlist.fm), for the gig"
                + (f" at {place}" if place else "")
                + f" on {nice_date(show.start_date)}. Made by Plekstender, refreshed weekly "
                "until the gig."
            ),
            now=now,
            result=result,
        )
        await db.commit()


async def _after(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    key: str,
    user: User,
    servers: list[UserServer],
    today: dt.date,
    now: dt.datetime,
    result: Made,
) -> None:
    gigs = list(
        await db.scalars(
            select(Gig)
            .where(
                Gig.user_id == user.id,
                Gig.date < today,
                Gig.date >= today - dt.timedelta(days=AFTER_DAYS),
            )
            .order_by(Gig.date, Gig.id)
        )
    )
    done = set(
        await db.scalars(
            select(GigPlaylist.ref).where(
                GigPlaylist.user_id == user.id, GigPlaylist.kind == "after"
            )
        )
    )
    for gig in gigs:
        if str(gig.id) in done or not gig.artists:
            continue
        if not gig.setlists:
            mbids = await _mbids(db, servers, [str(a.get("name") or "") for a in gig.artists])
            found = await gig_setlists(http, settings, key, gig, mbids)
            if not found:
                continue  # not on setlist.fm yet: next run
            gig.setlists = found
            await db.commit()
        # In the order played: the bill lists headliners first, they play last.
        order = [normalise_artist(str(a.get("name") or "")) for a in gig.artists]
        setlists = sorted(
            gig.setlists,
            key=lambda s: (
                -order.index(normalise_artist(s["artist"]))
                if normalise_artist(s["artist"]) in order
                else 0
            ),
        )
        parts = [
            Part(str(s["artist"]), _set_songs(s.get("songs") or []), _rating_key(gig, s["artist"]))
            for s in setlists
        ]
        us, songs = await _best_server(db, servers, parts)
        names = ", ".join(str(a.get("name")) for a in gig.artists[:MAX_ARTISTS])
        place = ", ".join(x for x in (gig.venue, gig.city) if x)
        await _save(
            db,
            record=None,
            user=user,
            kind="after",
            ref=str(gig.id),
            gig_date=gig.date,
            us=us,
            songs=songs,
            name=f"{names} live"
            + (f", {gig.venue}" if gig.venue else "")
            + f", {nice_date(gig.date)}",
            description=(
                "What was played"
                + (f" at {place}" if place else "")
                + f" on {nice_date(gig.date)}, from setlist.fm. Made by Plekstender."
            ),
            now=now,
            result=result,
        )
        await db.commit()


def _rating_key(gig: Gig, artist: str) -> int | None:
    wanted = normalise_artist(artist)
    for a in gig.artists:
        if normalise_artist(str(a.get("name") or "")) == wanted and a.get("rating_key"):
            return int(a["rating_key"])
    return None
