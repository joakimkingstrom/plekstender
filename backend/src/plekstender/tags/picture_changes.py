"""Applying picture changes (worker): a file's embedded front cover ("picture") and folder
images such as cover.jpg ("image").

A change's `old_value` is the SHA-1 of the picture there now (None: none), checked before
anything is replaced. Its `new_value` is a SHA-1 from the picture store (undo), None (remove),
or, for a new edit, where the picture comes from:

- `{"from": "folder", "path": "...", "sha1": "..."}`: a folder image (still that picture)
- `{"from": "plex", "thumb": "/library/metadata/1/thumb/2"}`: Plex's poster
- `{"from": "embedded", "path": "...", "sha1": "..."}`: a song's embedded cover
- `{"from": "self"}`: the file's own cover (shrinking it)

plus `"max_px"` for embedded covers. Every picture replaced is kept in the store first, and
the change then records the SHA-1s of what was there and what was written, so undo works.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import (
    FolderImage,
    MediaFile,
    PlexServer,
    TagChange,
    TagChangeSet,
    UserServer,
)
from plekstender.jobs import check_stop
from plekstender.plex.tv import PlexError
from plekstender.tags.pictures import (
    MAX_PX,
    PictureError,
    cover_of,
    embed,
    embedded,
    image_kind,
    info,
    keep,
    kept,
    prepare,
    sha1,
    thumbnail,
)
from plekstender.tags.read import COVERS_READER
from plekstender.tags.rename import MoveError, _within

log = logging.getLogger(__name__)

FIELDS = ("picture", "image")
PLEX_SIZE = 1500


class Skip(Exception):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


async def _plex_picture(
    settings: Settings, http: httpx.AsyncClient, db: AsyncSession, server: PlexServer, thumb: str
) -> bytes:
    from plekstender.plex.server import client_for

    owner = await db.scalar(
        select(UserServer).where(UserServer.server_id == server.id, UserServer.is_owner.is_(True))
    )
    if owner is None:
        raise Skip("failed", "No owner account to fetch Plex's poster with.")
    try:
        data, _ = await client_for(settings, http, owner).photo(thumb, PLEX_SIZE)
    except (PlexError, httpx.HTTPError) as exc:
        raise Skip("failed", f"Couldn't get Plex's poster: {exc}") from exc
    return data


def _source(
    root: Path, data_dir: Path, new: Any, plex: dict[str, bytes], own: bytes | None
) -> bytes | None:
    """The picture a change writes (before resizing)."""
    if new is None:
        return None
    if isinstance(new, str):
        try:
            return kept(data_dir, new)
        except PictureError as exc:
            raise Skip("failed", str(exc)) from exc
    origin = new.get("from")
    if origin == "plex":
        return plex[new["thumb"]]
    if origin == "self":
        if own is None:
            raise Skip("conflict", "The file has no cover any more.")
        return own
    if origin in ("folder", "embedded"):
        try:
            path = _within(root, new["path"])
        except MoveError as exc:
            raise Skip("failed", str(exc)) from exc
        if origin == "folder":
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise Skip("conflict", f"{new['path']} isn't there any more.") from exc
        else:
            found = embedded(path)
            data = found[0] if found else b""
        if sha1(data) != new.get("sha1"):
            raise Skip("conflict", f"The picture in {new['path']} changed since (scan again).")
        return data
    raise Skip("failed", "Unknown picture source.")


def _apply_picture(
    root: Path, data_dir: Path, media: MediaFile, change: TagChange, plex: dict[str, bytes]
) -> None:
    """Replace a file's embedded front cover (in a thread)."""
    try:
        path = _within(root, media.path)
    except MoveError as exc:
        raise Skip("failed", str(exc)) from exc
    if not path.is_file():
        raise Skip("failed", "The file isn't in the music folder any more.")
    try:
        found = embedded(path)
    except PictureError as exc:
        raise Skip("failed", str(exc)) from exc
    current = sha1(found[0]) if found else None
    known = media.reader >= COVERS_READER  # scanned before covers were described: whatever is there
    if known and current != change.old_value:
        raise Skip("conflict", "The file's cover changed since it was scanned (scan again).")
    data = _source(root, data_dir, change.new_value, plex, found[0] if found else None)
    if data is not None and not isinstance(change.new_value, str):
        max_px = int((change.new_value or {}).get("max_px") or MAX_PX)
        try:
            data = prepare(data, max_px)
        except PictureError as exc:
            raise Skip("failed", str(exc)) from exc
    if found:
        keep(data_dir, found[0])
    new_sha1 = keep(data_dir, data) if data is not None else None
    if new_sha1 != current:
        try:
            embed(path, data)
        except PictureError as exc:
            raise Skip("failed", str(exc)) from exc
    change.old_value, change.new_value = current, new_sha1


def _apply_image(
    root: Path, data_dir: Path, change: TagChange, plex: dict[str, bytes]
) -> dict[str, Any] | None:
    """Write (or remove) a folder image. Returns its new description (None: removed)."""
    try:
        path = _within(root, change.path)
    except MoveError as exc:
        raise Skip("failed", str(exc)) from exc
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Skip("failed", "That isn't a plain file.")
    old = path.read_bytes() if path.exists() else None
    current = sha1(old) if old is not None else None
    if current != change.old_value:
        raise Skip("conflict", "The picture there changed since it was scanned (scan again).")
    data = _source(root, data_dir, change.new_value, plex, None)
    if data is not None and not isinstance(change.new_value, str):
        described = info(data)
        if described.mime != "image/jpeg" and path.suffix.lower() in {".jpg", ".jpeg"}:
            data = prepare(data, 4000, 5_000_000)  # a .jpg must be a JPEG
    if old is not None:
        keep(data_dir, old)
    new_sha1 = keep(data_dir, data) if data is not None else None
    if data is None:
        path.unlink(missing_ok=True)
    elif new_sha1 != current:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.plekstender")
        temporary.write_bytes(data)
        temporary.replace(path)
    change.old_value, change.new_value = current, new_sha1
    if data is None:
        return None
    described = info(data)
    thumbnail(data_dir, data, described.sha1)
    st = path.stat()
    return {"sha1": described.sha1, "width": described.width, "height": described.height,
            "size": st.st_size, "mtime": st.st_mtime}  # fmt: skip


async def apply_pictures(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    root: Path,
    server: PlexServer,
    set_id: int,
    touched: set[tuple[int | None, str]],
) -> None:
    changes = list(
        await db.scalars(
            select(TagChange)
            .where(
                TagChange.change_set_id == set_id,
                TagChange.status == "pending",
                TagChange.field.in_(FIELDS),
            )
            .order_by(TagChange.id)
        )
    )
    if not changes:
        return
    plex: dict[str, bytes] = {}
    for change in changes:
        new = change.new_value
        if isinstance(new, dict) and new.get("from") == "plex" and new["thumb"] not in plex:
            try:
                plex[new["thumb"]] = await _plex_picture(settings, http, db, server, new["thumb"])
            except Skip as exc:
                for c in changes:
                    if isinstance(c.new_value, dict) and c.new_value.get("thumb") == new["thumb"]:
                        c.status, c.error = exc.status, str(exc)
    data_dir = settings.data_dir
    for change in changes:
        await check_stop(db, TagChangeSet, set_id)
        if change.status != "pending":
            continue
        try:
            if change.field == "picture":
                media = (
                    await db.get(MediaFile, change.media_file_id) if change.media_file_id else None
                )
                if media is None:
                    raise Skip("failed", "The file isn't in the music folder any more.")
                await asyncio.to_thread(_apply_picture, root, data_dir, media, change, plex)
                path = _within(root, media.path)
                media.cover = await asyncio.to_thread(cover_of, path, data_dir)
                media.has_cover = media.cover is not None
                st = await asyncio.to_thread(path.stat)
                media.size, media.mtime = st.st_size, st.st_mtime
                touched.add((media.rating_key, str(PurePosixPath(media.path).parent)))
            else:
                described = await asyncio.to_thread(_apply_image, root, data_dir, change, plex)
                if described is None:
                    await db.execute(
                        delete(FolderImage).where(
                            FolderImage.server_id == server.id, FolderImage.path == change.path
                        )
                    )
                else:
                    row = {
                        "server_id": server.id,
                        "path": change.path,
                        "kind": image_kind(PurePosixPath(change.path).name) or "cover",
                        "error": None,
                        **described,
                    }
                    stmt = insert(FolderImage).values(row)
                    await db.execute(
                        stmt.on_conflict_do_update(
                            index_elements=[FolderImage.server_id, FolderImage.path],
                            set_={
                                k: stmt.excluded[k] for k in row if k not in ("server_id", "path")
                            },
                        )
                    )
                touched.add((change.rating_key, str(PurePosixPath(change.path).parent)))
            change.status, change.error = "applied", None
        except Skip as exc:
            change.status, change.error = exc.status, str(exc)
        except (OSError, PictureError) as exc:
            change.status, change.error = "failed", f"Couldn't write it: {exc}"
        await db.commit()
