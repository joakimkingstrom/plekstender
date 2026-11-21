"""Background artwork jobs."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.artwork import service as art
from plekstender.artwork import sources
from plekstender.artwork.images import Transform
from plekstender.config import Settings
from plekstender.db.instance import get_fanart_api_key
from plekstender.db.models import Artist, ArtistArtwork, ArtworkJob, UserItemState, UserServer
from plekstender.plex.server import PlexServerClient
from plekstender.security.crypto import TokenCipher

log = logging.getLogger(__name__)

Runner = Callable[
    [AsyncSession, httpx.AsyncClient, Settings, ArtworkJob, UserServer], Awaitable[None]
]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def client_for(settings: Settings, http: httpx.AsyncClient, us: UserServer) -> PlexServerClient:
    cipher = TokenCipher(settings.derive_key("token-encryption"))
    token = cipher.decrypt(us.access_token_enc, purpose="user_server.access_token")
    return PlexServerClient(settings, http, us.connection_uri, token)


async def job_artists(db: AsyncSession, job: ArtworkJob, us: UserServer) -> list[int]:
    """The artists a job covers: the given keys, or every artist with the given status."""
    keys = job.params.get("rating_keys")
    if keys:
        return [int(k) for k in keys]
    status = job.params.get("status", "none")
    current = func.coalesce(ArtistArtwork.status, "none")
    rows = await db.scalars(
        select(Artist.rating_key)
        .join(
            UserItemState,
            (UserItemState.user_id == us.user_id)
            & (UserItemState.server_id == Artist.server_id)
            & (UserItemState.rating_key == Artist.rating_key),
        )
        .outerjoin(
            ArtistArtwork,
            (ArtistArtwork.server_id == Artist.server_id)
            & (ArtistArtwork.rating_key == Artist.rating_key),
        )
        .where(
            Artist.server_id == us.server_id,
            Artist.section_key.in_(us.music_section_keys),
            current == status,
        )
        .order_by(func.lower(func.coalesce(Artist.title_sort, Artist.title)))
    )
    return list(rows)


async def run_fanart(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, job: ArtworkJob, us: UserServer
) -> None:
    """Apply each artist's most-liked fanart.tv logo with the job's transform."""
    api_key, _ = await get_fanart_api_key(db, settings)
    if not api_key:
        raise art.ArtworkError("fanart.tv isn't set up: an admin can add an API key in Settings.")
    client = client_for(settings, http, us)
    transform = Transform.model_validate(job.params.get("transform") or {})
    keys = await job_artists(db, job, us)
    stats = {"applied": 0, "no_mbid": 0, "no_logo": 0, "failed": 0, "folder_errors": 0, **job.stats}
    done = int(job.progress.get("done", 0))  # resume after a restart
    job.progress = {"done": done, "total": len(keys)}
    await db.commit()
    for key in keys[done:]:
        artist = await db.get(Artist, (us.server_id, key))
        if artist is not None:
            try:
                mbid = await art.artist_mbid(db, http, settings, artist)
                logos = (
                    await sources.fanart_logos(http, settings.fanart_api_url, api_key, mbid)
                    if mbid
                    else []
                )
                if not mbid:
                    stats["no_mbid"] += 1
                elif not logos:
                    stats["no_logo"] += 1
                else:
                    spec = art.SourceSpec(
                        kind="candidate", token=art.sign_ref(settings, logos[0].url)
                    )
                    applied = await art.apply(
                        db, http, settings, client, artist, spec, transform, us.user_id
                    )
                    stats["applied"] += 1
                    if applied.folder_error:
                        stats["folder_errors"] += 1
            except (art.ArtworkError, sources.SourceError) as exc:
                log.info("fanart logo for %s failed: %s", artist.title, exc)
                stats["failed"] += 1
        done += 1
        job.progress = {"done": done, "total": len(keys)}
        job.stats = dict(stats)
        job.updated_at = utcnow()
        await db.commit()


def _runners() -> dict[str, Runner]:
    from plekstender.artwork import covers, packs

    return {
        "fanart": run_fanart,
        "export": packs.run_export,
        "import": packs.run_import,
        "covers": covers.run_best_covers,
    }


async def run_job(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, job_id: int
) -> None:
    job = await db.get(ArtworkJob, job_id)
    if job is None or job.status != "queued":
        return
    us = await db.get(UserServer, job.user_server_id)
    if us is None:
        return
    now = utcnow()
    job.status = "running"
    job.started_at = job.started_at or now
    job.updated_at = now
    job.error = None
    await db.commit()
    try:
        await _runners()[job.kind](db, http, settings, job, us)
        job.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, art.ArtworkError):
            log.exception("artwork job %s failed", job_id)
        await db.rollback()
        failed = await db.get(ArtworkJob, job_id, populate_existing=True)
        if failed is None:
            return
        job = failed
        job.status = "failed"
        job.error = (
            str(exc)
            if isinstance(exc, art.ArtworkError)
            else "Unexpected error; see the worker log."
        )
    finished = utcnow()
    job.finished_at = finished
    job.updated_at = finished
    await db.commit()
