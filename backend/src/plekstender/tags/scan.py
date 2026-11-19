"""Scanning a server's music folder (worker only): which files there are, their tags, and
which Plex track each one is.

Files are re-read only when their size or modification time changed (or the tag reader
learned new fields), so a rescan of a big library is quick. Symlinks aren't followed
(nothing outside the mapped folder is read).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import FileScan, MediaFile, NfoFile, PlexServer, Track
from plekstender.tags.nfo import NfoError, kind_of
from plekstender.tags.nfo import parse as parse_nfo
from plekstender.tags.read import AUDIO_EXTENSIONS, READER, TagReadError, read_file

log = logging.getLogger(__name__)

BATCH = 200


class ScanError(Exception):
    """Stops the scan with a message for the user."""


@dataclass
class Found:
    path: str  # relative to the root, POSIX
    size: int
    mtime: float


def walk(root: Path) -> list[Found]:
    """Every audio file under `root` (symlinks not followed)."""
    return walk_all(root)[0]


def walk_all(root: Path) -> tuple[list[Found], list[Found]]:
    """Every audio file and every artist.nfo / album.nfo under `root` (no symlinks)."""
    audio: list[Found] = []
    nfos: list[Found] = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            is_nfo = kind_of(name) is not None
            if name.startswith(".") or not (
                is_nfo or Path(name).suffix.lower() in AUDIO_EXTENSIONS
            ):
                continue
            full = Path(folder) / name
            try:
                st = full.lstat()
            except OSError:
                continue
            if not full.is_file() or full.is_symlink():
                continue
            item = Found(full.relative_to(root).as_posix(), st.st_size, st.st_mtime)
            (nfos if is_nfo else audio).append(item)
    return audio, nfos


def _read_nfos(root: Path, items: list[Found]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        try:
            kind, data = parse_nfo(root / item.path)
            out.append({"kind": kind, "data": data, "error": None})
        except NfoError as exc:
            out.append(
                {"kind": kind_of(PurePosixPath(item.path).name), "data": {}, "error": str(exc)}
            )
    return out


async def _scan_nfos(db: AsyncSession, server: PlexServer, root: Path, nfos: list[Found]) -> int:
    """Read new and changed .nfo files; forget the ones that are gone."""
    known = {
        path: (size, mtime)
        for path, size, mtime in (
            await db.execute(
                select(NfoFile.path, NfoFile.size, NfoFile.mtime).where(
                    NfoFile.server_id == server.id
                )
            )
        ).all()
    }
    changed = [f for f in nfos if known.get(f.path) != (f.size, f.mtime)]
    for start in range(0, len(changed), BATCH):
        batch = changed[start : start + BATCH]
        read = await asyncio.to_thread(_read_nfos, root, batch)
        now = dt.datetime.now(dt.UTC)
        rows = [
            {
                "server_id": server.id,
                "path": f.path,
                "kind": info["kind"],
                "size": f.size,
                "mtime": f.mtime,
                "data": info["data"],
                "error": info["error"],
                "scanned_at": now,
            }
            for f, info in zip(batch, read, strict=True)
        ]
        stmt = insert(NfoFile).values(rows)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[NfoFile.server_id, NfoFile.path],
                set_={c: stmt.excluded[c] for c in rows[0] if c not in ("server_id", "path")},
            )
        )
        await db.commit()
    present = {f.path for f in nfos}
    gone = [path for path in known if path not in present]
    for start in range(0, len(gone), 1000):
        await db.execute(
            delete(NfoFile).where(
                NfoFile.server_id == server.id, NfoFile.path.in_(gone[start : start + 1000])
            )
        )
    await db.commit()
    return len(nfos)


def _read(root: Path, item: Found) -> dict[str, Any]:
    try:
        return {**read_file(root / item.path), "error": None}
    except TagReadError as exc:
        return {
            "format": None,
            "duration_ms": None,
            "tags": {},
            "has_cover": False,
            "error": str(exc),
        }


def _read_all(root: Path, batch: list[Found]) -> list[dict[str, Any]]:
    return [_read(root, f) for f in batch]


def plex_path(server: PlexServer, relative: str) -> str:
    assert server.plex_path_prefix is not None
    return str(PurePosixPath(server.plex_path_prefix) / relative)


async def run_scan(db: AsyncSession, scan_id: int) -> None:
    scan = await db.get(FileScan, scan_id)
    if scan is None or scan.status != "queued":
        return
    server = await db.get(PlexServer, scan.server_id)
    now = dt.datetime.now(dt.UTC)
    scan.status = "running"
    scan.started_at = now
    scan.updated_at = now
    await db.commit()
    try:
        if server is None or not (server.plex_path_prefix and server.local_path_prefix):
            raise ScanError("Set up the music folder first (Settings → Music files).")
        root = Path(server.local_path_prefix)
        if not await asyncio.to_thread(root.is_dir):
            raise ScanError(
                f"{root} doesn't exist in the worker container. Mount your music folder there "
                "(see compose.override.example.yaml)."
            )
        resolved = await asyncio.to_thread(root.resolve)
        stats = await _scan(db, scan, server, resolved)
        scan.stats = stats
        scan.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, ScanError):
            log.exception("scan %s failed", scan_id)
        await db.rollback()
        failed = await db.get(FileScan, scan_id, populate_existing=True)
        if failed is None:
            return
        scan = failed
        scan.status = "failed"
        scan.error = (
            str(exc) if isinstance(exc, ScanError) else "Unexpected error; see the worker log."
        )
    finished = dt.datetime.now(dt.UTC)
    scan.finished_at = finished
    scan.updated_at = finished
    await db.commit()


async def _scan(db: AsyncSession, scan: FileScan, server: PlexServer, root: Path) -> dict[str, int]:
    files, nfos = await asyncio.to_thread(walk_all, root)
    known = {
        path: (size, mtime, reader)
        for path, size, mtime, reader in (
            await db.execute(
                select(MediaFile.path, MediaFile.size, MediaFile.mtime, MediaFile.reader).where(
                    MediaFile.server_id == server.id
                )
            )
        ).all()
    }
    # Plex's tracks by file path, to match files to them.
    tracks = {
        path: key
        for path, key in (
            await db.execute(
                select(Track.file_path, Track.rating_key).where(
                    Track.server_id == server.id, Track.file_path.is_not(None)
                )
            )
        ).all()
    }
    stats = {"files": len(files), "read": 0, "unchanged": 0, "removed": 0, "errors": 0}
    scan.progress = {"done": 0, "total": len(files)}
    await db.commit()

    changed = [f for f in files if known.get(f.path) != (f.size, f.mtime, READER)]
    stats["unchanged"] = len(files) - len(changed)
    for start in range(0, len(changed), BATCH):
        batch = changed[start : start + BATCH]
        read = await asyncio.to_thread(_read_all, root, batch)
        now = dt.datetime.now(dt.UTC)
        rows = [
            {
                "server_id": server.id,
                "path": f.path,
                "size": f.size,
                "mtime": f.mtime,
                "format": info["format"],
                "duration_ms": info["duration_ms"],
                "tags": info["tags"],
                "has_cover": info["has_cover"],
                "reader": READER,
                "error": info["error"],
                "rating_key": tracks.get(plex_path(server, f.path)),
                "scanned_at": now,
            }
            for f, info in zip(batch, read, strict=True)
        ]
        stmt = insert(MediaFile).values(rows)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[MediaFile.server_id, MediaFile.path],
                set_={c: stmt.excluded[c] for c in rows[0] if c not in ("server_id", "path")},
            )
        )
        stats["read"] += len(batch)
        stats["errors"] += sum(1 for info in read if info["error"])
        scan.progress = {"done": stats["unchanged"] + stats["read"], "total": len(files)}
        scan.updated_at = dt.datetime.now(dt.UTC)
        await db.commit()

    # Files that are gone, and matches for unchanged files (Plex may have new tracks).
    present = {f.path for f in files}
    gone = [path for path in known if path not in present]
    for start in range(0, len(gone), 1000):
        await db.execute(
            delete(MediaFile).where(
                MediaFile.server_id == server.id, MediaFile.path.in_(gone[start : start + 1000])
            )
        )
    stats["removed"] = len(gone)
    stats["nfo"] = await _scan_nfos(db, server, root, nfos)
    unmatched = await db.scalars(
        select(MediaFile).where(MediaFile.server_id == server.id, MediaFile.rating_key.is_(None))
    )
    for row in unmatched:
        row.rating_key = tracks.get(plex_path(server, row.path))
    await db.commit()
    stats["matched"] = (
        await db.scalar(
            select(func.count()).where(
                MediaFile.server_id == server.id, MediaFile.rating_key.is_not(None)
            )
        )
        or 0
    )
    return stats
