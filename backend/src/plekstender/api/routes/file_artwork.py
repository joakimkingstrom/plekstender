"""Artwork in the music files (server owners): embedded covers and folder images (cover.jpg,
artist.jpg). Reading them is the worker's (scans); these routes show what it found and make
edits that the worker applies (resized to what players can show; undo puts pictures back)."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, insert, or_, select

from plekstender.api.deps import DbDep, SettingsDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.routes.files import _editable, _queue, _sets_out
from plekstender.api.schemas import (
    AlbumArtworkOut,
    ArtworkSummaryOut,
    EmbedCoverIn,
    EmbeddedCoverOut,
    FolderCoverIn,
    FolderImageOut,
    ShrinkCoversIn,
    TagChangeSetOut,
)
from plekstender.db.models import Album, FolderImage, MediaFile, TagChange, TagChangeSet, Track
from plekstender.tags.pictures import EMBEDDABLE, MAX_BYTES, MAX_PX, needs_shrinking, thumbnail_path
from plekstender.worker import enqueue_tag_changes

router = APIRouter(prefix="/api/servers/{server_id}/files", tags=["files"])


def noun(n: int, word: str) -> str:
    return word if n == 1 else f"{word}s"


@router.get("/pictures/{sha1}.jpg")
async def picture(
    server_id: int, sha1: str, db: DbDep, user: UserDep, settings: SettingsDep
) -> Response:
    """A cover's thumbnail (made by the worker when it saw the picture)."""
    await owner_server(db, user, server_id)
    path = thumbnail_path(settings.data_dir, sha1)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such picture")
    return Response(
        path.read_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


async def _album(db: DbDep, server_id: int, album_key: int) -> tuple[Album, list[MediaFile]]:
    album = await db.get(Album, (server_id, album_key))
    if album is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Album not found")
    files = list(
        await db.scalars(
            select(MediaFile)
            .join(
                Track,
                (Track.server_id == MediaFile.server_id)
                & (Track.rating_key == MediaFile.rating_key),
            )
            .where(
                MediaFile.server_id == server_id,
                Track.album_rating_key == album_key,
                MediaFile.error.is_(None),
            )
            .order_by(MediaFile.path)
        )
    )
    return album, files


def _album_folder(files: list[MediaFile]) -> str | None:
    """Where most of the album's songs are (where its cover.jpg goes)."""
    folders = Counter(str(PurePosixPath(f.path).parent) for f in files)
    return folders.most_common(1)[0][0] if folders else None


@router.get("/albums/{album_key}/artwork", response_model=AlbumArtworkOut)
async def album_artwork(
    server_id: int, album_key: int, db: DbDep, user: UserDep
) -> AlbumArtworkOut:
    us = await owner_server(db, user, server_id)
    album, files = await _album(db, us.server_id, album_key)
    covers: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for f in files:
        if f.cover:
            covers.setdefault(f.cover["sha1"], f.cover)
            counts[f.cover["sha1"]] += 1
    folders = {str(PurePosixPath(f.path).parent) for f in files}
    # The album's folders, and the folders above them (disc folders: CD1, CD2).
    places = folders | ({str(PurePosixPath(f).parent) for f in folders} - {"."})
    images: list[FolderImage] = []
    if places:
        in_places = or_(
            *(
                ~FolderImage.path.contains("/")  # the music folder itself
                if p == "."
                else func.lower(FolderImage.path).like(f"{p.lower()}/%")
                for p in places
            )
        )
        images = list(
            await db.scalars(
                select(FolderImage)
                .where(FolderImage.server_id == us.server_id, in_places)
                .order_by(FolderImage.path)
            )
        )
    images = [i for i in images if str(PurePosixPath(i.path).parent) in places]
    return AlbumArtworkOut(
        songs=len(files),
        without_cover=sum(1 for f in files if not f.cover),
        covers=[
            EmbeddedCoverOut(
                **{k: c.get(k) for k in ("sha1", "mime", "bytes", "width", "height")},
                files=counts[sha],
                oversized=needs_shrinking(c),
            )
            for sha, c in sorted(covers.items(), key=lambda kv: -counts[kv[0]])
        ],
        folder_images=[FolderImageOut.model_validate(i, from_attributes=True) for i in images],
        folder=_album_folder(files),
        plex_poster=bool(album.thumb),
    )


@router.post(
    "/albums/{album_key}/artwork/embed",
    response_model=TagChangeSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def embed_cover(
    server_id: int, album_key: int, body: EmbedCoverIn, db: DbDep, user: UserDep
) -> TagChangeSetOut:
    """Put one cover in every song of the album (resized for players), as one edit."""
    us, server = await _editable(db, user, server_id)
    album, files = await _album(db, us.server_id, album_key)
    if body.source == "folder":
        image = await db.scalar(
            select(FolderImage).where(
                FolderImage.server_id == us.server_id, FolderImage.path == body.path
            )
        )
        if image is None or image.sha1 is None or image.error:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "That folder image isn't known (scan again)."
            )
        source: dict[str, Any] = {"from": "folder", "path": image.path, "sha1": image.sha1}
        what = PurePosixPath(image.path).name
    else:
        if not album.thumb:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Plex has no poster for this album.")
        source = {"from": "plex", "thumb": album.thumb}
        what = "Plex's poster"
    targets = [f for f in files if PurePosixPath(f.path).suffix.lower() in EMBEDDABLE]
    if not targets:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "None of these files can hold a cover.")
    changes = [
        TagChange(
            media_file_id=f.id,
            path=f.path,
            rating_key=f.rating_key,
            field="picture",
            old_value=(f.cover or {}).get("sha1"),
            new_value={**source, "max_px": body.max_px},
        )
        for f in targets
    ]
    n = len(changes)
    return await _queue(
        db,
        TagChangeSet(
            server_id=server.id,
            user_id=user.id,
            kind="edit",
            description=f"Embedded {what} in {n} {noun(n, 'song')} ({body.max_px} px)",
        ),
        changes,
    )


@router.post(
    "/albums/{album_key}/artwork/folder",
    response_model=TagChangeSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def folder_cover(
    server_id: int, album_key: int, body: FolderCoverIn, db: DbDep, user: UserDep
) -> TagChangeSetOut:
    """Save a cover.jpg (or folder.jpg) in the album's folder, as an edit (undo removes it, or
    puts back the one it replaced)."""
    us, server = await _editable(db, user, server_id)
    album, files = await _album(db, us.server_id, album_key)
    folder = _album_folder(files)
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No scanned files for this album.")
    if body.source == "embedded":
        holder = next((f for f in files if (f.cover or {}).get("sha1") == body.sha1), None)
        if holder is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No song has that cover (scan again).")
        source: dict[str, Any] = {"from": "embedded", "path": holder.path, "sha1": body.sha1}
        what = "the songs' cover"
    else:
        if not album.thumb:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Plex has no poster for this album.")
        source = {"from": "plex", "thumb": album.thumb}
        what = "Plex's poster"
    path = str(PurePosixPath(folder) / body.name)
    existing = await db.scalar(
        select(FolderImage).where(FolderImage.server_id == us.server_id, FolderImage.path == path)
    )
    change = TagChange(
        media_file_id=None,
        path=path,
        rating_key=files[0].rating_key,
        field="image",
        old_value=existing.sha1 if existing else None,
        new_value=source,
    )
    return await _queue(
        db,
        TagChangeSet(
            server_id=server.id, user_id=user.id, kind="edit", description=f"Saved {what} as {path}"
        ),
        [change],
    )


def _oversized() -> Any:
    width = MediaFile.cover["width"].as_integer()
    height = MediaFile.cover["height"].as_integer()
    size = MediaFile.cover["bytes"].as_integer()
    return or_(width > MAX_PX, height > MAX_PX, size > MAX_BYTES * 2)


@router.get("/artwork/summary", response_model=ArtworkSummaryOut)
async def artwork_summary(server_id: int, db: DbDep, user: UserDep) -> ArtworkSummaryOut:
    us = await owner_server(db, user, server_id)
    base = (MediaFile.server_id == us.server_id, MediaFile.error.is_(None))
    songs, without, oversized = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(MediaFile.cover.is_(None)),
                func.count().filter(_oversized()),
            ).where(*base)
        )
    ).one()
    song_folders = set(
        await db.scalars(
            select(func.regexp_replace(MediaFile.path, "/[^/]*$", "")).where(*base).distinct()
        )
    )
    image_folders = set(
        await db.scalars(
            select(func.regexp_replace(FolderImage.path, "/[^/]*$", "")).where(
                FolderImage.server_id == us.server_id, FolderImage.kind == "cover"
            )
        )
    )
    return ArtworkSummaryOut(
        songs=songs,
        without_cover=without,
        oversized=oversized,
        folders_without_cover=len(song_folders - image_folders),
    )


@router.post("/artwork/shrink", response_model=TagChangeSetOut, status_code=status.HTTP_201_CREATED)
async def shrink_covers(
    server_id: int, body: ShrinkCoversIn, db: DbDep, user: UserDep
) -> TagChangeSetOut:
    """Make every oversized embedded cover player-sized (one edit; undo puts them back)."""
    us, server = await _editable(db, user, server_id)
    rows = (
        await db.execute(
            select(MediaFile.id, MediaFile.path, MediaFile.rating_key, MediaFile.cover).where(
                MediaFile.server_id == us.server_id, MediaFile.error.is_(None), _oversized()
            )
        )
    ).all()
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No covers are too big.")
    change_set = TagChangeSet(
        server_id=server.id,
        user_id=user.id,
        kind="edit",
        description=f"Shrank {len(rows)} embedded {noun(len(rows), 'cover')} to {body.max_px} px",
    )
    db.add(change_set)
    await db.flush()
    values = [
        {
            "change_set_id": change_set.id,
            "media_file_id": file_id,
            "path": path,
            "rating_key": rating_key,
            "field": "picture",
            "old_value": (cover or {}).get("sha1"),
            "new_value": {"from": "self", "max_px": body.max_px},
            "status": "pending",
        }
        for file_id, path, rating_key, cover in rows
    ]
    for start in range(0, len(values), 2000):
        await db.execute(insert(TagChange).values(values[start : start + 2000]))
    await db.commit()
    await enqueue_tag_changes(change_set)
    [out] = await _sets_out(db, [change_set])
    return out
