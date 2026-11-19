"""Renaming and moving music files from a template (server owners): a dry run in the worker,
then the moves as one edit (undo moves them back)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, status

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.routes.files import _editable, _queue, _scan_out
from plekstender.api.schemas import (
    FileScanOut,
    RenameIn,
    RenameMove,
    RenamePlanOut,
    RenameSkip,
    TagChangeSetOut,
)
from plekstender.db.models import FileScan, TagChange, TagChangeSet
from plekstender.tags.rename import TemplateError, check_template
from plekstender.worker import enqueue_rename_plan

router = APIRouter(prefix="/api/servers/{server_id}/files/renames", tags=["files"])
# A dry run older than this is checked again before moving anything.
FRESH_FOR = dt.timedelta(minutes=30)


@router.post("", response_model=FileScanOut, status_code=status.HTTP_201_CREATED)
async def plan_renames(server_id: int, body: RenameIn, db: DbDep, user: UserDep) -> FileScanOut:
    """Work out (in the worker) where an album's or an artist's files would go. Nothing moves."""
    us = await owner_server(db, user, server_id)
    try:
        template = check_template(body.template)
    except TemplateError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    job = FileScan(
        server_id=us.server_id,
        kind="rename",
        params={"scope": body.scope, "key": body.key, "template": template},
        status="queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await enqueue_rename_plan(job)
    out = _scan_out(job)
    assert out is not None
    return out


async def _job(db: DbDep, server_id: int, job_id: int) -> FileScan:
    job = await db.get(FileScan, job_id)
    if job is None or job.server_id != server_id or job.kind != "rename":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return job


@router.get("/{job_id}", response_model=RenamePlanOut)
async def rename_plan(server_id: int, job_id: int, db: DbDep, user: UserDep) -> RenamePlanOut:
    us = await owner_server(db, user, server_id)
    job = await _job(db, us.server_id, job_id)
    stats = job.stats or {}
    out = _scan_out(job)
    assert out is not None
    out.stats = {}  # the plan is below
    return RenamePlanOut(
        job=out,
        template=stats.get("template"),
        songs=int(stats.get("songs", 0)),
        unchanged=int(stats.get("unchanged", 0)),
        moves=[
            RenameMove(kind=m["kind"], source=m["source"], target=m["target"])
            for m in stats.get("moves", [])
        ],
        skipped=[RenameSkip(**s) for s in stats.get("skipped", [])],
        applied_set_id=job.params.get("applied_set_id"),
    )


@router.post("/{job_id}/apply", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def apply_renames(server_id: int, job_id: int, db: DbDep, user: UserDep) -> TagChangeSetOut:
    """Make the dry run's moves, as one edit (undo it from the history to move them back)."""
    us, server = await _editable(db, user, server_id)
    job = await _job(db, us.server_id, job_id)
    if job.status != "succeeded":
        raise HTTPException(status.HTTP_409_CONFLICT, "The dry run hasn't finished.")
    if job.params.get("applied_set_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "These files were moved already.")
    if job.finished_at and dt.datetime.now(dt.UTC) - job.finished_at > FRESH_FOR:
        raise HTTPException(status.HTTP_409_CONFLICT, "This dry run is old: check again first.")
    moves = (job.stats or {}).get("moves", [])
    if not moves:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to move.")
    songs = sum(1 for m in moves if m["kind"] == "song")
    others = len(moves) - songs
    description = (
        f"Moved {songs} {'song' if songs == 1 else 'songs'}"
        + (f" and {others} other {'file' if others == 1 else 'files'}" if others else "")
        + f" to {job.stats['template']}"
    )
    out = await _queue(
        db,
        TagChangeSet(
            server_id=server.id, user_id=user.id, kind="edit", description=description[:300]
        ),
        [
            TagChange(
                media_file_id=m.get("file_id"),
                path=m["source"],
                rating_key=m.get("rating_key"),
                field="path",
                old_value=m["source"],
                new_value=m["target"],
            )
            for m in moves
        ],
    )
    job = await _job(db, us.server_id, job_id)
    job.params = {**job.params, "applied_set_id": out.id}
    await db.commit()
    return out
