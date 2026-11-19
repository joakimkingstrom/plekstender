"""Saving artist images into the artist folders (artist.jpg), next to the music.

Plex reports file paths as Plex sees them; the owner maps Plex's prefix to where the
same files are mounted in Plekstender's containers. An artist's folder is the common
parent of its albums' folders, and is only written to when it lies inside the mapped
folder and its name matches the artist, so a mapping mistake can't scatter files.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Artist, PlexServer, Track
from plekstender.sync.parse import normalise_artist

FILE_NAME = "artist.jpg"


class FolderError(Exception):
    pass


def map_path(plex_path: str, plex_prefix: str, local_prefix: str) -> Path | None:
    plex = PurePosixPath(plex_path)
    prefix = PurePosixPath(plex_prefix)
    if not plex.is_relative_to(prefix):
        return None
    return Path(local_prefix) / plex.relative_to(prefix)


def artist_plex_folder(paths: list[str]) -> PurePosixPath | None:
    """The artist folder for its track files: Artist/Album/track -> Artist."""
    album_dirs = {PurePosixPath(p).parent for p in paths if p}
    if not album_dirs:
        return None
    common = PurePosixPath(os.path.commonpath([str(d) for d in album_dirs]))
    # One album: tracks sit in Artist/Album, so the artist is one level up.
    return common.parent if len(album_dirs) == 1 else common


async def artist_folder(db: AsyncSession, server: PlexServer, artist: Artist) -> Path:
    """Where artist.jpg goes for this artist (raises FolderError when unsure)."""
    if not (server.plex_path_prefix and server.local_path_prefix):
        raise FolderError("Folder saving isn't set up for this server.")
    paths = list(
        await db.scalars(
            select(Track.file_path).where(
                Track.server_id == artist.server_id,
                Track.artist_rating_key == artist.rating_key,
                Track.file_path.is_not(None),
            )
        )
    )
    folder = artist_plex_folder([p for p in paths if p])
    if folder is None:
        raise FolderError("Plex has no file paths for this artist's tracks.")
    if normalise_artist(folder.name) != normalise_artist(artist.title):
        raise FolderError(f"The artist's folder ({folder.name}) doesn't match the artist's name.")
    local = map_path(str(folder), server.plex_path_prefix, server.local_path_prefix)
    if local is None:
        raise FolderError(f"{folder} isn't under the Plex path {server.plex_path_prefix}.")
    return await asyncio.to_thread(_within, local, Path(server.local_path_prefix))


def _within(local: Path, root: Path) -> Path:
    """`local` with symlinks resolved, refusing anything outside `root`."""
    resolved = local.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise FolderError("The artist's folder is outside the mapped folder.")
    return resolved


def is_dir(path: Path) -> bool:
    return path.is_dir()


def _write(folder: Path, data: bytes) -> Path:
    if not folder.is_dir():
        raise FolderError(f"{folder} doesn't exist in Plekstender's container (is it mounted?).")
    target = folder / FILE_NAME
    tmp = folder / f".{FILE_NAME}.plekstender"
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise FolderError(f"Couldn't write {target}: {exc.strerror}.") from exc
    return target


async def save_to_folder(db: AsyncSession, server: PlexServer, artist: Artist, jpeg: bytes) -> Path:
    folder = await artist_folder(db, server, artist)
    return await asyncio.to_thread(_write, folder, jpeg)
