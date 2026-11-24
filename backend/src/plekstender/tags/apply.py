"""Applying a change set (worker only): moves first (a file's "path" changing), then tags
(snapshot, write, re-read); then Plex is asked to rescan."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections import defaultdict
from pathlib import Path, PurePosixPath

import httpx
from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings, get_settings
from plekstender.db.models import (
    MediaFile,
    PlexServer,
    TagChange,
    TagChangeSet,
    TagSnapshot,
    Track,
    UserServer,
)
from plekstender.plex.tv import PlexError
from plekstender.tags.lyrics import FIELDS as LYRICS_FIELDS
from plekstender.tags.lyrics import apply_lyrics
from plekstender.tags.picture_changes import apply_pictures
from plekstender.tags.pictures import cover_of
from plekstender.tags.read import READER, TagReadError, read_file, unread_fields
from plekstender.tags.rename import MoveError, move, remove_empty_folders
from plekstender.tags.write import Planned, TagWriteError, apply, snapshot

log = logging.getLogger(__name__)

FILE_BATCH = 200
# Changes handled apart from tags: moves first, then pictures and lyrics.
SPECIAL = ("path", "picture", "image", *LYRICS_FIELDS)
# More folders than this changed: Plex rescans the library section(s) instead.
MAX_FOLDER_RESCANS = 25


def _inside(root: Path, relative: str) -> Path | None:
    """The file, if it's a real file inside the music folder (no symlinks out)."""
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError, RuntimeError:
        return None
    if not resolved.is_relative_to(root) or path.is_symlink() or not resolved.is_file():
        return None
    return resolved


async def run_change_set(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, set_id: int
) -> None:
    change_set = await db.get(TagChangeSet, set_id)
    if change_set is None or change_set.status != "queued":
        return
    change_set.status = "running"
    change_set.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    server = await db.get(PlexServer, change_set.server_id)
    if server is None or not server.local_path_prefix or not server.tag_editing:
        await db.execute(
            update(TagChange)
            .where(TagChange.change_set_id == set_id)
            .values(status="failed", error="Tag editing is off or the folder isn't set up.")
        )
        change_set.status = "failed"
        change_set.error = "Tag editing is off, or the music folder isn't set up."
        await db.commit()
        return
    root = await asyncio.to_thread(lambda: Path(server.local_path_prefix or "").resolve())
    moved = await _apply_moves(db, root, server, set_id)
    touched: set[tuple[int | None, str]] = set()  # (Plex track, folder) for Plex rescans
    await apply_pictures(db, http, settings, root, server, set_id, touched)
    await apply_lyrics(db, root, server, set_id, touched)
    # File by file, a batch of files at a time (a library-wide edit can be very big).
    file_ids = list(
        await db.scalars(
            select(TagChange.media_file_id)
            .where(
                TagChange.change_set_id == set_id,
                TagChange.status == "pending",
                TagChange.field.not_in(SPECIAL),
            )
            .group_by(TagChange.media_file_id)
            .order_by(func.min(TagChange.id))
        )
    )
    for start in range(0, len(file_ids), FILE_BATCH):
        batch = file_ids[start : start + FILE_BATCH]
        ids = [f for f in batch if f is not None]
        in_batch: ColumnElement[bool] = TagChange.media_file_id.in_(ids)
        if None in batch:  # changes whose file was forgotten since
            in_batch = or_(in_batch, TagChange.media_file_id.is_(None))
        changes = list(
            await db.scalars(
                select(TagChange)
                .where(
                    TagChange.change_set_id == set_id,
                    TagChange.status == "pending",
                    TagChange.field.not_in(SPECIAL),
                    in_batch,
                )
                .order_by(TagChange.id)
            )
        )
        by_file: dict[int | None, list[TagChange]] = defaultdict(list)
        for change in changes:
            by_file[change.media_file_id].append(change)
        for file_id, file_changes in by_file.items():
            await _apply_file(db, root, file_id, file_changes, touched)
        change_set.updated_at = dt.datetime.now(dt.UTC)
        await db.commit()

    counts = dict(
        (
            await db.execute(
                select(TagChange.status, func.count())
                .where(TagChange.change_set_id == set_id)
                .group_by(TagChange.status)
            )
        ).all()
    )
    statuses = set(counts)
    change_set.status = (
        "applied"
        if statuses == {"applied"}
        else "failed"
        if "applied" not in statuses
        else "partial"
    )
    change_set.applied_at = change_set.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    await _tell_plex(db, http, settings, server, touched, moved)


async def _apply_moves(db: AsyncSession, root: Path, server: PlexServer, set_id: int) -> set[int]:
    """Move files (renames): each one only if it's still where it was and nothing is where it
    goes. Returns the Plex tracks moved (their library sections are rescanned)."""
    changes = list(
        await db.scalars(
            select(TagChange)
            .where(
                TagChange.change_set_id == set_id,
                TagChange.status == "pending",
                TagChange.field == "path",
            )
            .order_by(TagChange.id)
        )
    )
    moved: set[int] = set()
    left: set[str] = set()
    for change in changes:
        source, target = str(change.old_value), str(change.new_value)
        try:
            await asyncio.to_thread(move, root, source, target)
        except MoveError as exc:
            change.status, change.error = exc.status, str(exc)
            await db.commit()
            continue
        change.status, change.error = "applied", None
        left.add(str(PurePosixPath(source).parent))
        if change.media_file_id is not None:
            # A row for a file that was at the target before is out of date (it isn't there).
            await db.execute(
                delete(MediaFile).where(
                    MediaFile.server_id == server.id,
                    MediaFile.path == target,
                    MediaFile.id != change.media_file_id,
                )
            )
            await db.execute(
                update(MediaFile).where(MediaFile.id == change.media_file_id).values(path=target)
            )
        if change.rating_key is not None and server.plex_path_prefix:
            moved.add(change.rating_key)
            await db.execute(  # what Plex will call it (the next library sync confirms)
                update(Track)
                .where(Track.server_id == server.id, Track.rating_key == change.rating_key)
                .values(file_path=str(PurePosixPath(server.plex_path_prefix) / target))
            )
        await db.commit()
    if left:
        await asyncio.to_thread(remove_empty_folders, root, left)
    return moved


async def _apply_file(
    db: AsyncSession,
    root: Path,
    file_id: int | None,
    file_changes: list[TagChange],
    touched: set[tuple[int | None, str]],
) -> None:
    media = await db.get(MediaFile, file_id) if file_id else None
    path = await asyncio.to_thread(_inside, root, media.path) if media else None
    if media is None or path is None:
        for change in file_changes:
            change.status, change.error = "failed", "The file isn't in the music folder any more."
        return
    try:
        if await db.get(TagSnapshot, media.id) is None:
            db.add(
                TagSnapshot(
                    media_file_id=media.id,
                    path=media.path,
                    tags=await asyncio.to_thread(snapshot, path),
                )
            )
    except TagWriteError as exc:
        log.warning("no snapshot of %s: %s", media.path, exc)
    unknown = unread_fields(media.reader)
    planned = [
        Planned(c.id, c.field, c.old_value, c.new_value, known=c.field not in unknown)
        for c in file_changes
    ]
    outcomes = {o.id: o for o in await asyncio.to_thread(apply, path, planned)}
    for change in file_changes:
        outcome = outcomes[change.id]
        change.status, change.error = outcome.status, outcome.error
        if outcome.replaced:
            change.old_value = outcome.found  # what undo puts back
    if any(o.status == "applied" for o in outcomes.values()):
        await _reread(media, path)
        touched.add((media.rating_key, str(PurePosixPath(media.path).parent)))


async def _reread(media: MediaFile, path: Path) -> None:
    try:
        info = await asyncio.to_thread(read_file, path)
        st = await asyncio.to_thread(path.stat)
    except (TagReadError, OSError) as exc:
        media.error = str(exc)
        return
    media.tags = info["tags"]
    media.has_cover = info["has_cover"]
    media.cover = (
        await asyncio.to_thread(cover_of, path, get_settings().data_dir)
        if info["has_cover"]
        else None
    )
    media.reader = READER
    media.size, media.mtime = st.st_size, st.st_mtime
    media.error = None


async def _tell_plex(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    server: PlexServer,
    touched: set[tuple[int | None, str]],
    moved: set[int] | None = None,
) -> None:
    """Ask Plex to rescan the folders whose files changed (best effort). Moved files: their
    whole library sections, so Plex sees the old and new places in one scan (and can tell a
    moved file from a deleted one)."""
    if not (touched or moved) or not server.plex_path_prefix:
        return
    from plekstender.plex.server import client_for

    owner = await db.scalar(
        select(UserServer).where(UserServer.server_id == server.id, UserServer.is_owner.is_(True))
    )
    if owner is None:
        return
    client = client_for(settings, http, owner)
    folders: dict[str, str] = {}
    for rating_key, folder in touched:
        section = (
            await db.scalar(
                select(Track.section_key).where(
                    Track.server_id == server.id, Track.rating_key == rating_key
                )
            )
            if rating_key
            else None
        )
        if section:
            folders[str(PurePosixPath(server.plex_path_prefix) / folder)] = section
    whole: set[str] = set()
    for rating_key in moved or set():
        section = await db.scalar(
            select(Track.section_key).where(
                Track.server_id == server.id, Track.rating_key == rating_key
            )
        )
        if section:
            whole.add(section)
    if len(folders) > MAX_FOLDER_RESCANS:
        whole |= set(folders.values())
    try:
        for section in whole:
            await client.refresh_folder(section, None)
        for folder, section in folders.items():
            if section not in whole:  # already rescanned as a whole
                await client.refresh_folder(section, folder)
    except (PlexError, httpx.HTTPError) as exc:
        log.info("Plex rescan failed: %s", exc)
