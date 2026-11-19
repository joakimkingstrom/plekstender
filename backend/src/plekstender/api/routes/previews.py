"""Song previews (40-second clips) for the signed-in user's libraries."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.library import visible
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import PreviewOut
from plekstender.db.models import Track, User, UserServer
from plekstender.previews import Clip, PreviewError, get_clip

router = APIRouter(prefix="/api/servers/{server_id}/tracks", tags=["previews"])

# Clips are 40 s: start no later than this, so there's always something to hear.
LATEST_START_MS = 25_000


async def visible_track(db: AsyncSession, user: User, us: UserServer, rating_key: int) -> Track:
    state, cond, where = visible(Track, user.id, us)
    track = await db.scalar(
        select(Track).join(state, cond).where(*where, Track.rating_key == rating_key)
    )
    if track is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Song not found")
    return track


def clip_url(server_id: int, rating_key: int) -> str:
    return f"/api/servers/{server_id}/tracks/{rating_key}/preview.m4a"


def preview_out(server_id: int, rating_key: int, clip: Clip) -> PreviewOut:
    # Start just before the music does, skipping any silence (but leave at least 15 s).
    start = max(0, min(clip.intro_ms - 300, LATEST_START_MS))
    return PreviewOut(
        rating_key=rating_key,
        url=clip_url(server_id, rating_key),
        start_ms=start,
        quiet_intro=clip.quiet_intro,
    )


async def load_clip(
    db: AsyncSession,
    user: User,
    us: UserServer,
    track: Track,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> Clip:
    try:
        return await get_clip(db, settings, server_client(us, cipher, settings, http), track)
    except PreviewError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get(
    "/{rating_key}/preview",
    response_model=PreviewOut,
    dependencies=[rate_limit("preview", limit=60, window=60)],
)
async def preview_info(
    server_id: int,
    rating_key: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> PreviewOut:
    """Prepare a song's preview clip (the first time takes a few seconds) and say where to
    play it from: where the music starts, after any silence."""
    us = await get_user_server(db, user, server_id)
    track = await visible_track(db, user, us, rating_key)
    clip = await load_clip(db, user, us, track, settings, http, cipher)
    return preview_out(server_id, rating_key, clip)


@router.get(
    "/{rating_key}/preview.m4a",
    response_class=FileResponse,
    responses={200: {"content": {"audio/mp4": {}}}},
)
async def preview_file(
    server_id: int,
    rating_key: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> FileResponse:
    """The clip itself (AAC, 40 s), with byte ranges for the audio player."""
    us = await get_user_server(db, user, server_id)
    track = await visible_track(db, user, us, rating_key)
    clip = await load_clip(db, user, us, track, settings, http, cipher)
    return FileResponse(
        clip.path,
        media_type="audio/mp4",
        headers={"Cache-Control": "private, max-age=86400"},
    )
