"""Stopping long jobs from the app (see plekstender.jobs): artwork jobs, music folder jobs
(scans, identifying files, duplicates), edits being written, and history imports and sends.

"Stop" keeps what's done; "cancel" also takes it back where that's possible: artwork put
back as it was, matches found forgotten, what an edit wrote undone, imported plays removed.
A running job does it at its next item; one that's queued, or whose worker has gone, is
wound up here and now.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.schemas import JobOut, JobsOut, JobStopIn, JobStopOut, JobWaitingOut
from plekstender.artwork import jobs as artwork_jobs
from plekstender.db.models import (
    ArtworkJob,
    FileScan,
    ListenImport,
    TagChange,
    TagChangeSet,
    User,
    UserServer,
)
from plekstender.history.store import stopped_import
from plekstender.job_list import Job, jobs_for
from plekstender.jobs import JobStopped, abandoned
from plekstender.tags import apply, lyrics
from plekstender.tags.identify import forget_run

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

Kind = Literal["artwork", "files", "edits", "imports"]
MODELS: dict[str, Any] = {
    "artwork": ArtworkJob,
    "files": FileScan,
    "edits": TagChangeSet,
    "imports": ListenImport,
}


async def _owns_server(db: DbDep, user: User, server_id: int) -> bool:
    return (
        await db.scalar(
            select(UserServer.id).where(
                UserServer.user_id == user.id,
                UserServer.server_id == server_id,
                UserServer.is_owner.is_(True),
            )
        )
        is not None
    )


async def _job(db: DbDep, user: User, kind: str, job_id: int) -> Any:
    job = await db.get(MODELS[kind], job_id)
    allowed = job is not None and (
        job.user_id == user.id if kind == "imports" else await _owns_server(db, user, job.server_id)
    )
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return job


def _job_out(job: Job) -> JobOut:
    return JobOut(
        kind=job.kind,
        id=job.id,
        what=job.what,
        subkind=job.subkind,
        status=job.status,
        server_id=job.server_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        progress=job.progress,
        error=job.error,
        stop_requested=job.stop_requested,
        stoppable=job.stoppable,
        waiting_for=JobWaitingOut(what=job.waiting_for.what, since=job.waiting_for.since)
        if job.waiting_for
        else None,
    )


@router.get("", response_model=JobsOut)
async def list_jobs(db: DbDep, user: UserDep) -> JobsOut:
    """Your jobs (and those on servers you own): running and waiting ones, each waiting one
    with what it waits for, then the latest finished."""
    active, finished = await jobs_for(db, user.id)
    return JobsOut(active=[_job_out(j) for j in active], finished=[_job_out(j) for j in finished])


@router.post("/{kind}/{job_id}/stop", response_model=JobStopOut)
async def stop_job(
    kind: Kind,
    job_id: int,
    body: JobStopIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
) -> JobStopOut:
    """Stop a job (keeping what it did), or cancel it (taking that back where possible)."""
    job = await _job(db, user, kind, job_id)
    if job.status not in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "It isn't running.")
    request: Literal["stop", "cancel"] = "cancel" if body.cancel else "stop"
    if kind == "files" and job.kind == "lyrics":
        raise HTTPException(status.HTTP_409_CONFLICT, "Stop a lyrics lookup from /api/lyrics.")
    if job.status == "running" and not abandoned(job):
        job.stop_requested = request  # the job does it before its next item
        await db.commit()
        return JobStopOut(status=job.status, error=job.error, stop_requested=request)
    # Queued, or its worker has gone: wind it up here.
    job.stop_requested = request
    job.status = "failed"
    job.error = await _wind_up(db, kind, job, user, body.cancel, settings, http)
    now = dt.datetime.now(dt.UTC)
    if hasattr(job, "finished_at"):
        job.finished_at = now
    job.updated_at = now
    await db.commit()
    return JobStopOut(status=job.status, error=job.error, stop_requested=request)


async def _wind_up(
    db: DbDep,
    kind: str,
    job: Any,
    user: User,
    cancel: bool,
    settings: SettingsDep,
    http: HttpDep,
) -> str:
    if kind == "imports":
        if job.source == "lb_send":
            return "Stopped. What it sent stays on ListenBrainz; resuming carries on from there."
        return await stopped_import(db, job, cancel)
    if kind == "edits":
        left = await db.execute(
            update(TagChange)
            .where(TagChange.change_set_id == job.id, TagChange.status == "pending")
            .values(status="failed", error="The edit was stopped first.")
        )
        unwritten = int(getattr(left, "rowcount", 0) or 0)
        await apply.finish(db, job, job.id)  # failed, or partial if some were written
        if cancel:
            await lyrics.undo_edit(db, job.id, user.id, why="a cancelled edit")
        return (
            f"Stopped: {unwritten} {'change was' if unwritten == 1 else 'changes were'}"
            " not written." + (" What was written is being undone." if cancel else "")
        )
    if kind == "files":
        if cancel and job.kind == "identify":
            forgotten = await forget_run(db, job)
            return f"Cancelled: what it found for {forgotten} files was forgotten."
        return "Stopped."
    us = await db.get(UserServer, job.user_server_id)
    return await artwork_jobs.wind_up(
        db, http, settings, job, us, JobStopped("cancel" if cancel else "stop")
    )
