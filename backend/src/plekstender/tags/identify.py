"""Identifying music files by sound (worker job): fingerprint each file with fpcalc, look the
fingerprint up on AcoustID, and keep the matches on the file (`media_files.identity`).

A file is fingerprinted again only when it changed (size or modification time). Choosing a
match and writing its tags happens later, from the web app, previewed like every edit.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import acoustid
from plekstender.config import Settings
from plekstender.db.instance import get_acoustid_api_key
from plekstender.db.models import FileScan, MediaFile, PlexServer

log = logging.getLogger(__name__)

BATCH = 8  # files fingerprinted at once


class IdentifyError(Exception):
    pass


def file_key(media: MediaFile) -> str:
    return f"{media.size}:{media.mtime}"


def _inside(root: Path, relative: str) -> Path | None:
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


async def _fingerprint(settings: Settings, root: Path, media: MediaFile) -> dict[str, Any]:
    path = await asyncio.to_thread(_inside, root, media.path)
    if path is None:
        return {"error": "The file isn't in the music folder any more (scan again)."}
    try:
        fp, duration = await acoustid.fingerprint(settings.fpcalc_path, path)
    except acoustid.FingerprintError as exc:
        return {"error": str(exc)}
    return {"fingerprint": fp, "duration": duration}


async def run_identify(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, job_id: int
) -> None:
    job = await db.get(FileScan, job_id)
    if job is None or job.status != "queued" or job.kind != "identify":
        return
    now = dt.datetime.now(dt.UTC)
    job.status, job.started_at, job.updated_at = "running", now, now
    await db.commit()
    try:
        server = await db.get(PlexServer, job.server_id)
        if server is None or not server.local_path_prefix:
            raise IdentifyError("Set up the music folder first (Settings → Music files).")
        key, _ = await get_acoustid_api_key(db, settings)
        if not key:
            raise IdentifyError("Add an AcoustID application key first (Settings → Connections).")
        root = await asyncio.to_thread(Path(server.local_path_prefix).resolve)
        if not await asyncio.to_thread(root.is_dir):
            raise IdentifyError(f"{root} doesn't exist in the worker container.")
        job.stats = await _identify(db, http, settings, job, root, key)
        job.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, IdentifyError | acoustid.AcoustIdError):
            log.exception("identify job %s failed", job_id)
        await db.rollback()
        failed = await db.get(FileScan, job_id, populate_existing=True)
        if failed is None:
            return
        job = failed
        job.status = "failed"
        job.error = (
            str(exc)
            if isinstance(exc, IdentifyError | acoustid.AcoustIdError)
            else "Unexpected error; see the worker log."
        )
    finished = dt.datetime.now(dt.UTC)
    job.finished_at = finished
    job.updated_at = finished
    await db.commit()


async def _identify(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    job: FileScan,
    root: Path,
    key: str,
) -> dict[str, int]:
    ids = [int(i) for i in job.params.get("file_ids", [])]
    again = bool(job.params.get("again"))
    stats = {"files": len(ids), "identified": 0, "no_match": 0, "errors": 0, "known": 0}
    job.progress = {"done": 0, "total": len(ids)}
    await db.commit()
    done = 0
    for start in range(0, len(ids), BATCH):
        files = list(
            await db.scalars(
                select(MediaFile).where(
                    MediaFile.server_id == job.server_id,
                    MediaFile.id.in_(ids[start : start + BATCH]),
                )
            )
        )
        current = {
            f.id: f.identity
            for f in files
            if f.identity and f.identity.get("key") == file_key(f) and f.identity.get("fingerprint")
        }
        todo = [f for f in files if f.id not in current]
        prints = await asyncio.gather(*(_fingerprint(settings, root, f) for f in todo))
        for media, fp in zip(todo, prints, strict=True):
            current[media.id] = {"key": file_key(media), **fp}
        for media in files:
            identity = dict(current[media.id])
            if identity.get("error"):
                stats["errors"] += 1
            elif "results" in identity and not again:
                stats["known"] += 1
            else:
                try:
                    identity["results"] = await acoustid.lookup(
                        http,
                        settings.acoustid_api_url,
                        key,
                        identity["fingerprint"],
                        identity["duration"],
                    )
                    identity.pop("error", None)
                except acoustid.AcoustIdKeyError:
                    raise  # no point going on
                except acoustid.AcoustIdError as exc:
                    identity["error"] = str(exc)
                    stats["errors"] += 1
                else:
                    stats["identified" if identity["results"] else "no_match"] += 1
            identity["at"] = dt.datetime.now(dt.UTC).isoformat()
            media.identity = identity
        done += len(files)
        job.progress = {"done": done, "total": len(ids)}
        job.updated_at = dt.datetime.now(dt.UTC)
        await db.commit()
    return stats


# --- choosing a match ------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def _similar(a: str | None, b: str | None) -> bool:
    return bool(a and b) and _norm(a) == _norm(b)


def suggest(
    identity: dict[str, Any] | None, tags: dict[str, Any], popular: dict[str, int]
) -> tuple[str, str | None, str | None] | None:
    """The likeliest (recording, release, release track) for a file: the best match's
    recording, on the release named like the file's album, else the one most of the other
    files are on (an album identified together), else the earliest."""
    results = (identity or {}).get("results") or []
    if not results or not results[0]["recordings"]:
        return None
    recording = results[0]["recordings"][0]
    releases = recording.get("releases") or []
    if not releases:
        return recording["id"], None, None
    named = [r for r in releases if _similar(r["title"], tags.get("album"))]
    pick = (
        named or sorted(releases, key=lambda r: (-popular.get(r["id"], 0), r["date"] or "9999"))
    )[0]
    return recording["id"], pick["id"], pick["track_id"]


def find(
    identity: dict[str, Any] | None, recording_id: str, track_id: str | None
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """A recording (and one of its release tracks) among a file's matches."""
    for result in (identity or {}).get("results") or []:
        for rec in result["recordings"]:
            if rec["id"] != recording_id:
                continue
            if track_id is None:
                return rec, None
            release = next((r for r in rec["releases"] if r["track_id"] == track_id), None)
            return (rec, release) if release else None
    return None


def values(recording: dict[str, Any], release: dict[str, Any] | None) -> dict[str, Any]:
    """The tags a chosen match gives a file (the shape mbapply.plan_file takes)."""
    out: dict[str, Any] = {
        "title": recording["title"],
        "artist": recording["artist"],
        "musicbrainz_trackid": recording["id"],
        "musicbrainz_artistid": recording.get("artist_id"),
    }
    if release:
        out |= {
            "album": release["title"],
            "albumartist": release["artist"],
            "date": release["date"],
            "releasecountry": release["country"],
            "musicbrainz_albumid": release["id"],
            "musicbrainz_albumartistid": release.get("artist_id"),
            "musicbrainz_releasetrackid": release["track_id"],
        }
        if release["position"] and release["track_count"]:
            out["tracknumber"] = (release["position"], release["track_count"])
        if release["disc"]:
            out["discnumber"] = (release["disc"], release["discs"])
    return out
