"""Background artwork jobs."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.artwork import service as art
from plekstender.artwork import sources, undo
from plekstender.artwork.images import Transform
from plekstender.config import Settings
from plekstender.db.instance import get_fanart_api_key
from plekstender.db.models import Artist, ArtistArtwork, ArtworkJob, UserItemState, UserServer
from plekstender.jobs import JobStopped, check_stop
from plekstender.plex.server import client_for

log = logging.getLogger(__name__)

Runner = Callable[
    [AsyncSession, httpx.AsyncClient, Settings, ArtworkJob, UserServer], Awaitable[None]
]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


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
        await check_stop(db, ArtworkJob, job.id)
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
                    before = await undo.remember(db, client, job, "artists", key)
                    applied = await art.apply(
                        db, http, settings, client, artist, spec, transform, us.user_id
                    )
                    undo.keep(job, "artists", key, before)
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


async def wind_up(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    job: ArtworkJob,
    us: UserServer | None,
    stopped: JobStopped,
) -> str:
    """Wind a stopped job up (the worker's, or the API's when no worker has it); what to
    tell the user."""
    if job.kind == "export":
        if job.result_path:
            await asyncio.to_thread(Path(job.result_path).unlink, missing_ok=True)
        job.result_path = None
        return "Stopped: nothing was exported."
    if not stopped.cancel or us is None:
        return "Stopped. What it changed so far stays." if job.undo else "Stopped."
    put = await undo.put_back(db, client_for(settings, http, us), job)
    job.undo = {}
    if not put:
        return "Cancelled. It hadn't changed anything yet."
    return (
        f"Cancelled: {put} {'picture was' if put == 1 else 'pictures were'} put back as they were."
    )


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
    except JobStopped as stopped:
        await db.rollback()
        job = await db.get(ArtworkJob, job_id, populate_existing=True) or job
        job.status = "failed"
        owner = await db.get(UserServer, job.user_server_id, populate_existing=True)
        job.error = await wind_up(db, http, settings, job, owner, stopped)
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
