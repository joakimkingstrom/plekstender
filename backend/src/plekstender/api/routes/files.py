"""Music files and their tags (server owners). Reading the files is the worker's job: these
routes start scans and show what they found, next to Plex's metadata."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.schemas import (
    AlbumFilesOut,
    FileScanOut,
    FilesSummaryOut,
    TagChangeOut,
    TagChangeSetOut,
    TagEditIn,
    TagSettingsIn,
    TagUndoIn,
    TrackFileOut,
)
from plekstender.db.models import (
    Album,
    Artist,
    FileScan,
    ItemTag,
    MediaFile,
    PlexServer,
    Tag,
    TagChange,
    TagChangeSet,
    Track,
    User,
    UserServer,
)
from plekstender.tags.write import clean
from plekstender.worker import enqueue_file_scan, enqueue_tag_changes

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
        tag_editing=server.tag_editing,
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
                file_id=media.id if media else None,
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


# --- editing tags ---------------------------------------------------------------------------


@router.put("/settings", response_model=FilesSummaryOut)
async def files_settings(
    server_id: int, body: TagSettingsIn, db: DbDep, user: UserDep
) -> FilesSummaryOut:
    """Allow (or stop) editing tags: it writes to the music files."""
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    assert server is not None
    server.tag_editing = body.tag_editing
    await db.commit()
    return await files_summary(server_id, db, user)


async def _editable(db: DbDep, user: User, server_id: int) -> tuple[UserServer, PlexServer]:
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    assert server is not None
    if not server.tag_editing:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Turn on tag editing first (Settings → Music files)."
        )
    return us, server


def _stored(media: MediaFile, field: str) -> Any:
    """What the scan found in the file for a field (as the writer compares it)."""
    return clean(field, (media.tags or {}).get(field))


async def _sets_out(db: DbDep, sets: list[TagChangeSet]) -> list[TagChangeSetOut]:
    if not sets:
        return []
    ids = [s.id for s in sets]
    changes = list(
        await db.scalars(
            select(TagChange).where(TagChange.change_set_id.in_(ids)).order_by(TagChange.id)
        )
    )
    undone = set(
        await db.scalars(
            select(TagChange.undoes_id).where(
                TagChange.undoes_id.in_([c.id for c in changes]), TagChange.status == "applied"
            )
        )
    )
    users = {
        u.id: u.username
        for u in await db.scalars(
            select(User).where(User.id.in_({s.user_id for s in sets if s.user_id}))
        )
    }
    by_set: dict[int, list[TagChangeOut]] = {}
    for c in changes:
        by_set.setdefault(c.change_set_id, []).append(
            TagChangeOut(
                id=c.id,
                file_id=c.media_file_id,
                path=c.path,
                rating_key=c.rating_key,
                field=c.field,
                old_value=c.old_value,
                new_value=c.new_value,
                status=c.status,
                error=c.error,
                undoes_id=c.undoes_id,
                undone=c.id in undone,
            )
        )
    return [
        TagChangeSetOut(
            id=s.id,
            kind=s.kind,
            description=s.description,
            status=s.status,
            error=s.error,
            user=users.get(s.user_id) if s.user_id else None,
            created_at=s.created_at,
            applied_at=s.applied_at,
            changes=by_set.get(s.id, []),
        )
        for s in sets
    ]


async def _queue(db: DbDep, change_set: TagChangeSet, changes: list[TagChange]) -> TagChangeSetOut:
    db.add(change_set)
    await db.flush()
    for change in changes:
        change.change_set_id = change_set.id
        db.add(change)
    await db.commit()
    await enqueue_tag_changes(change_set)
    [out] = await _sets_out(db, [change_set])
    return out


@router.post("/edits", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def edit_tags(server_id: int, body: TagEditIn, db: DbDep, user: UserDep) -> TagChangeSetOut:
    """Change tags in music files. The changes are recorded first, then the worker writes
    them; a file that changed since it was scanned isn't overwritten (a conflict)."""
    us, server = await _editable(db, user, server_id)
    files = {
        m.id: m
        for m in await db.scalars(
            select(MediaFile).where(
                MediaFile.server_id == us.server_id,
                MediaFile.id.in_({c.file_id for c in body.changes}),
            )
        )
    }
    changes = []
    seen: set[tuple[int, str]] = set()
    for c in body.changes:
        media = files.get(c.file_id)
        if media is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        if (c.file_id, c.field) in seen:
            continue
        seen.add((c.file_id, c.field))
        old, new = _stored(media, c.field), clean(c.field, c.value)
        if old == new:
            continue
        changes.append(
            TagChange(
                media_file_id=media.id,
                path=media.path,
                rating_key=media.rating_key,
                field=c.field,
                old_value=old,
                new_value=new,
            )
        )
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to change.")
    files_changed = len({c.media_file_id for c in changes})
    description = (body.description or "").strip() or (
        f"Edited {len(changes)} {'tag' if len(changes) == 1 else 'tags'} in "
        f"{files_changed} {'file' if files_changed == 1 else 'files'}"
    )
    return await _queue(
        db,
        TagChangeSet(server_id=server.id, user_id=user.id, kind="edit", description=description),
        changes,
    )


@router.post("/undo", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def undo_tags(server_id: int, body: TagUndoIn, db: DbDep, user: UserDep) -> TagChangeSetOut:
    """Put back the values from before the given changes (any of them: a whole edit, or
    picked ones). An undo is an edit too: it's recorded, and can itself be undone."""
    us, server = await _editable(db, user, server_id)
    originals = list(
        await db.scalars(
            select(TagChange)
            .join(TagChangeSet, TagChangeSet.id == TagChange.change_set_id)
            .where(
                TagChangeSet.server_id == us.server_id,
                TagChange.id.in_(body.change_ids),
                TagChange.status == "applied",
            )
            .order_by(TagChange.id.desc())
        )
    )
    already = set(
        await db.scalars(
            select(TagChange.undoes_id).where(
                TagChange.undoes_id.in_([c.id for c in originals]),
                TagChange.status.in_(("pending", "applied")),
            )
        )
    )
    changes = [
        TagChange(
            media_file_id=c.media_file_id,
            path=c.path,
            rating_key=c.rating_key,
            field=c.field,
            old_value=c.new_value,  # what the file should hold now
            new_value=c.old_value,
            undoes_id=c.id,
        )
        for c in originals
        if c.id not in already
    ]
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to undo.")
    description = f"Undid {len(changes)} {'change' if len(changes) == 1 else 'changes'}"
    return await _queue(
        db,
        TagChangeSet(server_id=server.id, user_id=user.id, kind="undo", description=description),
        changes,
    )


@router.get("/changes", response_model=list[TagChangeSetOut])
async def tag_history(
    server_id: int,
    db: DbDep,
    user: UserDep,
    album: int | None = None,
    artist: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TagChangeSetOut]:
    """Edits (newest first) that touched an album's or an artist's files, or any."""
    us = await owner_server(db, user, server_id)
    touched = select(TagChange.change_set_id)
    if album is not None or artist is not None:
        column = Track.album_rating_key if album is not None else Track.artist_rating_key
        keys = select(Track.rating_key).where(
            Track.server_id == us.server_id, column == (album if album is not None else artist)
        )
        touched = touched.where(TagChange.rating_key.in_(keys))
    sets = list(
        await db.scalars(
            select(TagChangeSet)
            .where(TagChangeSet.server_id == us.server_id, TagChangeSet.id.in_(touched))
            .order_by(TagChangeSet.id.desc())
            .limit(limit)
        )
    )
    return await _sets_out(db, sets)
