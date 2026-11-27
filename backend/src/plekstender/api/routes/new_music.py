"""Settings → New music: what the server's owner wants done when new artists and albums
arrive in Plex (see plekstender.new_music)."""

from __future__ import annotations

from fastapi import APIRouter

from plekstender import new_music
from plekstender.api.deps import DbDep, SettingsDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.schemas import NewMusicIn, NewMusicLyricsWrite, NewMusicOut
from plekstender.db.instance import get_fanart_api_key
from plekstender.db.models import PlexServer

router = APIRouter(prefix="/api/servers/{server_id}/new-music", tags=["new music"])


async def _out(
    db: DbDep, settings: SettingsDep, server_id: int, value: new_music.NewMusicSettings
) -> NewMusicOut:
    server = await db.get(PlexServer, server_id)
    api_key, _ = await get_fanart_api_key(db, settings)
    return NewMusicOut(
        logos=value.logos,
        covers=value.covers,
        lyrics=value.lyrics,
        lyrics_write=NewMusicLyricsWrite(**value.lyrics_write.model_dump())
        if value.lyrics_write
        else None,
        since=value.since,
        fanart_ready=bool(api_key),
        folder_ready=bool(server and server.local_path_prefix),
        tag_editing=bool(server and server.tag_editing),
    )


@router.get("", response_model=NewMusicOut)
async def get_new_music(
    server_id: int, db: DbDep, user: UserDep, settings: SettingsDep
) -> NewMusicOut:
    us = await owner_server(db, user, server_id)
    return await _out(db, settings, us.server_id, await new_music.load(db, us.server_id))


@router.put("", response_model=NewMusicOut)
async def put_new_music(
    server_id: int, body: NewMusicIn, db: DbDep, user: UserDep, settings: SettingsDep
) -> NewMusicOut:
    """Choose what's done for new artists and albums, after each library sync. Only what's
    added from now on counts as new."""
    us = await owner_server(db, user, server_id)
    value = new_music.NewMusicSettings(
        logos=body.logos,
        covers=body.covers,
        lyrics=body.lyrics,
        lyrics_write=new_music.LyricsWrite(**body.lyrics_write.model_dump())
        if body.lyrics_write
        else None,
    )
    saved = await new_music.save(db, us.server_id, value)
    await db.commit()
    return await _out(db, settings, us.server_id, saved)
