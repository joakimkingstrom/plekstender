"""Background job: copy plays from Plex's play history into `listens`."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from plekstender.config import Settings
from plekstender.db.models import Album, Artist, HistoryCursor, Listen, Track, User, UserServer
from plekstender.history.duplicates import mark_duplicates
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError
from plekstender.security.crypto import TokenCipher
from plekstender.sync.parse import epoch, normalise_artist, normalise_title

log = logging.getLogger(__name__)

PAGE_SIZE = 500
# Re-read a little before the newest play seen, in case Plex records plays late.
OVERLAP = dt.timedelta(minutes=10)
# On a server owner's connection, Plex lists the owner as account 1; everyone else
# appears under their plex.tv account ID.
OWNER_ACCOUNT_ID = 1


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class CaptureResult:
    added: int = 0
    seen: int = 0
    # Earliest new listen per user, to look for duplicates from there on.
    earliest: dict[int, dt.datetime] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


async def account_map(db: AsyncSession, us: UserServer) -> dict[int, int]:
    """Plex account ID on this server → Plekstender user, for plays this connection may store.

    Only people who connected the server in Plekstender get their plays stored. A shared
    user's own connection only stores that user's plays.
    """
    if not us.is_owner:
        plex_id = await db.scalar(select(User.plex_account_id).where(User.id == us.user_id))
        return {} if plex_id is None else {int(plex_id): us.user_id}
    rows = await db.execute(
        select(User.id, User.plex_account_id)
        .join(UserServer, UserServer.user_id == User.id)
        .where(UserServer.server_id == us.server_id, UserServer.id != us.id)
    )
    accounts = {int(plex_id): user_id for user_id, plex_id in rows.all()}
    accounts[OWNER_ACCOUNT_ID] = us.user_id
    return accounts


async def _tracks(db: AsyncSession, server_id: int, keys: set[int]) -> dict[int, Any]:
    if not keys:
        return {}
    album_artist = aliased(Artist)
    rows = await db.execute(
        select(Track, Album.title, album_artist.title)
        .outerjoin(
            Album,
            (Album.server_id == Track.server_id) & (Album.rating_key == Track.album_rating_key),
        )
        .outerjoin(
            album_artist,
            (album_artist.server_id == Track.server_id)
            & (album_artist.rating_key == Track.artist_rating_key),
        )
        .where(Track.server_id == server_id, Track.rating_key.in_(keys))
    )
    return {t.rating_key: (t, album, artist) for t, album, artist in rows.all()}


def _key(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


async def listen_rows(
    db: AsyncSession, server_id: int, items: list[dict[str, Any]], accounts: dict[int, int]
) -> list[dict[str, Any]]:
    """Listens for the history entries that are music plays by mapped accounts.

    Names come from the library mirror when the track is known (it has the track artist
    for compilations, which history entries lack), otherwise from the entry itself.
    """
    wanted = [
        i
        for i in items
        if i.get("type") == "track" and _key(i.get("accountID")) in accounts and i.get("historyKey")
    ]
    tracks = await _tracks(db, server_id, {k for i in wanted if (k := _key(i.get("ratingKey")))})
    rows = []
    for item in wanted:
        viewed = epoch(item.get("viewedAt"))
        rating_key = _key(item.get("ratingKey"))
        if viewed is None:
            continue
        known = tracks.get(rating_key) if rating_key is not None else None
        if known:
            track, album_title, album_artist = known
            title, album = track.title, album_title
            artist = track.original_title or album_artist
            duration_ms = track.duration_ms
        else:
            title, album = item.get("title"), item.get("parentTitle")
            artist = item.get("originalTitle") or item.get("grandparentTitle")
            duration_ms = _key(item.get("duration"))
        if not title or not artist:
            continue
        # Plex records a play when the track (nearly) finishes; store when it started.
        started = viewed - dt.timedelta(milliseconds=duration_ms) if duration_ms else viewed
        rows.append(
            {
                "user_id": accounts[int(item["accountID"])],
                "listened_at": started,
                "source": "plex",
                "artist_name": artist,
                "album_name": album or None,
                "track_name": title,
                "artist_key": normalise_artist(artist),
                "album_key": normalise_title(album) if album else None,
                "track_key": normalise_title(title),
                "duration_ms": duration_ms,
                "server_id": server_id,
                "rating_key": rating_key,
                "source_ref": str(item["historyKey"]),
            }
        )
    return rows


async def insert_plex_listens(
    db: AsyncSession, rows: list[dict[str, Any]]
) -> list[tuple[int, dt.datetime]]:
    """Insert listens not stored yet. Returns (user_id, listened_at) of the new ones."""
    if not rows:
        return []
    stmt = (
        insert(Listen)
        .values(rows)
        .on_conflict_do_nothing()
        .returning(Listen.user_id, Listen.listened_at)
    )
    return [(user_id, at) for user_id, at in (await db.execute(stmt)).all()]


async def capture_plays(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, us: UserServer
) -> CaptureResult:
    """Read new plays for one server connection, all its music libraries."""
    result = CaptureResult()
    cipher = TokenCipher(settings.derive_key("token-encryption"))
    token = cipher.decrypt(us.access_token_enc, purpose="user_server.access_token")
    client = PlexServerClient(settings, http, us.connection_uri, token)
    accounts = await account_map(db, us)
    # Plain values: a rollback after a failed request expires `us`.
    us_id, server_id, sections = us.id, us.server_id, list(us.music_section_keys)

    for section in sections:
        cursor = await db.get(HistoryCursor, (us_id, section))
        if cursor is None:
            cursor = HistoryCursor(user_server_id=us_id, section_key=section)
            db.add(cursor)
        since = cursor.viewed_through - OVERLAP if cursor.viewed_through else None
        start = 0
        try:
            while True:
                items, total = await client.history(
                    section,
                    start=start,
                    size=PAGE_SIZE,
                    viewed_since=int(since.timestamp()) if since else None,
                )
                rows = await listen_rows(db, server_id, items, accounts)
                for user_id, at in await insert_plex_listens(db, rows):
                    result.added += 1
                    if user_id not in result.earliest or at < result.earliest[user_id]:
                        result.earliest[user_id] = at
                result.seen += len(items)
                newest = max((v for i in items if (v := epoch(i.get("viewedAt")))), default=None)
                if newest and (cursor.viewed_through is None or newest > cursor.viewed_through):
                    cursor.viewed_through = newest
                cursor.last_run_at = utcnow()
                cursor.last_error = None
                await db.commit()
                start += len(items)
                if not items or start >= total:
                    break
            cursor.backfilled_at = cursor.backfilled_at or utcnow()
        except (httpx.HTTPError, PlexError) as exc:
            await db.rollback()
            cursor = await db.get(HistoryCursor, (us_id, section), populate_existing=True)
            if cursor is None:
                cursor = HistoryCursor(user_server_id=us_id, section_key=section)
                db.add(cursor)
            message = describe(exc)
            cursor.last_run_at = utcnow()
            cursor.last_error = message
            result.errors[section] = message
        await db.commit()

    for user_id, since in result.earliest.items():
        await mark_duplicates(db, user_id, since)
    await db.commit()
    return result


def describe(exc: Exception) -> str:
    if isinstance(exc, PlexError) and exc.status in (401, 403):
        return "Plex didn't allow reading play history with this account."
    if isinstance(exc, PlexError):
        return f"Plex error: {exc}"
    return "Couldn't reach the Plex server."
