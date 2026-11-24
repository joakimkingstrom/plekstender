"""Music files and their tags (server owners). Reading the files is the worker's job: these
routes start scans and show what they found, next to Plex's metadata."""

from __future__ import annotations

import asyncio
import time
from pathlib import PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, insert, or_, select

from plekstender import musicbrainz
from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.schemas import (
    AlbumFilesOut,
    FileScanOut,
    FilesSummaryOut,
    MbApplyIn,
    MbFieldChange,
    MbFileOut,
    MbPreviewOut,
    MbReleaseSummary,
    MbTrackOut,
    NfoOut,
    PlexSyncIn,
    PlexSyncPreviewOut,
    PlexSyncSample,
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
    NfoFile,
    PlexServer,
    Tag,
    TagChange,
    TagChangeSet,
    Track,
    User,
    UserServer,
)
from plekstender.tags.lyrics import FIELDS as LYRICS_FIELDS
from plekstender.tags.mbapply import AlbumFile, match, plan_file, wanted
from plekstender.tags.plexsync import Planned
from plekstender.tags.plexsync import plan as plan_sync
from plekstender.tags.read import unread_fields
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
            .where(FileScan.server_id == us.server_id, FileScan.kind == "scan")
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
        select(FileScan).where(
            FileScan.server_id == us.server_id,
            FileScan.kind == "scan",
            FileScan.status.in_(ACTIVE),
        )
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
    scanned = bool(
        await db.scalar(
            select(func.count()).where(FileScan.server_id == us.server_id, FileScan.kind == "scan")
        )
    )
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
            status.HTTP_403_FORBIDDEN,
            'Turn on "Allow changing files" first (Settings → Music files).',
        )
    return us, server


def _stored(media: MediaFile, field: str) -> Any:
    """What the scan found in the file for a field (as the writer compares it)."""
    return clean(field, (media.tags or {}).get(field))


LISTED = 200


def _shown(value: Any, field: str | None = None) -> Any:
    """A change's value for the history: a picture still to be made is described in words
    (once written, it's the picture's SHA-1); lyrics by their first line."""
    if field in LYRICS_FIELDS and isinstance(value, str):
        lines = [line for line in value.splitlines() if line.strip()]
        first = lines[0] if lines else ""
        return f"{len(lines)} {'line' if len(lines) == 1 else 'lines'}: {first[:80]}"
    if not isinstance(value, dict):
        return value
    origin = value.get("from")
    if origin == "plex":
        return "Plex's poster"
    if origin == "self":
        return f"its own cover, shrunk to {value.get('max_px')} px"
    return f"from {PurePosixPath(str(value.get('path', ''))).name}"


async def _sets_out(db: DbDep, sets: list[TagChangeSet]) -> list[TagChangeSetOut]:
    if not sets:
        return []
    ids = [s.id for s in sets]
    numbered = (
        select(
            TagChange.id,
            func.row_number()
            .over(partition_by=TagChange.change_set_id, order_by=TagChange.id)
            .label("n"),
        )
        .where(TagChange.change_set_id.in_(ids))
        .subquery()
    )
    changes = list(
        await db.scalars(
            select(TagChange)
            .join(numbered, numbered.c.id == TagChange.id)
            .where(numbered.c.n <= LISTED)
            .order_by(TagChange.id)
        )
    )
    counts: dict[int, dict[str, int]] = {}
    for set_id, status_, n in (
        await db.execute(
            select(TagChange.change_set_id, TagChange.status, func.count())
            .where(TagChange.change_set_id.in_(ids))
            .group_by(TagChange.change_set_id, TagChange.status)
        )
    ).all():
        counts.setdefault(set_id, {})[status_] = n
    undone_rows = await db.execute(
        select(TagChange.undoes_id, TagChange.change_set_id)
        .join(TagChangeSet, TagChangeSet.id == TagChange.change_set_id)
        .where(TagChange.status == "applied", TagChange.undoes_id.is_not(None))
        .where(
            TagChange.undoes_id.in_(select(TagChange.id).where(TagChange.change_set_id.in_(ids)))
        )
    )
    undone = {row[0] for row in undone_rows.all()}
    undone_per_set = (
        {
            set_id: n
            for set_id, n in (
                await db.execute(
                    select(TagChange.change_set_id, func.count())
                    .where(TagChange.change_set_id.in_(ids), TagChange.id.in_(undone))
                    .group_by(TagChange.change_set_id)
                )
            ).all()
        }
        if undone
        else {}
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
                old_value=_shown(c.old_value, c.field),
                new_value=_shown(c.new_value, c.field),
                status=c.status,
                error=c.error,
                undoes_id=c.undoes_id,
                undone=c.id in undone,
            )
        )
    out = []
    for s in sets:
        set_counts = counts.get(s.id, {})
        out.append(
            TagChangeSetOut(
                id=s.id,
                kind=s.kind,
                description=s.description,
                status=s.status,
                error=s.error,
                user=users.get(s.user_id) if s.user_id else None,
                created_at=s.created_at,
                applied_at=s.applied_at,
                total=sum(set_counts.values()),
                counts=set_counts,
                undoable=set_counts.get("applied", 0) - undone_per_set.get(s.id, 0),
                changes=by_set.get(s.id, []),
            )
        )
    return out


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
    if body.set_id is None and not body.change_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say what to undo.")
    which = (
        TagChange.change_set_id == body.set_id
        if body.set_id is not None
        else TagChange.id.in_(body.change_ids)
    )
    originals = list(
        await db.scalars(
            select(TagChange)
            .join(TagChangeSet, TagChangeSet.id == TagChange.change_set_id)
            .where(TagChangeSet.server_id == us.server_id, which, TagChange.status == "applied")
            .order_by(TagChange.id.desc())
        )
    )
    already = set(
        await db.scalars(
            select(TagChange.undoes_id).where(
                TagChange.undoes_id.in_(select(TagChange.id).where(which)),
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


# --- Plex's metadata into the files (in bulk) ------------------------------------------------


async def _plan(db: DbDep, us: UserServer, body: PlexSyncIn) -> list[Planned]:
    if body.scope != "library" and body.key is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Which artist or album?")
    return await plan_sync(db, us.server_id, body.scope, body.key, set(body.fields), us.user_id)


@router.post("/plex-sync/preview", response_model=PlexSyncPreviewOut)
async def plex_sync_preview(
    server_id: int, body: PlexSyncIn, db: DbDep, user: UserDep
) -> PlexSyncPreviewOut:
    """What writing Plex's metadata to the files would change (nothing is written)."""
    us = await owner_server(db, user, server_id)
    planned = await _plan(db, us, body)
    by_field: dict[str, int] = {}
    for p in planned:
        by_field[p.field] = by_field.get(p.field, 0) + 1
    return PlexSyncPreviewOut(
        files=len({p.media_file_id for p in planned}),
        changes=len(planned),
        by_field=by_field,
        samples=[
            PlexSyncSample(
                path=p.path,
                field=p.field,
                old_unknown=p.old_unknown,
                old_value=p.old_value,
                new_value=p.new_value,
            )
            for p in planned[:100]
        ],
    )


@router.post("/plex-sync", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def plex_sync(server_id: int, body: PlexSyncIn, db: DbDep, user: UserDep) -> TagChangeSetOut:
    """Write Plex's metadata to the files: recorded as one edit (undo it as a whole)."""
    us, server = await _editable(db, user, server_id)
    planned = await _plan(db, us, body)
    if not planned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The files already match Plex.")
    files = len({p.media_file_id for p in planned})
    where = {"library": "the library", "artist": "an artist", "album": "an album"}[body.scope]
    change_set = TagChangeSet(
        server_id=server.id,
        user_id=user.id,
        kind="edit",
        description=(body.description or "").strip()
        or f"Plex's metadata to {files} {'file' if files == 1 else 'files'} ({where})",
    )
    db.add(change_set)
    await db.flush()
    rows = [
        {
            "change_set_id": change_set.id,
            "media_file_id": p.media_file_id,
            "path": p.path,
            "rating_key": p.rating_key,
            "field": p.field,
            "old_value": p.old_value,
            "new_value": p.new_value,
            "status": "pending",
        }
        for p in planned
    ]
    for start in range(0, len(rows), 2000):
        await db.execute(insert(TagChange).values(rows[start : start + 2000]))
    await db.commit()
    await enqueue_tag_changes(change_set)
    [out] = await _sets_out(db, [change_set])
    return out


# --- MusicBrainz releases --------------------------------------------------------------------


def _mb_error(exc: musicbrainz.MusicBrainzError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.get("/musicbrainz/search", response_model=list[MbReleaseSummary])
async def musicbrainz_search(
    server_id: int,
    db: DbDep,
    user: UserDep,
    http: HttpDep,
    album: Annotated[str, Query(min_length=1, max_length=300)],
    artist: Annotated[str | None, Query(max_length=300)] = None,
) -> list[MbReleaseSummary]:
    """Releases on MusicBrainz by album title (and artist); a release link finds that one."""
    await owner_server(db, user, server_id)
    try:
        found = await musicbrainz.search(http, album, artist)
    except musicbrainz.MusicBrainzError as exc:
        raise _mb_error(exc) from exc
    return [MbReleaseSummary.model_validate(c, from_attributes=True) for c in found]


@router.get("/musicbrainz/cover/{release_id}")
async def musicbrainz_cover(
    server_id: int, release_id: str, db: DbDep, user: UserDep, http: HttpDep, settings: SettingsDep
) -> Response:
    """A release's front cover from the Cover Art Archive (kept under /data)."""
    await owner_server(db, user, server_id)
    if not musicbrainz.MBID.fullmatch(release_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    folder = settings.data_dir / "musicbrainz" / "covers"
    path, missing = folder / f"{release_id}.jpg", folder / f"{release_id}.none"
    headers = {"Cache-Control": "private, max-age=604800"}
    if await asyncio.to_thread(path.exists):
        return Response(
            await asyncio.to_thread(path.read_bytes), media_type="image/jpeg", headers=headers
        )
    if await asyncio.to_thread(
        lambda: missing.exists() and time.time() - missing.stat().st_mtime < 86_400
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    image = await musicbrainz.cover(http, release_id)
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    if image is None:
        await asyncio.to_thread(missing.touch)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    await asyncio.to_thread(path.write_bytes, image)
    return Response(image, media_type="image/jpeg", headers=headers)


async def _album_files(db: DbDep, server_id: int, album_key: int) -> list[AlbumFile]:
    rows = await db.scalars(
        select(MediaFile)
        .join(
            Track,
            (Track.server_id == MediaFile.server_id) & (Track.rating_key == MediaFile.rating_key),
        )
        .where(
            MediaFile.server_id == server_id,
            Track.album_rating_key == album_key,
            MediaFile.error.is_(None),
        )
        .order_by(MediaFile.path)
    )
    return [
        AlbumFile(
            m.id,
            m.path,
            m.rating_key,
            m.tags or {},
            m.duration_ms,
            _recordings(m),
            unread_fields(m.reader),
        )
        for m in rows
    ]


def _recordings(media: MediaFile) -> frozenset[str]:
    """The recordings a file sounds like (identified by sound, while the file is unchanged)."""
    identity = media.identity or {}
    if identity.get("key") != f"{media.size}:{media.mtime}":
        return frozenset()
    return frozenset(
        rec["id"] for result in identity.get("results") or [] for rec in result["recordings"]
    )


async def _mb_plan(
    db: DbDep, http: HttpDep, server_id: int, album_key: int, body: MbApplyIn
) -> tuple[musicbrainz.Release, list[AlbumFile], dict[int, str | None], list[Planned]]:
    files = await _album_files(db, server_id, album_key)
    if not files:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No scanned files for this album (scan the folder first)."
        )
    try:
        release = await musicbrainz.release(http, body.release_id)
    except musicbrainz.MusicBrainzError as exc:
        raise _mb_error(exc) from exc
    tracks = {t.id: t for t in release.tracks}
    if body.mapping is None:
        mapping = match(files, release)
    else:
        mapping = {f.id: body.mapping.get(f.id) for f in files}
        picked = [t for t in mapping.values() if t is not None]
        if any(t not in tracks for t in picked):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown track")
        if len(picked) != len(set(picked)):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "A track can only be matched to one file."
            )
    fields = set(body.fields)
    planned = []
    for f in files:
        track_id = mapping.get(f.id)
        if track_id is None:
            continue
        for field, new in plan_file(
            f.tags, wanted(release, tracks[track_id]), set[str](fields)
        ).items():
            planned.append(
                Planned(
                    f.id,
                    f.path,
                    f.rating_key or 0,
                    field,
                    clean(field, f.tags.get(field)),
                    new,
                    field in f.unknown,
                )
            )
    return release, files, mapping, planned


@router.post("/albums/{album_key}/musicbrainz/preview", response_model=MbPreviewOut)
async def musicbrainz_preview(
    server_id: int, album_key: int, body: MbApplyIn, db: DbDep, user: UserDep, http: HttpDep
) -> MbPreviewOut:
    """A release's tracks, which file is which (matched automatically unless given), and the
    tags that would change. Nothing is written."""
    us = await owner_server(db, user, server_id)
    release, files, mapping, planned = await _mb_plan(db, http, us.server_id, album_key, body)
    per_file: dict[int, list[MbFieldChange]] = {}
    for p in planned:
        per_file.setdefault(p.media_file_id, []).append(
            MbFieldChange(
                field=p.field,
                old_unknown=p.old_unknown,
                old_value=p.old_value,
                new_value=p.new_value,
            )
        )
    return MbPreviewOut(
        release=MbReleaseSummary.model_validate(release.summary, from_attributes=True),
        tracks=[MbTrackOut.model_validate(t, from_attributes=True) for t in release.tracks],
        files=[
            MbFileOut(
                file_id=f.id,
                path=f.path,
                disc=f.tags.get("disc"),
                track=f.tags.get("track"),
                title=f.tags.get("title"),
                duration_ms=f.duration_ms,
                track_id=mapping.get(f.id),
                changes=per_file.get(f.id, []),
            )
            for f in files
        ],
        changes=len(planned),
    )


@router.post(
    "/albums/{album_key}/musicbrainz",
    response_model=TagChangeSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def musicbrainz_apply(
    server_id: int, album_key: int, body: MbApplyIn, db: DbDep, user: UserDep, http: HttpDep
) -> TagChangeSetOut:
    """Write a release's tags to the album's files, as one edit (undo it from the history)."""
    us, server = await _editable(db, user, server_id)
    release, _files, _mapping, planned = await _mb_plan(db, http, us.server_id, album_key, body)
    if not planned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The files already match this release.")
    count = len({p.media_file_id for p in planned})
    about = ", ".join(
        x for x in (release.date[:4] if release.date else None, release.country,
                    release.summary.formats) if x
    )  # fmt: skip
    description = f"MusicBrainz release {release.title}" + (f" ({about})" if about else "")
    description = f"{description[:170]} to {count} {'file' if count == 1 else 'files'}"
    changes = [
        TagChange(
            media_file_id=p.media_file_id,
            path=p.path,
            rating_key=p.rating_key or None,
            field=p.field,
            old_value=p.old_value,
            new_value=p.new_value,
        )
        for p in planned
    ]
    return await _queue(
        db,
        TagChangeSet(server_id=server.id, user_id=user.id, kind="edit", description=description),
        changes,
    )


# --- .nfo files ---------------------------------------------------------------------------------


def _folders(paths: list[str]) -> set[str]:
    return {str(PurePosixPath(p).parent) for p in paths}


async def _nfo(db: DbDep, server_id: int, kind: str, folders: list[str]) -> NfoFile | None:
    """The .nfo of this kind in the first of these folders that has one."""
    if not folders:
        return None
    name = f"{kind}.nfo"
    wanted = [str(PurePosixPath(f) / name).lower() for f in folders]
    found = {
        n.path.lower(): n
        for n in await db.scalars(
            select(NfoFile).where(
                NfoFile.server_id == server_id,
                NfoFile.kind == kind,
                func.lower(NfoFile.path).in_(wanted),
            )
        )
    }
    return next((found[w] for w in wanted if w in found), None)


@router.get("/albums/{album_key}/nfo", response_model=NfoOut | None)
async def album_nfo(server_id: int, album_key: int, db: DbDep, user: UserDep) -> NfoOut | None:
    """The album.nfo in the album's folder (or the folder above its disc folders)."""
    us = await owner_server(db, user, server_id)
    files = await _album_files(db, us.server_id, album_key)
    folders = sorted(_folders([f.path for f in files]))
    parents = sorted({str(PurePosixPath(f).parent) for f in folders} - {"."} - set(folders))
    nfo = await _nfo(db, us.server_id, "album", folders + parents)
    if nfo is None:
        return None
    releases = sorted(
        {
            str(f.tags["musicbrainz_albumid"]).lower()
            for f in files
            if f.tags.get("musicbrainz_albumid")
        }
    )
    return NfoOut(
        path=nfo.path, kind="album", data=nfo.data, error=nfo.error, file_release_ids=releases
    )


@router.get("/artists/{artist_key}/nfo", response_model=NfoOut | None)
async def artist_nfo(server_id: int, artist_key: int, db: DbDep, user: UserDep) -> NfoOut | None:
    """The artist.nfo in the folder above the artist's albums (the closest one to most of
    their files)."""
    us = await owner_server(db, user, server_id)
    artist = await db.get(Artist, (us.server_id, artist_key))
    if artist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artist not found")
    paths = list(
        await db.scalars(
            select(MediaFile.path)
            .join(
                Track,
                (Track.server_id == MediaFile.server_id)
                & (Track.rating_key == MediaFile.rating_key),
            )
            .outerjoin(
                Album,
                (Album.server_id == Track.server_id) & (Album.rating_key == Track.album_rating_key),
            )
            .where(
                MediaFile.server_id == us.server_id,
                or_(Track.artist_rating_key == artist_key, Album.artist_rating_key == artist_key),
            )
            .limit(2000)
        )
    )
    counts: dict[str, int] = {}
    for path in paths:
        for parent in PurePosixPath(path).parents:
            if str(parent) != ".":
                counts[str(parent)] = counts.get(str(parent), 0) + 1
    # Most files first, then the deepest (the artist's own folder, not a genre folder).
    folders = sorted(counts, key=lambda f: (-counts[f], -len(PurePosixPath(f).parts)))
    nfo = await _nfo(db, us.server_id, "artist", folders)
    if nfo is None:
        return None
    return NfoOut(
        path=nfo.path, kind="artist", data=nfo.data, error=nfo.error, plex_mbid=artist.mbid
    )
