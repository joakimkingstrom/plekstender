"""Identifying music files by sound (server owners): which files need it, starting the worker
job, the matches it found, and writing a chosen match's tags (previewed, as one edit)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from plekstender.api.deps import DbDep, SettingsDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.routes.files import ACTIVE, _editable, _queue, _scan_out
from plekstender.api.schemas import (
    FileScanOut,
    IdentifyApplyIn,
    IdentifyFileChanges,
    IdentifyIn,
    IdentifyPreviewOut,
    IdentifyStatusOut,
    IdentityChoice,
    MbFieldChange,
    MbReleaseSummary,
    TagChangeSetOut,
    UnknownFileOut,
    UnknownFilesOut,
    UnknownKind,
)
from plekstender.db.instance import get_acoustid_api_key
from plekstender.db.models import FileScan, MediaFile, TagChange, TagChangeSet, Track
from plekstender.tags.identify import file_key, find, suggest, values
from plekstender.tags.mbapply import plan_file
from plekstender.tags.read import unread_fields
from plekstender.tags.write import clean
from plekstender.worker import enqueue_identify

router = APIRouter(prefix="/api/servers/{server_id}/files", tags=["files"])
LISTED = 200
MAX_FILES = 5000


def _unknown(server_id: int, kind: UnknownKind) -> list[ColumnElement[bool]]:
    tag = MediaFile.tags
    conditions: list[ColumnElement[bool]] = [
        MediaFile.server_id == server_id,
        MediaFile.error.is_(None),
    ]
    if kind == "missing":
        conditions.append(
            or_(
                tag["title"].astext.is_(None),
                tag["artist"].astext.is_(None),
                tag["album"].astext.is_(None),
            )
        )
    elif kind == "unmatched":
        conditions.append(MediaFile.rating_key.is_(None))
    else:
        conditions.append(tag["musicbrainz_trackid"].astext.is_(None))
    return conditions


def _current(media: MediaFile) -> dict[str, Any] | None:
    """The file's identity, if it's still about the file as it is."""
    identity = media.identity
    return identity if identity and identity.get("key") == file_key(media) else None


def _status(identity: dict[str, Any] | None) -> Literal["new", "identified", "no_match", "error"]:
    if identity is None or ("results" not in identity and not identity.get("error")):
        return "new"
    if identity.get("error"):
        return "error"
    return "identified" if identity["results"] else "no_match"


def _popular(files: list[MediaFile]) -> dict[str, int]:
    """How many of these files each release has (to suggest one release for an album)."""
    counts: dict[str, int] = {}
    for media in files:
        seen = {
            rel["id"]
            for result in (_current(media) or {}).get("results") or []
            for rec in result["recordings"]
            for rel in rec["releases"]
        }
        for release_id in seen:
            counts[release_id] = counts.get(release_id, 0) + 1
    return counts


@router.get("/unknown", response_model=UnknownFilesOut)
async def unknown_files(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: Annotated[UnknownKind, Query()] = "missing",
) -> UnknownFilesOut:
    """Files missing tags, not in Plex, or without a MusicBrainz ID, with what identifying them
    by sound found."""
    us = await owner_server(db, user, server_id)
    conditions = _unknown(us.server_id, kind)
    total = await db.scalar(select(func.count()).select_from(MediaFile).where(*conditions)) or 0
    files = list(
        await db.scalars(
            select(MediaFile).where(*conditions).order_by(MediaFile.path).limit(LISTED)
        )
    )
    popular = _popular(files)
    out = []
    for media in files:
        identity = _current(media)
        tags = media.tags or {}
        picked = suggest(identity, tags, popular)
        out.append(
            UnknownFileOut(
                file_id=media.id,
                path=media.path,
                in_plex=media.rating_key is not None,
                duration_ms=media.duration_ms,
                tags={k: tags[k] for k in ("title", "artist", "album", "track") if k in tags},
                status=_status(identity),
                error=(identity or {}).get("error"),
                results=(identity or {}).get("results") or [],
                suggestion=IdentityChoice(
                    file_id=media.id, recording_id=picked[0], track_id=picked[2]
                )
                if picked
                else None,
            )
        )
    return UnknownFilesOut(total=total, files=out)


async def _identify_status(db: DbDep, server_id: int, configured: bool) -> IdentifyStatusOut:
    recent = list(
        await db.scalars(
            select(FileScan)
            .where(FileScan.server_id == server_id, FileScan.kind == "identify")
            .order_by(FileScan.id.desc())
            .limit(5)
        )
    )
    return IdentifyStatusOut(
        configured=configured,
        active=_scan_out(next((j for j in recent if j.status in ACTIVE), None)),
        last=_scan_out(next((j for j in recent if j.status not in ACTIVE), None)),
    )


@router.get("/identify", response_model=IdentifyStatusOut)
async def identify_status(
    server_id: int, db: DbDep, user: UserDep, settings: SettingsDep
) -> IdentifyStatusOut:
    us = await owner_server(db, user, server_id)
    key, _ = await get_acoustid_api_key(db, settings)
    return await _identify_status(db, us.server_id, key is not None)


@router.post("/identify", response_model=FileScanOut, status_code=status.HTTP_201_CREATED)
async def start_identify(
    server_id: int, body: IdentifyIn, db: DbDep, user: UserDep, settings: SettingsDep
) -> FileScanOut:
    """Identify files by sound (in the worker): a kind of unknown file, picked files, or an
    album's. Files identified before are only looked up again when asked."""
    us = await owner_server(db, user, server_id)
    key, _ = await get_acoustid_api_key(db, settings)
    if key is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Add an AcoustID application key first (Settings → Connections).",
        )
    query = select(MediaFile).where(MediaFile.server_id == us.server_id)
    if body.kind is not None:
        query = query.where(*_unknown(us.server_id, body.kind))
    elif body.album_key is not None:
        query = query.join(
            Track,
            (Track.server_id == MediaFile.server_id) & (Track.rating_key == MediaFile.rating_key),
        ).where(Track.album_rating_key == body.album_key, MediaFile.error.is_(None))
    elif body.file_ids:
        query = query.where(MediaFile.id.in_(body.file_ids))
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Which files?")
    files = list(await db.scalars(query.order_by(MediaFile.path).limit(MAX_FILES * 4)))
    ids = [f.id for f in files if body.again or _status(_current(f)) in ("new", "error")]
    if not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "These files have been identified.")
    active = await db.scalar(
        select(FileScan).where(
            FileScan.server_id == us.server_id,
            FileScan.kind == "identify",
            FileScan.status.in_(ACTIVE),
        )
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Files are being identified already.")
    job = FileScan(
        server_id=us.server_id,
        kind="identify",
        params={"file_ids": ids[:MAX_FILES], "again": body.again},
        status="queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await enqueue_identify(job)
    out = _scan_out(job)
    assert out is not None
    return out


async def _plan_identity(
    db: DbDep, server_id: int, body: IdentifyApplyIn
) -> list[tuple[MediaFile, dict[str, Any]]]:
    files = {
        m.id: m
        for m in await db.scalars(
            select(MediaFile).where(
                MediaFile.server_id == server_id,
                MediaFile.id.in_({c.file_id for c in body.choices}),
            )
        )
    }
    fields = set[str](body.fields)
    out = []
    for choice in body.choices:
        media = files.get(choice.file_id)
        if media is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        found = find(_current(media), choice.recording_id, choice.track_id)
        if found is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{media.path}: that isn't one of its matches (identify it again?).",
            )
        changes = plan_file(media.tags or {}, values(*found), fields)
        if changes:
            out.append((media, changes))
    return out


@router.post("/identify/preview", response_model=IdentifyPreviewOut)
async def identify_preview(
    server_id: int, body: IdentifyApplyIn, db: DbDep, user: UserDep
) -> IdentifyPreviewOut:
    """The tags the chosen matches would change (nothing is written)."""
    us = await owner_server(db, user, server_id)
    planned = await _plan_identity(db, us.server_id, body)
    return IdentifyPreviewOut(
        files=[
            IdentifyFileChanges(
                file_id=media.id,
                path=media.path,
                changes=[
                    MbFieldChange(
                        field=f,
                        old_unknown=f in unread_fields(media.reader),
                        old_value=clean(f, (media.tags or {}).get(f)),
                        new_value=v,
                    )
                    for f, v in changes.items()
                ],
            )
            for media, changes in planned
        ],
        changes=sum(len(c) for _, c in planned),
    )


@router.post("/identify/apply", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def identify_apply(
    server_id: int, body: IdentifyApplyIn, db: DbDep, user: UserDep
) -> TagChangeSetOut:
    """Write the chosen matches' tags, as one edit (undo it from the history)."""
    us, server = await _editable(db, user, server_id)
    planned = await _plan_identity(db, us.server_id, body)
    if not planned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The files already have these tags.")
    changes = [
        TagChange(
            media_file_id=media.id,
            path=media.path,
            rating_key=media.rating_key,
            field=field,
            old_value=clean(field, (media.tags or {}).get(field)),
            new_value=value,
        )
        for media, planned_changes in planned
        for field, value in planned_changes.items()
    ]
    count = len(planned)
    return await _queue(
        db,
        TagChangeSet(
            server_id=server.id,
            user_id=user.id,
            kind="edit",
            description=f"Identified by sound: {count} {'file' if count == 1 else 'files'}",
        ),
        changes,
    )


@router.get("/albums/{album_key}/musicbrainz/suggested", response_model=list[MbReleaseSummary])
async def suggested_releases(
    server_id: int, album_key: int, db: DbDep, user: UserDep
) -> list[MbReleaseSummary]:
    """The releases the album's files were identified on (by sound), most files first."""
    us = await owner_server(db, user, server_id)
    files = list(
        await db.scalars(
            select(MediaFile)
            .join(
                Track,
                (Track.server_id == MediaFile.server_id)
                & (Track.rating_key == MediaFile.rating_key),
            )
            .where(MediaFile.server_id == us.server_id, Track.album_rating_key == album_key)
        )
    )
    counts = _popular(files)
    releases: dict[str, dict[str, Any]] = {}
    for media in files:
        for result in (_current(media) or {}).get("results") or []:
            for rec in result["recordings"]:
                for rel in rec["releases"]:
                    releases.setdefault(rel["id"], rel)
    ranked = sorted(releases.values(), key=lambda r: (-counts[r["id"]], r["date"] or "9999"))
    return [
        MbReleaseSummary(
            id=r["id"],
            score=round(100 * counts[r["id"]] / len(files)),
            title=r["title"],
            artist=r["artist"],
            date=r["date"],
            country=r["country"],
            formats=r["format"] or "",
            track_count=r["track_count"],
            label=None,
            catalog_number=None,
            type=None,
            status=None,
            disambiguation=None,
            barcode=None,
        )
        for r in ranked[:10]
    ]
