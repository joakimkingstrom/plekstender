"""Music files and their tags (server owners). Reading the files is the worker's job: these
routes start scans and show what they found, next to Plex's metadata."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.schemas import AlbumFilesOut, FileScanOut, FilesSummaryOut, TrackFileOut
from plekstender.db.models import (
    Album,
    Artist,
    FileScan,
    ItemTag,
    MediaFile,
    PlexServer,
    Tag,
    Track,
)
from plekstender.worker import enqueue_file_scan

router = APIRouter(prefix="/api/servers/{server_id}/files", tags=["files"])
ACTIVE = ("queued", "running")


def _scan_out(scan: FileScan | None) -> FileScanOut | None:
    return None if scan is None else FileScanOut.model_validate(scan, from_attributes=True)


@router.get("/summary", response_model=FilesSummaryOut)
async def files_summary(server_id: int, db: DbDep, user: UserDep) -> FilesSummaryOut:
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    assert server is not None
    counts = (
        await db.execute(
            select(
                func.count(),
                func.count(MediaFile.rating_key),
                func.count(MediaFile.error),
            ).where(MediaFile.server_id == us.server_id)
        )
    ).one()
    recent = list(
        await db.scalars(
            select(FileScan)
            .where(FileScan.server_id == us.server_id)
            .order_by(FileScan.id.desc())
            .limit(5)
        )
    )
    return FilesSummaryOut(
        mapped=bool(server.plex_path_prefix and server.local_path_prefix),
        files=counts[0],
        matched=counts[1],
        errors=counts[2],
        active=_scan_out(next((s for s in recent if s.status in ACTIVE), None)),
        last=_scan_out(next((s for s in recent if s.status not in ACTIVE), None)),
    )


@router.post("/scans", response_model=FileScanOut, status_code=status.HTTP_201_CREATED)
async def start_scan(server_id: int, db: DbDep, user: UserDep) -> FileScanOut:
    """Scan the music folder (in the worker): new and changed files' tags are read."""
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    assert server is not None
    if not (server.plex_path_prefix and server.local_path_prefix):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set up the music folder first.")
    active = await db.scalar(
        select(FileScan).where(FileScan.server_id == us.server_id, FileScan.status.in_(ACTIVE))
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A scan is already running.")
    scan = FileScan(server_id=us.server_id, status="queued")
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    await enqueue_file_scan(scan)
    out = _scan_out(scan)
    assert out is not None
    return out


def _clean(value: Any) -> str:
    return " ".join(str(value).split())


def _same(a: Any, b: Any) -> bool:
    """Exactly the same, ignoring only extra spaces (a tag editor cares about case and
    accents: "radiohead" isn't "Radiohead"). Genres compare as sets."""
    if isinstance(a, list) or isinstance(b, list):
        return {_clean(x) for x in a or []} == {_clean(x) for x in b or []}
    if a is None or b is None:
        return a is None and b is None
    return _clean(a) == _clean(b)


def differences(plex: dict[str, Any], tags: dict[str, Any]) -> list[str]:
    """Fields where the file and Plex disagree. A missing disc number in a one-disc album
    isn't a difference; years compare the year of the file's date."""
    out = []
    file_values = {**tags, "year": (tags.get("date") or "")[:4] or None}
    for field in ("title", "artist", "album", "albumartist", "track", "disc", "year", "genre"):
        mine, theirs = file_values.get(field), plex.get(field)
        if field == "disc" and mine is None and theirs in (None, 1):
            continue
        if field == "year" and theirs is not None:
            theirs = str(theirs)
        if field == "genre" and not theirs:
            continue  # Plex has no genre: nothing to compare with
        if not _same(mine, theirs):
            out.append(field)
    return out


@router.get("/albums/{album_key}", response_model=AlbumFilesOut)
async def album_files(server_id: int, album_key: int, db: DbDep, user: UserDep) -> AlbumFilesOut:
    """An album's songs: Plex's metadata next to the tags in their files."""
    us = await owner_server(db, user, server_id)
    album = await db.get(Album, (us.server_id, album_key))
    if album is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Album not found")
    album_artist = (
        await db.get(Artist, (us.server_id, album.artist_rating_key))
        if album.artist_rating_key
        else None
    )
    genres = list(
        await db.scalars(
            select(Tag.name)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(
                ItemTag.server_id == us.server_id,
                ItemTag.rating_key == album_key,
                Tag.kind == "genre",
            )
            .order_by(Tag.name)
        )
    )
    rows = (
        await db.execute(
            select(Track, Artist.title, MediaFile)
            .outerjoin(
                Artist,
                (Artist.server_id == Track.server_id)
                & (Artist.rating_key == Track.artist_rating_key),
            )
            .outerjoin(
                MediaFile,
                (MediaFile.server_id == Track.server_id)
                & (MediaFile.rating_key == Track.rating_key),
            )
            .where(Track.server_id == us.server_id, Track.album_rating_key == album_key)
            .order_by(Track.disc.nulls_last(), Track.number.nulls_last(), Track.rating_key)
        )
    ).all()
    tracks = []
    for track, artist_name, media in rows:
        plex = {
            "title": track.title,
            "artist": track.original_title or artist_name,
            "album": album.title,
            "albumartist": album_artist.title if album_artist else None,
            "track": track.number,
            "disc": track.disc,
            "year": album.year,
            "genre": genres,
        }
        tags = media.tags if media else {}
        tracks.append(
            TrackFileOut(
                rating_key=track.rating_key,
                disc=track.disc,
                number=track.number,
                plex=plex,
                file=media.path if media else None,
                format=media.format if media else None,
                duration_ms=media.duration_ms if media else None,
                has_cover=media.has_cover if media else False,
                tags=tags,
                error=media.error if media else None,
                differences=differences(plex, tags) if media and not media.error else [],
            )
        )
    scanned = bool(await db.scalar(select(func.count()).where(FileScan.server_id == us.server_id)))
    return AlbumFilesOut(scanned=scanned, tracks=tracks)
