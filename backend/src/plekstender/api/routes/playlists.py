"""Smart playlists: the builder's helpers, a live preview, and saved playlists."""

from __future__ import annotations

import asyncio
import random
import re
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import (
    PlaylistDetail,
    PlaylistIn,
    PlaylistOut,
    PlaylistPreviewOut,
    PlaylistSearchOut,
    PlaylistSong,
    PlaylistSuggestionsOut,
    SeedArtist,
    SeedTrack,
)
from plekstender.db.models import Playlist, UserServer
from plekstender.playlists import builder
from plekstender.playlists.definition import Definition
from plekstender.playlists.service import (
    PlaylistError,
    m3u,
    remove_from_plex,
    select_songs,
    summarise,
)
from plekstender.plex.tv import PlexError
from plekstender.worker import ACTIVE, enqueue_playlist_build

router = APIRouter(prefix="/api/servers/{server_id}/playlists", tags=["playlists"])

PREVIEW_SONGS = 100
# Seeds asked for sonically similar items (each is one request to Plex).
SONIC_SEEDS = 3
KeyList = Annotated[list[int], Query(max_length=200)]


def _artist(r: Any) -> SeedArtist:
    return SeedArtist(rating_key=r.rating_key, title=r.title, thumb=r.thumb)


def _track(r: Any) -> SeedTrack:
    return SeedTrack(rating_key=r.rating_key, title=r.title, artist=r.artist, thumb=r.thumb)


def _scope(us: UserServer) -> dict[str, Any]:
    return {
        "user_id": us.user_id,
        "server_id": us.server_id,
        "sections": list(us.music_section_keys),
    }


def _out(p: Playlist) -> PlaylistOut:
    return PlaylistOut(
        **{name: getattr(p, name) for name in PlaylistOut.model_fields if name != "exportable"},
        exportable=bool(p.song_keys),
    )


async def _get(db: DbDep, us: UserServer, playlist_id: int) -> Playlist:
    playlist = await db.scalar(
        select(Playlist).where(Playlist.id == playlist_id, Playlist.user_server_id == us.id)
    )
    if playlist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Playlist not found")
    return playlist


# --- builder helpers -----------------------------------------------------------------------


@router.get("/search", response_model=PlaylistSearchOut)
async def search(
    server_id: int, db: DbDep, user: UserDep, q: Annotated[str, Query(min_length=1, max_length=100)]
) -> PlaylistSearchOut:
    us = await get_user_server(db, user, server_id)
    artists, tracks = await builder.search(db, q=q.strip(), **_scope(us))
    return PlaylistSearchOut(
        artists=[_artist(a) for a in artists], tracks=[_track(t) for t in tracks]
    )


@router.get("/tag-artists", response_model=list[SeedArtist])
async def tag_artists(server_id: int, tag_id: int, db: DbDep, user: UserDep) -> list[SeedArtist]:
    """A genre's or mood's top 10 artists, to start a playlist from."""
    us = await get_user_server(db, user, server_id)
    rows = await builder.top_artists_for_tag(db, tag_id=tag_id, **_scope(us))
    return [_artist(r) for r in rows]


@router.get("/suggestions", response_model=PlaylistSuggestionsOut)
async def suggestions(
    server_id: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
    artists: KeyList = [],  # noqa: B006 - FastAPI query default
    tracks: KeyList = [],  # noqa: B006
) -> PlaylistSuggestionsOut:
    """Artists similar to the chosen ones, and sonically similar artists and songs."""
    us = await get_user_server(db, user, server_id)
    scope = _scope(us)
    similar = await builder.similar_artists(db, artists=artists, **scope)

    # Sonic similarity comes from Plex (Plex Pass sonic analysis); skip it on any error.
    client = server_client(us, cipher, settings, http)
    seeds = artists[:SONIC_SEEDS] + tracks[:SONIC_SEEDS]
    results = await asyncio.gather(
        *(client.nearest(key, limit=20) for key in seeds), return_exceptions=True
    )
    chosen = set(artists) | set(tracks) | {a.rating_key for a in similar}
    sonic_artist_keys: list[int] = []
    sonic_track_keys: list[int] = []
    for found in results:
        if isinstance(found, BaseException):
            if not isinstance(found, (PlexError, httpx.HTTPError)):
                raise found
            continue
        for item in found:
            try:
                key = int(item["ratingKey"])
            except KeyError, TypeError, ValueError:
                continue
            target = {"artist": sonic_artist_keys, "track": sonic_track_keys}.get(
                str(item.get("type"))
            )
            if target is not None and key not in chosen and key not in target:
                target.append(key)
    sonic_a, sonic_t = await builder.visible_items(
        db, artist_keys=sonic_artist_keys, track_keys=sonic_track_keys, **scope
    )
    order_a = {k: i for i, k in enumerate(sonic_artist_keys)}
    order_t = {k: i for i, k in enumerate(sonic_track_keys)}
    return PlaylistSuggestionsOut(
        similar=[_artist(a) for a in similar],
        sonic_artists=[_artist(a) for a in sorted(sonic_a, key=lambda r: order_a[r.rating_key])][
            :20
        ],
        sonic_tracks=[_track(t) for t in sorted(sonic_t, key=lambda r: order_t[r.rating_key])][:20],
    )


@router.post(
    "/preview",
    response_model=PlaylistPreviewOut,
    dependencies=[rate_limit("playlist-preview", limit=120, window=60)],
)
async def preview(
    server_id: int, definition: Definition, db: DbDep, user: UserDep
) -> PlaylistPreviewOut:
    us = await get_user_server(db, user, server_id)
    songs, matched = await select_songs(db, us, definition, random.Random())  # noqa: S311
    summary = summarise(songs)
    return PlaylistPreviewOut(
        matched=matched,
        count=summary["count"],
        duration_ms=summary["duration_ms"],
        artists=summary["artists"],
        top_artists=summary["top_artists"],
        songs=[
            PlaylistSong(
                rating_key=s.rating_key,
                title=s.title,
                artist=s.artist,
                artist_key=s.artist_key,
                album=s.album,
                thumb=s.thumb,
                duration_ms=s.duration_ms,
                rating=s.rating,
            )
            for s in songs[:PREVIEW_SONGS]
        ],
    )


# --- saved playlists -----------------------------------------------------------------------


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(server_id: int, db: DbDep, user: UserDep) -> list[PlaylistOut]:
    us = await get_user_server(db, user, server_id)
    rows = await db.scalars(
        select(Playlist).where(Playlist.user_server_id == us.id).order_by(Playlist.name)
    )
    return [_out(p) for p in rows]


@router.get("/{playlist_id}", response_model=PlaylistDetail)
async def get_playlist(
    server_id: int, playlist_id: int, db: DbDep, user: UserDep
) -> PlaylistDetail:
    us = await get_user_server(db, user, server_id)
    playlist = await _get(db, us, playlist_id)
    definition = playlist.definition or {}
    artist_keys = [int(k) for k in definition.get("artists", [])]
    track_keys = [int(k) for k in definition.get("tracks", [])]
    artists, tracks = await builder.visible_items(
        db, artist_keys=artist_keys, track_keys=track_keys, **_scope(us)
    )
    order_a = {k: i for i, k in enumerate(artist_keys)}
    order_t = {k: i for i, k in enumerate(track_keys)}
    return PlaylistDetail(
        **_out(playlist).model_dump(),
        seed_artists=[_artist(a) for a in sorted(artists, key=lambda r: order_a[r.rating_key])],
        seed_tracks=[_track(t) for t in sorted(tracks, key=lambda r: order_t[r.rating_key])],
    )


async def _save_and_build(db: DbDep, playlist: Playlist) -> PlaylistOut:
    playlist.status = "queued"
    playlist.error = None
    await db.commit()
    await db.refresh(playlist)
    await enqueue_playlist_build(playlist)
    return _out(playlist)


def _apply(playlist: Playlist, body: PlaylistIn) -> None:
    playlist.name = body.name.strip()
    playlist.description = (body.description or "").strip() or None
    playlist.definition = body.definition.model_dump()
    playlist.display = body.display
    if playlist.schedule != body.schedule:
        playlist.next_run_at = None
    playlist.schedule = body.schedule


@router.post("", response_model=PlaylistOut, status_code=status.HTTP_201_CREATED)
async def create_playlist(
    server_id: int, body: PlaylistIn, db: DbDep, user: UserDep
) -> PlaylistOut:
    """Save a playlist and build it in Plex (in the background)."""
    us = await get_user_server(db, user, server_id)
    playlist = Playlist(user_server_id=us.id)
    _apply(playlist, body)
    db.add(playlist)
    return await _save_and_build(db, playlist)


@router.put("/{playlist_id}", response_model=PlaylistOut)
async def update_playlist(
    server_id: int, playlist_id: int, body: PlaylistIn, db: DbDep, user: UserDep
) -> PlaylistOut:
    """Change a playlist and rebuild it (its songs are replaced in place in Plex)."""
    us = await get_user_server(db, user, server_id)
    playlist = await _get(db, us, playlist_id)
    if playlist.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "This playlist is being built; try again.")
    _apply(playlist, body)
    return await _save_and_build(db, playlist)


@router.post("/{playlist_id}/build", response_model=PlaylistOut)
async def rebuild_playlist(
    server_id: int, playlist_id: int, db: DbDep, user: UserDep
) -> PlaylistOut:
    us = await get_user_server(db, user, server_id)
    playlist = await _get(db, us, playlist_id)
    if playlist.status in ACTIVE:
        return _out(playlist)
    return await _save_and_build(db, playlist)


@router.get(
    "/{playlist_id}/export.m3u",
    response_class=PlainTextResponse,
    responses={200: {"content": {"audio/x-mpegurl": {}}}},
)
async def export_m3u(
    server_id: int,
    playlist_id: int,
    db: DbDep,
    user: UserDep,
    paths: Annotated[Literal["plex", "relative"], Query()] = "plex",
) -> PlainTextResponse:
    """The songs of the last build as an M3U file, for other players. Paths are as Plex
    sees them, or relative to the music library's root folder."""
    us = await get_user_server(db, user, server_id)
    playlist = await _get(db, us, playlist_id)
    if not playlist.song_keys:
        raise HTTPException(status.HTTP_409_CONFLICT, "Build the playlist first.")
    body = await m3u(db, us, playlist, relative=paths == "relative")
    name = re.sub(r"[^A-Za-z0-9 ._-]+", "", playlist.name).strip() or "playlist"
    return PlainTextResponse(
        body,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{name}.m3u"'},
    )


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playlist(
    server_id: int,
    playlist_id: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    from_plex: bool = True,
) -> Response:
    """Delete the playlist here, and (by default) in Plex too."""
    us = await get_user_server(db, user, server_id)
    playlist = await _get(db, us, playlist_id)
    if from_plex:
        try:
            await remove_from_plex(db, http, settings, playlist)
        except PlaylistError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    await db.delete(playlist)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
