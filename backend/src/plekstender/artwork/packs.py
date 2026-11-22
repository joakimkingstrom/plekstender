"""Logo packs: export a server's finished artist images as a zip, import one elsewhere.

A pack is a zip with `manifest.json` and one JPEG per artist. Entries carry the artist's
MusicBrainz ID, so packs can be shared between servers; names are the fallback match.
Imports are read straight from the uploaded zip (never extracted), with size checks.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import __version__
from plekstender.artwork import images
from plekstender.artwork import service as art
from plekstender.artwork.images import ImageError, Transform
from plekstender.config import Settings
from plekstender.db.models import Artist, ArtistArtwork, ArtworkJob, UserItemState, UserServer
from plekstender.plex.tv import PlexError
from plekstender.sync.parse import normalise_artist

MANIFEST = "manifest.json"
VERSION = 1
MAX_PACK = 500 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_IMAGE = 15 * 1024 * 1024
MAX_MANIFEST = 5 * 1024 * 1024
IMPORT_ID = re.compile(r"^[0-9a-f]{32}$")
# Posters are already square: keep them as they are.
AS_IS = Transform(fit=False, zoom=1.0)


class PackError(Exception):
    pass


def exports_dir(settings: Settings) -> Path:
    return settings.data_dir / "exports"


def imports_dir(settings: Settings) -> Path:
    return settings.data_dir / "imports"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "artist"


# --- export --------------------------------------------------------------------------------


async def run_export(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, job: ArtworkJob, us: UserServer
) -> None:
    """Zip the current Plex image of every artist marked done or custom."""
    from plekstender.artwork.jobs import utcnow
    from plekstender.plex.server import client_for

    client = client_for(settings, http, us)
    rows = (
        await db.execute(
            select(Artist, ArtistArtwork)
            .join(
                ArtistArtwork,
                (ArtistArtwork.server_id == Artist.server_id)
                & (ArtistArtwork.rating_key == Artist.rating_key),
            )
            .join(
                UserItemState,
                (UserItemState.user_id == us.user_id)
                & (UserItemState.server_id == Artist.server_id)
                & (UserItemState.rating_key == Artist.rating_key),
            )
            .where(
                Artist.server_id == us.server_id,
                ArtistArtwork.status.in_(("done", "custom")),
                Artist.thumb.is_not(None),
            )
            .order_by(func.lower(Artist.title))
        )
    ).all()
    folder = exports_dir(settings)
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    path = folder / f"artwork-{job.id}.zip"
    entries: list[dict[str, Any]] = []
    skipped = 0
    job.progress = {"done": 0, "total": len(rows)}
    await db.commit()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as pack:
        for n, (artist, work) in enumerate(rows, start=1):
            try:
                data, _ = await client.photo(artist.thumb or "", 1000)
                jpeg = images.to_jpeg(images.open_image(data))
            except PlexError, ImageError:
                skipped += 1
            else:
                name = f"images/{n:05d}-{_slug(artist.title)}.jpg"
                pack.writestr(name, jpeg)
                entries.append(
                    {
                        "mbid": artist.mbid or work.mbid,
                        "artist_name": artist.title,
                        "normalised_name": normalise_artist(artist.title),
                        "file": name,
                        "sha256": hashlib.sha256(jpeg).hexdigest(),
                        "source": work.source,
                    }
                )
            if n % 10 == 0 or n == len(rows):
                job.progress = {"done": n, "total": len(rows)}
                job.updated_at = utcnow()
                await db.commit()
        manifest = {
            "version": VERSION,
            "app": f"Plekstender {__version__}",
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
            "entries": entries,
        }
        pack.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
    job.result_path = str(path)
    job.stats = {"exported": len(entries), "skipped": skipped}


# --- import --------------------------------------------------------------------------------


@dataclass
class PackEntry:
    index: int
    artist_name: str
    mbid: str | None
    file: str


def read_manifest(path: Path) -> list[PackEntry]:
    """The pack's entries, after checking the zip is sane."""
    try:
        with zipfile.ZipFile(path) as pack:
            info = {i.filename: i for i in pack.infolist()}
            if MANIFEST not in info or info[MANIFEST].file_size > MAX_MANIFEST:
                raise PackError("This zip has no Plekstender manifest.json.")
            manifest = json.loads(pack.read(MANIFEST))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PackError("That file isn't a valid logo pack (zip).") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        raise PackError("Unsupported logo pack version.")
    raw = manifest.get("entries")
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise PackError("The pack's manifest has no usable entries.")
    entries = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        file, name = item.get("file"), item.get("artist_name")
        if not isinstance(file, str) or not isinstance(name, str) or file not in info:
            continue
        if info[file].file_size > MAX_IMAGE:
            continue
        mbid = item.get("mbid")
        entries.append(PackEntry(index, name[:300], mbid if isinstance(mbid, str) else None, file))
    return entries


def read_image(path: Path, file: str) -> bytes:
    with zipfile.ZipFile(path) as pack:
        info = pack.getinfo(file)
        if info.file_size > MAX_IMAGE:
            raise PackError("Image too large.")
        return pack.read(info)


def pack_path(settings: Settings, import_id: str) -> Path:
    if not IMPORT_ID.match(import_id):
        raise PackError("Unknown import.")
    path = imports_dir(settings) / f"{import_id}.zip"
    if not path.exists():
        raise PackError("This import has expired; upload the pack again.")
    return path


@dataclass
class Match:
    entry: PackEntry
    artist: Artist | None
    matched_by: str | None  # mbid | name
    status: str | None


async def match_entries(db: AsyncSession, us: UserServer, entries: list[PackEntry]) -> list[Match]:
    """Match pack entries to the user's visible artists: by MBID, then by normalised name."""
    rows = (
        await db.execute(
            select(Artist, ArtistArtwork)
            .join(
                UserItemState,
                (UserItemState.user_id == us.user_id)
                & (UserItemState.server_id == Artist.server_id)
                & (UserItemState.rating_key == Artist.rating_key),
            )
            .outerjoin(
                ArtistArtwork,
                (ArtistArtwork.server_id == Artist.server_id)
                & (ArtistArtwork.rating_key == Artist.rating_key),
            )
            .where(Artist.server_id == us.server_id, Artist.section_key.in_(us.music_section_keys))
        )
    ).all()
    by_mbid: dict[str, tuple[Artist, ArtistArtwork | None]] = {}
    by_name: dict[str, tuple[Artist, ArtistArtwork | None]] = {}
    for artist, work in rows:
        for mbid in (artist.mbid, work.mbid if work else None):
            if mbid:
                by_mbid.setdefault(mbid.lower(), (artist, work))
        by_name.setdefault(artist.name_key or normalise_artist(artist.title), (artist, work))
    out = []
    for entry in entries:
        hit = by_mbid.get(entry.mbid.lower()) if entry.mbid else None
        how = "mbid" if hit else None
        if hit is None and (hit := by_name.get(normalise_artist(entry.artist_name))):
            how = "name"
        if hit is None:
            out.append(Match(entry, None, None, None))
        else:
            found, found_work = hit
            out.append(Match(entry, found, how, found_work.status if found_work else "none"))
    return out


async def run_import(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, job: ArtworkJob, us: UserServer
) -> None:
    """Apply the chosen pack images to their artists."""
    from plekstender.artwork.jobs import utcnow
    from plekstender.plex.server import client_for

    try:
        path = pack_path(settings, str(job.params.get("import_id", "")))
        entries = {e.index: e for e in read_manifest(path)}
    except PackError as exc:
        raise art.ArtworkError(str(exc)) from exc
    client = client_for(settings, http, us)
    items = list(job.params.get("items") or [])
    stats = {"applied": 0, "failed": 0, "folder_errors": 0, **job.stats}
    done = int(job.progress.get("done", 0))
    job.progress = {"done": done, "total": len(items)}
    await db.commit()
    for item in items[done:]:
        entry = entries.get(int(item["index"]))
        artist = await db.get(Artist, (us.server_id, int(item["rating_key"])))
        if entry is None or artist is None:
            stats["failed"] += 1
        else:
            try:
                data = await asyncio.to_thread(read_image, path, entry.file)
                upload_id = art.save_upload(settings, data)
                spec = art.SourceSpec(kind="upload", upload_id=upload_id)
                applied = await art.apply(
                    db, http, settings, client, artist, spec, AS_IS, us.user_id
                )
                applied.artwork.source = "import"
                await db.commit()
                stats["applied"] += 1
                if applied.folder_error:
                    stats["folder_errors"] += 1
            except art.ArtworkError, PackError, KeyError:
                stats["failed"] += 1
        done += 1
        job.progress = {"done": done, "total": len(items)}
        job.stats = dict(stats)
        job.updated_at = utcnow()
        await db.commit()


def remove_old_files(settings: Settings, older_than: dt.timedelta) -> int:
    cutoff = dt.datetime.now(dt.UTC).timestamp() - older_than.total_seconds()
    removed = 0
    for folder in (exports_dir(settings), imports_dir(settings)):
        if not folder.exists():
            continue
        for path in folder.glob("*.zip"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
    return removed
