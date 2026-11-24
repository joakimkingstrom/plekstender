"""Lyrics for music files (server owners): looked up on LRCLIB by the worker, previewed,
then written as one edit (undo takes them out again). See plekstender.tags.lyrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.routes.files import _editable, _scan_out, _sets_out
from plekstender.api.schemas import (
    FileScanOut,
    LyricsApplyIn,
    LyricsLookupIn,
    LyricsPlacement,
    LyricsPlanOut,
    LyricsSampleOut,
    TagChangeSetOut,
)
from plekstender.db.models import FileScan, PlexServer, TagChangeSet
from plekstender.tags import lyrics
from plekstender.worker import enqueue_lyrics_lookup, enqueue_tag_changes

router = APIRouter(prefix="/api/servers/{server_id}/files/lyrics", tags=["files"])


@router.post("", response_model=FileScanOut, status_code=status.HTTP_201_CREATED)
async def look_up_lyrics(
    server_id: int, body: LyricsLookupIn, db: DbDep, user: UserDep
) -> FileScanOut:
    """Look up lyrics (in the worker) for the library or some albums. Nothing is written."""
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    if server is None or not server.local_path_prefix:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Set up the music folder first (Settings → Music files)."
        )
    job = FileScan(
        server_id=us.server_id,
        kind="lyrics",
        params={"albums": sorted(set(body.albums)), "again": body.again},
        status="queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await enqueue_lyrics_lookup(job)
    out = _scan_out(job)
    assert out is not None
    return out


async def _job(db: DbDep, server_id: int, job_id: int) -> FileScan:
    job = await db.get(FileScan, job_id)
    if job is None or job.server_id != server_id or job.kind != "lyrics":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return job


@router.get("/{job_id}", response_model=LyricsPlanOut)
async def lyrics_plan(
    server_id: int,
    job_id: int,
    db: DbDep,
    user: UserDep,
    placement: Annotated[LyricsPlacement, Query()] = "sidecar",
    overwrite: bool = False,
) -> LyricsPlanOut:
    """What's been found so far, and what writing it would change."""
    us = await owner_server(db, user, server_id)
    job = await _job(db, us.server_id, job_id)
    counts, sample = await lyrics.summary(
        db, us.server_id, list(job.params.get("albums") or []), placement, overwrite
    )
    out = _scan_out(job)
    assert out is not None
    return LyricsPlanOut(
        job=out,
        **counts,
        sample=[LyricsSampleOut(**row._asdict()) for row in sample],
        applied_set_id=job.params.get("applied_set_id"),
    )


@router.post("/{job_id}/apply", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def write_lyrics(
    server_id: int, job_id: int, body: LyricsApplyIn, db: DbDep, user: UserDep
) -> TagChangeSetOut:
    """Write the lyrics found, as one edit."""
    us, server = await _editable(db, user, server_id)
    job = await _job(db, us.server_id, job_id)
    if job.status in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "The lyrics are still being looked up.")
    if job.params.get("applied_set_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "These lyrics were written already.")
    albums = list(job.params.get("albums") or [])
    change_set = TagChangeSet(
        server_id=server.id, user_id=user.id, kind="edit", description="Lyrics"
    )
    db.add(change_set)
    await db.flush()
    added = await lyrics.add_changes(
        db, change_set.id, server.id, albums, body.placement, body.overwrite
    )
    if not added:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to write.")
    where = {"sidecar": "next to", "embedded": "into", "both": "next to and into"}[body.placement]
    scope = f"{len(albums)} {'album' if len(albums) == 1 else 'albums'}" if albums else "library"
    change_set.description = f"Lyrics from LRCLIB written {where} the songs ({scope})"
    job.params = {**job.params, "applied_set_id": change_set.id}
    await db.commit()
    await enqueue_tag_changes(change_set)
    [out] = await _sets_out(db, [change_set])
    return out
