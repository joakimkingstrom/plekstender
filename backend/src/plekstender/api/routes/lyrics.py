"""Lyrics for music files (server owners): how many songs have which kind, looked up on
LRCLIB by the worker, previewed, then written as one edit (undo takes them out again). See
plekstender.tags.lyrics."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import case, func, select

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.routes.files import _editable, _scan_out, _sets_out
from plekstender.api.schemas import (
    FileScanOut,
    LyricsApplyIn,
    LyricsCoverageOut,
    LyricsLookupIn,
    LyricsPlanOut,
    LyricsSampleOut,
    LyricsStopIn,
    TagChangeSetOut,
)
from plekstender.db.models import FileScan, MediaFile, PlexServer, TagChangeSet
from plekstender.jobs import abandoned
from plekstender.tags import lyrics
from plekstender.tags.read import LYRICS_READER
from plekstender.worker import enqueue_lyrics_lookup, enqueue_tag_changes

router = APIRouter(prefix="/api/servers/{server_id}/files/lyrics", tags=["files"])


@router.get("/coverage", response_model=LyricsCoverageOut)
async def lyrics_coverage(server_id: int, db: DbDep, user: UserDep) -> LyricsCoverageOut:
    """How many of the music folder's songs have which kind of lyrics (as of the last scan
    or lyrics edit)."""
    us = await owner_server(db, user, server_id)
    unchecked = MediaFile.reader < LYRICS_READER
    kind = case((unchecked, "unchecked"), else_=func.coalesce(MediaFile.lyrics, "none"))
    rows = await db.execute(
        select(kind, func.count())
        .where(MediaFile.server_id == us.server_id, MediaFile.error.is_(None))
        .group_by(kind)
    )
    counts: dict[str, int] = {str(k): int(n) for k, n in rows.all()}
    return LyricsCoverageOut(
        files=sum(counts.values()),
        word=counts.get("word", 0),
        synced=counts.get("synced", 0),
        plain=counts.get("plain", 0),
        instrumental=counts.get("instrumental", 0),
        none=counts.get("none", 0),
        unchecked=counts.get("unchecked", 0),
    )


@router.post("", response_model=FileScanOut, status_code=status.HTTP_201_CREATED)
async def look_up_lyrics(
    server_id: int, body: LyricsLookupIn, db: DbDep, user: UserDep
) -> FileScanOut:
    """Look up lyrics (in the worker) for the library or some albums. Nothing is written,
    unless `write` says where: then lyrics are written as they're found."""
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    if server is None or not server.local_path_prefix:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Set up the music folder first (Settings → Music files)."
        )
    params: dict[str, object] = {"albums": sorted(set(body.albums)), "again": body.again}
    if body.write is not None:
        await _editable(db, user, server_id)
        if not (body.write.sidecar or body.write.lyricsfile or body.write.embedded):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose where to write them."
            )
        params |= {"write": body.write.model_dump(), "user_id": user.id}
    job = FileScan(server_id=us.server_id, kind="lyrics", params=params, status="queued")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await enqueue_lyrics_lookup(job)
    out = _scan_out(job)
    assert out is not None
    return out


@router.get("/latest", response_model=FileScanOut | None)
async def latest_lookup(
    server_id: int, db: DbDep, user: UserDep, scope: Literal["library", "any"] = "library"
) -> FileScanOut | None:
    """The last lyrics lookup for the whole library (or for anything: `scope=any`), if
    there's been one."""
    us = await owner_server(db, user, server_id)
    jobs = await db.scalars(
        select(FileScan)
        .where(FileScan.server_id == us.server_id, FileScan.kind == "lyrics")
        .order_by(FileScan.id.desc())
        .limit(20)
    )
    return _scan_out(next((j for j in jobs if scope == "any" or not j.params.get("albums")), None))


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
    sidecar: bool = True,
    lyricsfile: bool = False,
    embedded: bool = False,
    overwrite: bool = False,
) -> LyricsPlanOut:
    """What's been found so far, and what writing it (to these places) would change."""
    us = await owner_server(db, user, server_id)
    job = await _job(db, us.server_id, job_id)
    counts, sample = await lyrics.summary(
        db,
        us.server_id,
        list(job.params.get("albums") or []),
        lyrics.Destinations(sidecar, lyricsfile, embedded),
        overwrite,
    )
    out = _scan_out(job)
    assert out is not None
    return LyricsPlanOut(
        job=out,
        **counts,
        sample=[LyricsSampleOut(**row._asdict()) for row in sample],
        applied_set_id=job.params.get("applied_set_id"),
    )


@router.post("/{job_id}/stop", response_model=FileScanOut)
async def stop_lookup(
    server_id: int, job_id: int, body: LyricsStopIn, db: DbDep, user: UserDep
) -> FileScanOut:
    """Stop a lookup (what it found and wrote is kept), or cancel it: what it found is thrown
    away, and with `undo` what it wrote is taken out again (an undo edit)."""
    us = await owner_server(db, user, server_id)
    job = await _job(db, us.server_id, job_id)
    if job.status not in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "It isn't running.")
    request = "undo" if body.cancel and body.undo else "cancel" if body.cancel else "stop"
    # A running lookup saves its progress at least every couple of minutes; one that hasn't
    # for a while has no worker (it stopped): stopping it is done here instead.
    if job.status == "running" and not abandoned(job):
        job.params = {**job.params, "stop": request}  # the worker stops before the next song
        await db.commit()
    else:
        job.status = "failed"
        job.error = lyrics.STOP_MESSAGES[request]
        job.finished_at = job.updated_at = dt.datetime.now(dt.UTC)
        if request != "stop":
            await lyrics.discard(db, job)
        await db.commit()
        set_id = job.params.get("applied_set_id")
        if set_id and await lyrics.close_edit(db, job, set_id) and request == "undo":
            await lyrics.undo_edit(db, set_id, user.id)
    await db.refresh(job)
    out = _scan_out(job)
    assert out is not None
    return out


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
    to = lyrics.Destinations(body.sidecar, body.lyricsfile, body.embedded)
    if not (to.sidecar or to.lyricsfile or to.embedded):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose where to write them.")
    albums = list(job.params.get("albums") or [])
    change_set = TagChangeSet(
        server_id=server.id, user_id=user.id, kind="edit", description="Lyrics"
    )
    db.add(change_set)
    await db.flush()
    added = await lyrics.add_changes(db, change_set.id, server.id, albums, to, body.overwrite)
    if not added:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to write.")
    change_set.description = lyrics.describe(body.model_dump(), albums)
    job.params = {**job.params, "applied_set_id": change_set.id}
    await db.commit()
    await enqueue_tag_changes(change_set)
    [out] = await _sets_out(db, [change_set])
    return out
