"""Writing a smart playlist to Plex, and when to rebuild it."""

from __future__ import annotations

import datetime as dt
import logging
import random
from collections import Counter
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import Playlist, PlexServer, UserServer
from plekstender.playlists.builder import Song, matching_songs, pick
from plekstender.playlists.definition import Definition
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexAuthError, PlexError
from plekstender.security.crypto import DecryptionError, TokenCipher

log = logging.getLogger(__name__)

SCHEDULES = {
    "daily": dt.timedelta(days=1),
    "weekly": dt.timedelta(days=7),
    "monthly": dt.timedelta(days=30),
}
# Songs per request when adding to a Plex playlist (keeps the URL a sane length).
BATCH = 200


class PlaylistError(Exception):
    """Something the user should see, e.g. 'no songs match'."""


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def next_run(schedule: str, after: dt.datetime) -> dt.datetime | None:
    step = SCHEDULES.get(schedule)
    return after + step if step else None


def summarise(songs: list[Song]) -> dict[str, Any]:
    artists = Counter(s.artist or "Unknown" for s in songs)
    return {
        "count": len(songs),
        "duration_ms": sum(s.duration_ms or 0 for s in songs),
        "artists": len(artists),
        "top_artists": [name for name, _ in artists.most_common(5)],
    }


async def select_songs(
    db: AsyncSession, us: UserServer, definition: Definition, rng: random.Random
) -> tuple[list[Song], int]:
    """The songs a build would use, and how many matched before the limit."""
    songs = await matching_songs(
        db,
        user_id=us.user_id,
        server_id=us.server_id,
        sections=list(us.music_section_keys),
        d=definition,
    )
    return pick(songs, definition, rng), len(songs)


def _client(settings: Settings, http: httpx.AsyncClient, us: UserServer) -> PlexServerClient:
    cipher = TokenCipher(settings.derive_key("token-encryption"))
    try:
        token = cipher.decrypt(us.access_token_enc, purpose="user_server.access_token")
    except DecryptionError as exc:
        raise PlaylistError("Reconnect this server in Settings, then try again.") from exc
    return PlexServerClient(settings, http, us.connection_uri, token)


async def write_to_plex(
    client: PlexServerClient,
    machine_id: str,
    playlist: Playlist,
    keys: list[int],
) -> int:
    """Create the Plex playlist, or replace an existing one's songs in place. Returns its key."""
    batches = [keys[i : i + BATCH] for i in range(0, len(keys), BATCH)]
    key = playlist.plex_playlist_key
    if key is not None:
        try:
            await client.clear_playlist(key)
            await client.rename_playlist(key, playlist.name, playlist.description)
        except PlexAuthError:
            raise
        except PlexError as exc:
            if exc.status != 404:
                raise
            key = None  # deleted in Plex: make a new one
    if key is None:
        key = await client.create_playlist(playlist.name, machine_id, batches[0])
        if playlist.description:
            await client.rename_playlist(key, playlist.name, playlist.description)
        batches = batches[1:]
    for batch in batches:
        await client.add_to_playlist(key, machine_id, batch)
    return key


async def build(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    playlist_id: int,
    rng: random.Random | None = None,
) -> None:
    """Rebuild one playlist in Plex, recording the outcome on the playlist."""
    playlist = await db.get(Playlist, playlist_id)
    if playlist is None:
        return
    us = await db.get(UserServer, playlist.user_server_id)
    server = await db.get(PlexServer, us.server_id) if us else None
    if us is None or server is None:
        return
    playlist.status = "running"
    playlist.updated_at = utcnow()
    await db.commit()
    try:
        definition = Definition.model_validate(playlist.definition)
        songs, matched = await select_songs(
            db,
            us,
            definition,
            rng or random.Random(),  # noqa: S311 - not security
        )
        if not songs:
            raise PlaylistError("No songs match this playlist; nothing was changed in Plex.")
        client = _client(settings, http, us)
        playlist.plex_playlist_key = await write_to_plex(
            client, server.machine_identifier, playlist, [s.rating_key for s in songs]
        )
        playlist.status = "ok"
        playlist.error = None
        playlist.last_result = {**summarise(songs), "matched": matched}
    except PlaylistError as exc:
        playlist.status = "error"
        playlist.error = str(exc)
    except PlexAuthError:
        playlist.status = "error"
        playlist.error = "Plex rejected your access to this server; reconnect it in Settings."
    except (PlexError, httpx.HTTPError) as exc:
        log.warning("playlist %s: Plex error: %s", playlist_id, exc)
        playlist.status = "error"
        playlist.error = "Plex couldn't be reached or refused the change; try again later."
    now = utcnow()
    playlist.last_built_at = now
    playlist.updated_at = now
    playlist.next_run_at = next_run(playlist.schedule, now)
    await db.commit()


async def remove_from_plex(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, playlist: Playlist
) -> None:
    """Delete the Plex playlist too (best effort: it may already be gone)."""
    if playlist.plex_playlist_key is None:
        return
    us = await db.get(UserServer, playlist.user_server_id)
    if us is None:
        return
    try:
        await _client(settings, http, us).delete_playlist(playlist.plex_playlist_key)
    except PlexError as exc:
        if exc.status != 404:
            raise PlaylistError("Plex couldn't delete the playlist; try again later.") from exc
    except (PlaylistError, httpx.HTTPError) as exc:
        raise PlaylistError("Plex couldn't delete the playlist; try again later.") from exc
