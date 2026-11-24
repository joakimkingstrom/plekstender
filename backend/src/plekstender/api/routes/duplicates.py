"""Duplicate music files (server owners): found by a worker job (same recording, compared
by sound), then the extra copies moved out of the library as one edit (undo moves them
back). See plekstender.tags.duplicates."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.routes.files import _editable, _queue, _scan_out
from plekstender.api.schemas import (
    DupeGroupOut,
    DuplicatesApplyIn,
    DuplicatesOut,
    FileScanOut,
    TagChangeSetOut,
)
from plekstender.db.models import FileScan, PlexServer, TagChange, TagChangeSet
from plekstender.tags.duplicates import TRASH, DuplicatesError, moves_for
from plekstender.worker import enqueue_duplicates

router = APIRouter(prefix="/api/servers/{server_id}/files/duplicates", tags=["files"])
# A check older than this is done again before moving anything.
FRESH_FOR = dt.timedelta(days=1)


@router.post("", response_model=FileScanOut, status_code=status.HTTP_201_CREATED)
async def find_duplicates(server_id: int, db: DbDep, user: UserDep) -> FileScanOut:
    """Look for duplicate files (in the worker). Nothing moves."""
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    if server is None or not server.local_path_prefix:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Set up the music folder first (Settings → Music files)."
        )
    job = FileScan(server_id=us.server_id, kind="dupes", params={}, status="queued")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await enqueue_duplicates(job)
    out = _scan_out(job)
    assert out is not None
    out.stats = {}
    return out


async def _job(db: DbDep, server_id: int, job_id: int) -> FileScan:
    job = await db.get(FileScan, job_id)
    if job is None or job.server_id != server_id or job.kind != "dupes":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return job


@router.get("/latest", response_model=DuplicatesOut | None)
async def latest_duplicates(server_id: int, db: DbDep, user: UserDep) -> DuplicatesOut | None:
    """The last check, if there's been one."""
    us = await owner_server(db, user, server_id)
    job = await db.scalar(
        select(FileScan)
        .where(FileScan.server_id == us.server_id, FileScan.kind == "dupes")
        .order_by(FileScan.id.desc())
        .limit(1)
    )
    return None if job is None else _out(job)


@router.get("/{job_id}", response_model=DuplicatesOut)
async def duplicates(server_id: int, job_id: int, db: DbDep, user: UserDep) -> DuplicatesOut:
    us = await owner_server(db, user, server_id)
    return _out(await _job(db, us.server_id, job_id))


def _out(job: FileScan) -> DuplicatesOut:
    stats = job.stats or {}
    out = _scan_out(job)
    assert out is not None
    out.stats = {}  # the groups are below
    return DuplicatesOut(
        job=out,
        groups=[DupeGroupOut(**g) for g in stats.get("groups", [])],
        total_groups=int(stats.get("total_groups", 0)),
        files=int(stats.get("files", 0)),
        fingerprint_error=stats.get("fingerprint_error"),
        applied_set_id=job.params.get("applied_set_id"),
    )


@router.post("/{job_id}/apply", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def remove_duplicates(
    server_id: int, job_id: int, body: DuplicatesApplyIn, db: DbDep, user: UserDep
) -> TagChangeSetOut:
    """Move the chosen copies (and their lyrics files) out of the library, into a hidden
    folder in the music folder, as one edit."""
    us, server = await _editable(db, user, server_id)
    job = await _job(db, us.server_id, job_id)
    if job.status != "succeeded":
        raise HTTPException(status.HTTP_409_CONFLICT, "The check hasn't finished.")
    if job.params.get("applied_set_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "These duplicates were moved already.")
    if job.finished_at and dt.datetime.now(dt.UTC) - job.finished_at > FRESH_FOR:
        raise HTTPException(status.HTTP_409_CONFLICT, "This check is old: check again first.")
    try:
        moves = moves_for((job.stats or {}).get("groups", []), set(body.remove))
    except DuplicatesError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    songs = sum(1 for m in moves if m["file_id"] is not None)
    description = (
        f"Moved {songs} duplicate {'file' if songs == 1 else 'files'} out of the library "
        f"(into {TRASH})"
    )
    out = await _queue(
        db,
        TagChangeSet(server_id=server.id, user_id=user.id, kind="edit", description=description),
        [
            TagChange(
                media_file_id=m["file_id"],
                path=m["source"],
                rating_key=m["rating_key"],
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
