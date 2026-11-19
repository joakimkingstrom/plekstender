"""Applying a tag change set (worker only): snapshot, write, re-read, tell Plex."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections import defaultdict
from pathlib import Path, PurePosixPath

import httpx
from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
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
from plekstender.tags.read import TagReadError, read_file
from plekstender.tags.write import Planned, TagWriteError, apply, snapshot

log = logging.getLogger(__name__)

FILE_BATCH = 200
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
    # File by file, a batch of files at a time (a library-wide edit can be very big).
    file_ids = list(
        await db.scalars(
            select(TagChange.media_file_id)
            .where(TagChange.change_set_id == set_id, TagChange.status == "pending")
            .group_by(TagChange.media_file_id)
            .order_by(func.min(TagChange.id))
        )
    )
    touched: set[tuple[int | None, str]] = set()  # (Plex track, folder) for Plex rescans
    for start in range(0, len(file_ids), FILE_BATCH):
        batch = file_ids[start : start + FILE_BATCH]
        ids = [f for f in batch if f is not None]
        in_batch: ColumnElement[bool] = TagChange.media_file_id.in_(ids)
        if None in batch:  # changes whose file was forgotten since
            in_batch = or_(in_batch, TagChange.media_file_id.is_(None))
        changes = list(
            await db.scalars(
                select(TagChange)
                .where(TagChange.change_set_id == set_id, TagChange.status == "pending", in_batch)
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
    await _tell_plex(db, http, settings, server, touched)


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
    planned = [Planned(c.id, c.field, c.old_value, c.new_value) for c in file_changes]
    outcomes = {o.id: o for o in await asyncio.to_thread(apply, path, planned)}
    for change in file_changes:
        outcome = outcomes[change.id]
        change.status, change.error = outcome.status, outcome.error
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
    media.size, media.mtime = st.st_size, st.st_mtime
    media.error = None
    media.scanned_at = dt.datetime.now(dt.UTC)


async def _tell_plex(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    server: PlexServer,
    touched: set[tuple[int | None, str]],
) -> None:
    """Ask Plex to rescan the folders whose files changed (best effort)."""
    if not touched or not server.plex_path_prefix:
        return
    from plekstender.artwork.jobs import client_for

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
    try:
        if len(folders) > MAX_FOLDER_RESCANS:
            for section in set(folders.values()):
                await client.refresh_folder(section, None)
        else:
            for folder, section in folders.items():
                await client.refresh_folder(section, folder)
    except (PlexError, httpx.HTTPError) as exc:
        log.info("Plex rescan failed: %s", exc)
