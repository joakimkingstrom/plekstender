"""Scanning a server's music folder (worker only): which files there are, their tags, and
which Plex track each one is.

Files are re-read only when their size or modification time changed (or the tag reader
learned new fields), so a rescan of a big library is quick. Symlinks aren't followed
(nothing outside the mapped folder is read).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import get_settings
from plekstender.db.models import (
    FileScan,
    FolderImage,
    FolderVideo,
    MediaFile,
    NfoFile,
    PlexServer,
    Track,
)
from plekstender.jobs import JobStopped, check_stop
from plekstender.tags import embedded_lyrics
from plekstender.tags.lyrics_kinds import (
    LYRICSFILE,
    SIDECARS,
    Kind,
    best,
    of_sidecar,
    of_text,
    sidecar_path,
)
from plekstender.tags.nfo import NfoError, kind_of
from plekstender.tags.nfo import parse as parse_nfo
from plekstender.tags.pictures import cover_of, image_kind, thumbnail
from plekstender.tags.pictures import info as picture_info
from plekstender.tags.read import (
    AUDIO_EXTENSIONS,
    READER,
    TagReadError,
    is_video_cover,
    read_file,
)

log = logging.getLogger(__name__)

BATCH = 200
# What a scan is doing, for its progress.
STEPS = {
    "files": "Music files",
    "lyrics": "Lyrics files",
    "nfo": ".nfo files",
    "images": "Folder pictures",
    "matching": "Matching files to Plex",
}
# Saves a step's progress (and stops the scan there when it's been asked to): done, total.
Report = Callable[[int, int], Awaitable[None]] | None


class ScanError(Exception):
    """Stops the scan with a message for the user."""


@dataclass
class Found:
    path: str  # relative to the root, POSIX
    size: int
    mtime: float


def walk(root: Path) -> list[Found]:
    """Every audio file under `root` (symlinks not followed)."""
    return walk_all(root).audio


def _is_lyrics(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(LYRICSFILE) or Path(lower).suffix in (".lrc", ".txt")


@dataclass
class Walked:
    audio: list[Found]
    nfos: list[Found]
    images: list[Found]
    lyrics: set[str]
    videos: list[Found]


def walk_all(root: Path) -> Walked:
    """Every audio file, artist.nfo / album.nfo, folder image (cover.jpg, artist.jpg...) and
    video cover (cover.mp4...) under `root` (no symlinks), and the paths of lyrics files
    (.lrc, .txt, .lyricsfile.yaml). A video cover isn't a song, whatever its extension."""
    audio: list[Found] = []
    nfos: list[Found] = []
    images: list[Found] = []
    videos: list[Found] = []
    lyrics: set[str] = set()
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if not name.startswith(".") and _is_lyrics(name):
                lyrics.add((Path(folder) / name).relative_to(root).as_posix())
                continue
            is_nfo = kind_of(name) is not None
            is_image = image_kind(name) is not None
            is_video = is_video_cover(name)
            if name.startswith(".") or not (
                is_nfo or is_image or is_video or Path(name).suffix.lower() in AUDIO_EXTENSIONS
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
            (nfos if is_nfo else images if is_image else videos if is_video else audio).append(item)
    return Walked(audio, nfos, images, lyrics, videos)


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


async def _scan_nfos(
    db: AsyncSession, server: PlexServer, root: Path, nfos: list[Found], report: Report = None
) -> int:
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
        if report:
            await report(start, len(changed))
        batch = changed[start : start + BATCH]
        read = await asyncio.to_thread(_read_nfos, root, batch)
        rows = [
            {
                "server_id": server.id,
                "path": f.path,
                "kind": info["kind"],
                "size": f.size,
                "mtime": f.mtime,
                "data": info["data"],
                "error": info["error"],
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


def _read(root: Path, item: Found, data_dir: Path) -> dict[str, Any]:
    try:
        info = read_file(root / item.path)
    except TagReadError as exc:
        return {
            "format": None,
            "duration_ms": None,
            "tags": {},
            "has_cover": False,
            "cover": None,
            "embedded_lyrics": None,
            "error": str(exc),
        }
    cover = cover_of(root / item.path, data_dir) if info["has_cover"] else None
    try:
        lyrics = of_text(embedded_lyrics.read(root / item.path))
    except embedded_lyrics.LyricsError:
        lyrics = None
    return {**info, "cover": cover, "embedded_lyrics": lyrics, "error": None}


def lyrics_beside(root: Path, songs: list[str], lyrics: set[str]) -> dict[str, Kind]:
    """The best kind of lyrics in files next to each song (songs without any are left out)."""
    out: dict[str, Kind] = {}
    for song in songs:
        found = [
            of_sidecar(root / sidecar_path(song, suffix))
            for suffix in SIDECARS
            if sidecar_path(song, suffix) in lyrics
        ]
        if (kind := best(*found)) is not None:
            out[song] = kind
    return out


def _read_all(root: Path, batch: list[Found], data_dir: Path) -> list[dict[str, Any]]:
    return [_read(root, f, data_dir) for f in batch]


def _read_images(root: Path, items: list[Found], data_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        try:
            data = (root / item.path).read_bytes()
        except OSError as exc:
            out.append({"sha1": None, "width": None, "height": None, "error": str(exc)})
            continue
        described = picture_info(data)
        if described.width is None:
            out.append(
                {
                    "sha1": described.sha1,
                    "width": None,
                    "height": None,
                    "error": "Not a picture Plekstender can read.",
                }
            )
            continue
        thumbnail(data_dir, data, described.sha1)
        out.append(
            {
                "sha1": described.sha1,
                "width": described.width,
                "height": described.height,
                "error": None,
            }
        )
    return out


async def _scan_images(
    db: AsyncSession, server: PlexServer, root: Path, images: list[Found], report: Report = None
) -> int:
    """Describe new and changed folder images (cover.jpg, artist.jpg...); forget gone ones."""
    data_dir = get_settings().data_dir
    known = {
        path: (size, mtime)
        for path, size, mtime in (
            await db.execute(
                select(FolderImage.path, FolderImage.size, FolderImage.mtime).where(
                    FolderImage.server_id == server.id
                )
            )
        ).all()
    }
    changed = [f for f in images if known.get(f.path) != (f.size, f.mtime)]
    for start in range(0, len(changed), BATCH):
        if report:
            await report(start, len(changed))
        batch = changed[start : start + BATCH]
        read = await asyncio.to_thread(_read_images, root, batch, data_dir)
        rows = [
            {
                "server_id": server.id,
                "path": f.path,
                "kind": image_kind(PurePosixPath(f.path).name) or "cover",
                "size": f.size,
                "mtime": f.mtime,
                **info,
            }
            for f, info in zip(batch, read, strict=True)
        ]
        stmt = insert(FolderImage).values(rows)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[FolderImage.server_id, FolderImage.path],
                set_={c: stmt.excluded[c] for c in rows[0] if c not in ("server_id", "path")},
            )
        )
        await db.commit()
    present = {f.path for f in images}
    gone = [path for path in known if path not in present]
    for start in range(0, len(gone), 1000):
        await db.execute(
            delete(FolderImage).where(
                FolderImage.server_id == server.id,
                FolderImage.path.in_(gone[start : start + 1000]),
            )
        )
    await db.commit()
    return len(images)


VIDEO_MAX = 80 * 1024 * 1024  # bytes: bigger video covers aren't kept (or shown)


def video_folder(data_dir: Path, server_id: int) -> Path:
    return data_dir / "video-covers" / str(server_id)


def _stored_name(path: str) -> str:
    """The copy's name: from the path (a new file there replaces it), with its extension."""
    digest = hashlib.sha1(path.encode(), usedforsecurity=False).hexdigest()
    return f"{digest}{PurePosixPath(path).suffix.lower()}"


def _copy_videos(root: Path, folder: Path, items: list[Found]) -> list[dict[str, Any]]:
    folder.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for item in items:
        if item.size > VIDEO_MAX:
            out.append({"stored": None, "error": "Too big to show (over 80 MB)."})
            continue
        name = _stored_name(item.path)
        part = folder / f"{name}.part"
        try:
            shutil.copyfile(root / item.path, part)
            part.replace(folder / name)
        except OSError as exc:
            part.unlink(missing_ok=True)
            out.append({"stored": None, "error": str(exc)})
            continue
        out.append({"stored": name, "error": None})
    return out


async def scan_videos(db: AsyncSession, server: PlexServer, root: Path, videos: list[Found]) -> int:
    """Copy new and changed video covers to DATA_DIR (where the web app can stream them from);
    forget gone ones (and their copies)."""
    folder = video_folder(get_settings().data_dir, server.id)
    known = {
        v.path: v
        for v in await db.scalars(select(FolderVideo).where(FolderVideo.server_id == server.id))
    }
    changed = [
        f
        for f in videos
        if f.path not in known or (known[f.path].size, known[f.path].mtime) != (f.size, f.mtime)
    ]
    copied = await asyncio.to_thread(_copy_videos, root, folder, changed)
    for f, info in zip(changed, copied, strict=True):
        stmt = insert(FolderVideo).values(
            server_id=server.id, path=f.path, size=f.size, mtime=f.mtime, **info
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[FolderVideo.server_id, FolderVideo.path],
                set_={"size": f.size, "mtime": f.mtime, **info},
            )
        )
    present = {f.path for f in videos}
    for path, video in known.items():
        if path not in present:
            if video.stored:
                (folder / video.stored).unlink(missing_ok=True)
            await db.delete(video)
    await db.commit()
    return len(videos)


async def _scan_lyrics(
    db: AsyncSession, server: PlexServer, root: Path, songs: list[str], lyrics: set[str]
) -> int:
    """Each song's kind of lyrics: the best of its tags' and the files next to it (read on
    every scan: a .lrc added next to a song doesn't change the song). Returns how many have
    lyrics."""
    beside = await asyncio.to_thread(lyrics_beside, root, songs, lyrics)
    rows = (
        await db.execute(
            select(MediaFile.id, MediaFile.path, MediaFile.embedded_lyrics, MediaFile.lyrics).where(
                MediaFile.server_id == server.id
            )
        )
    ).all()
    changed = []
    with_lyrics = 0
    for file_id, path, embedded, current in rows:
        kind = best(beside.get(path), embedded)  # type: ignore[arg-type]
        with_lyrics += kind is not None
        if kind != current:
            changed.append({"id": file_id, "lyrics": kind})
    for start in range(0, len(changed), 1000):
        await db.execute(update(MediaFile), changed[start : start + 1000])
    await db.commit()
    return with_lyrics


def _reporter(db: AsyncSession, scan: FileScan, step: str) -> Callable[[int, int], Awaitable[None]]:
    async def report(done: int, total: int) -> None:
        await check_stop(db, FileScan, scan.id)
        scan.progress = {"step": step, "done": done, "total": total}
        scan.updated_at = dt.datetime.now(dt.UTC)
        await db.commit()

    return report


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
    except JobStopped:
        await db.rollback()
        scan = await db.get(FileScan, scan_id, populate_existing=True) or scan
        scan.status = "failed"
        scan.error = "Stopped. The files read so far are kept; the next scan reads the rest."
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
    walked = await asyncio.to_thread(walk_all, root)
    files, nfos, images, lyrics = walked.audio, walked.nfos, walked.images, walked.lyrics
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
    report = _reporter(db, scan, STEPS["files"])
    await report(0, len(files))

    changed = [f for f in files if known.get(f.path) != (f.size, f.mtime, READER)]
    stats["unchanged"] = len(files) - len(changed)
    for start in range(0, len(changed), BATCH):
        await report(stats["unchanged"] + stats["read"], len(files))
        batch = changed[start : start + BATCH]
        read = await asyncio.to_thread(_read_all, root, batch, get_settings().data_dir)
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
                "cover": info["cover"],
                "embedded_lyrics": info["embedded_lyrics"],
                "reader": READER,
                "error": info["error"],
                "rating_key": tracks.get(plex_path(server, f.path)),
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
    # The steps after the tags (on a first scan, the pictures can take a while).
    await _reporter(db, scan, STEPS["lyrics"])(0, 0)
    stats["lyrics"] = await _scan_lyrics(db, server, root, [f.path for f in files], lyrics)
    stats["nfo"] = await _scan_nfos(db, server, root, nfos, _reporter(db, scan, STEPS["nfo"]))
    stats["images"] = await _scan_images(
        db, server, root, images, _reporter(db, scan, STEPS["images"])
    )
    stats["videos"] = await scan_videos(db, server, root, walked.videos)
    await _reporter(db, scan, STEPS["matching"])(0, 0)
    unmatched = (
        await db.execute(
            select(MediaFile.id, MediaFile.path).where(
                MediaFile.server_id == server.id, MediaFile.rating_key.is_(None)
            )
        )
    ).all()
    matches = [
        {"id": file_id, "rating_key": key}
        for file_id, path in unmatched
        if (key := tracks.get(plex_path(server, path))) is not None
    ]
    for start in range(0, len(matches), 1000):
        await db.execute(update(MediaFile), matches[start : start + 1000])
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
