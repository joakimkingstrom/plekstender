"""Automatic actions for new music: what a server's owner wants done when new artists and
albums arrive in Plex (Settings → New music), done after each library sync.

- fanart.tv's most liked logo for new artists (the artwork `fanart` job, for those keys);
- the best cover match for new albums (the artwork `covers` job, with the owner's sources);
- lyrics for the new albums' songs (a lyrics lookup, written where the owner chose, when
  tag editing is on for the server).

"New" is added to Plex after the last check: each run moves that mark on, and turning the
first action on sets it to then, so the library already there isn't worked through. One
notification says what was found and what's being done (the jobs show on Settings → Jobs).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.instance import get_fanart_api_key, get_instance_value, put_instance_value
from plekstender.db.models import (
    Album,
    Artist,
    ArtworkJob,
    FileScan,
    PlexServer,
    User,
    UserServer,
)
from plekstender.notifications import Problem, raise_problem

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")


class LyricsWrite(BaseModel):
    """Where found lyrics go (as on the lyrics page); none of them: only looked up."""

    sidecar: bool = True
    lyricsfile: bool = False
    embedded: bool = False
    overwrite: bool = False


class NewMusicSettings(BaseModel):
    logos: bool = False
    covers: bool = False
    lyrics: bool = False
    lyrics_write: LyricsWrite | None = None
    # Things added to Plex after this are new (moved on after each run).
    since: dt.datetime | None = None


def _key(server_id: int) -> str:
    return f"new-music:{server_id}"


async def load(db: AsyncSession, server_id: int) -> NewMusicSettings:
    return NewMusicSettings.model_validate(await get_instance_value(db, _key(server_id)) or {})


async def save(
    db: AsyncSession, server_id: int, value: NewMusicSettings, now: dt.datetime | None = None
) -> NewMusicSettings:
    """Save the owner's choices; the first one turned on starts the clock (what's in the
    library already isn't new)."""
    old = await load(db, server_id)
    any_on = value.logos or value.covers or value.lyrics
    since = old.since if (old.logos or old.covers or old.lyrics) else None
    value.since = (since or now or dt.datetime.now(dt.UTC)) if any_on else None
    await put_instance_value(db, _key(server_id), value.model_dump(mode="json"))
    return value


async def _new(db: AsyncSession, model: Any, us: UserServer, since: dt.datetime) -> list[Any]:
    return list(
        await db.scalars(
            select(model)
            .where(
                model.server_id == us.server_id,
                model.section_key.in_(us.music_section_keys),
                model.added_at > since,
            )
            .order_by(model.added_at)
        )
    )


def _and(items: list[str]) -> str:
    """ "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _names(items: list[str], most: int = 3) -> str:
    return ", ".join(items[:most]) + (f" and {len(items) - most} more" if len(items) > most else "")


async def act(
    db: AsyncSession, settings: Settings, server_id: int, now: dt.datetime | None = None
) -> dict[str, int]:
    """After a sync: start the chosen jobs for what's new since the last time. Returns how
    many artists and albums were new, and which jobs started (job kind → 1)."""
    from plekstender.worker import enqueue_artwork_job, enqueue_lyrics_lookup

    now = now or dt.datetime.now(dt.UTC)
    choices = await load(db, server_id)
    if not (choices.logos or choices.covers or choices.lyrics) or choices.since is None:
        return {}
    us = await db.scalar(
        select(UserServer).where(UserServer.server_id == server_id, UserServer.is_owner)
    )
    server = await db.get(PlexServer, server_id)
    if us is None or server is None or not us.music_section_keys:
        return {}
    artists = await _new(db, Artist, us, choices.since)
    albums = await _new(db, Album, us, choices.since)
    newest = max((x.added_at for x in [*artists, *albums] if x.added_at), default=None)
    if newest is not None:
        choices.since = newest
        await put_instance_value(db, _key(server_id), choices.model_dump(mode="json"))
    await db.commit()
    if not artists and not albums:
        return {}

    done: dict[str, int] = {"artists": len(artists), "albums": len(albums)}
    doing: list[str] = []
    notes: list[str] = []
    user = await db.get(User, us.user_id)
    prefs = (user.preferences or {}) if user else {}
    busy = await db.scalar(
        select(func.count()).where(ArtworkJob.server_id == server_id, ArtworkJob.status.in_(ACTIVE))
    )
    jobs: list[ArtworkJob] = []
    if choices.logos and artists:
        api_key, _ = await get_fanart_api_key(db, settings)
        if api_key:
            jobs.append(_artwork(us, "fanart", {"rating_keys": [a.rating_key for a in artists]}))
            doing.append("logos")
        else:
            notes.append("No logos: fanart.tv isn't set up (an admin adds its key in Settings).")
    if choices.covers and albums:
        params: dict[str, Any] = {"rating_keys": [a.rating_key for a in albums]}
        if sources := prefs.get("best_cover_sources"):
            params["sources"] = sources
        jobs.append(_artwork(us, "covers", params))
        doing.append("covers")
    for job in jobs:
        db.add(job)
    lookup: FileScan | None = None
    if choices.lyrics and albums:
        if not server.local_path_prefix:
            notes.append("No lyrics: the music folder isn't set up (Settings → Music files).")
        else:
            lyrics: dict[str, Any] = {"albums": sorted(a.rating_key for a in albums), "mode": "new"}
            write = choices.lyrics_write
            if (
                write
                and server.tag_editing
                and (write.sidecar or write.lyricsfile or write.embedded)
            ):
                lyrics |= {"write": write.model_dump(), "user_id": us.user_id}
            elif write:
                notes.append("Lyrics only looked up, not written: tag editing is off.")
            lookup = FileScan(server_id=server_id, kind="lyrics", params=lyrics, status="queued")
            db.add(lookup)
            doing.append("lyrics")
    await db.commit()
    for job in jobs:  # one artwork job at a time per server: these wait their turn
        await enqueue_artwork_job(job)
        done[job.kind] = 1
    if lookup is not None:
        await enqueue_lyrics_lookup(lookup)
        done["lyrics"] = 1
    if busy:
        notes.append("They start once the artwork job running now is done.")

    what = " and ".join(p for p in (
        f"{len(albums)} {'album' if len(albums) == 1 else 'albums'}" if albums else "",
        f"{len(artists)} new {'artist' if len(artists) == 1 else 'artists'}" if artists else "",
    ) if p)  # fmt: skip
    # Big to small: "Paramore · After Laughter".
    keys = {a.artist_rating_key for a in albums if a.artist_rating_key}
    by_key = {
        k: t
        for k, t in await db.execute(
            select(Artist.rating_key, Artist.title).where(
                Artist.server_id == server_id, Artist.rating_key.in_(keys)
            )
        )
    }
    detail = _names(
        [" · ".join(filter(None, [by_key.get(a.artist_rating_key or 0), a.title])) for a in albums]
        or [a.title for a in artists]
    )
    action = (
        "Getting "
        + _and([{"logos": "logos", "covers": "best covers", "lyrics": "lyrics"}[d] for d in doing])
        if doing
        else "Nothing to do for them"
    )
    await raise_problem(
        db,
        Problem(
            key=f"new-music:{server_id}:{now.isoformat()}",
            user_id=us.user_id,
            level="info",
            title=f"New in Plex: {what}",
            detail=" ".join([f"{detail}. {action}.", *notes]),
            link="/settings/jobs",
        ),
        now,
    )
    await db.commit()
    return done


def _artwork(us: UserServer, kind: str, params: dict[str, Any]) -> ArtworkJob:
    return ArtworkJob(
        server_id=us.server_id,
        user_server_id=us.id,
        kind=kind,
        status="queued",
        params=params,
        progress={},
        stats={},
    )
