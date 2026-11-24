"""Duplicate music files (server owners): the same recording kept more than once.

A worker job (`FileScan`, kind "dupes") finds candidates cheaply first: files with the same
MusicBrainz recording ID, or the same artist and title (normalised, so "(Remastered)" and
the like don't matter), and about the same length. Each candidate is then fingerprinted
(fpcalc, the first two minutes, kept in `file_fingerprints`) and compared with the others:
only files that sound the same stay together. Without fpcalc, groups are shown as
"not checked by sound".

Removing duplicates moves them (and their lyrics files) into a hidden folder in the music
folder, `.plekstender-duplicates`, keeping their paths. Plex and Plekstender's scans don't
look in hidden folders. It's an edit like renaming: undo moves them back.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import acoustid
from plekstender.config import Settings
from plekstender.db.models import FileFingerprint, FileScan, MediaFile, PlexServer
from plekstender.sync.parse import normalise_artist, normalise_title

log = logging.getLogger(__name__)

TRASH = ".plekstender-duplicates"
SAME = 0.85  # fingerprint similarity (share of matching bits) for the same recording
MAX_OFFSET = 16  # fingerprint numbers to shift by (about 2 s): files may start a bit apart
MIN_OVERLAP = 40  # numbers to compare at least (about 5 s)
LENGTH_SLACK_MS = 4000  # candidates' lengths may differ this much
MAX_GROUPS = 2000
LOSSLESS = {"flac", "wav", "aif", "aiff", "ape", "wv", "dsf", "alac"}
SIDECARS = (".lrc", ".txt")


class DuplicatesError(Exception):
    pass


def similarity(a: list[int], b: list[int], max_offset: int = MAX_OFFSET) -> float:
    """How alike two raw fingerprints are (0-1: the share of bits that match), at the best
    of a few small shifts. Different songs come out near 0.5, re-encodes above 0.9."""
    best = 0.0
    for offset in range(-max_offset, max_offset + 1):
        x, y = (a[offset:], b) if offset >= 0 else (a, b[-offset:])
        n = min(len(x), len(y))
        if n < MIN_OVERLAP:
            continue
        differ = sum(((p ^ q) & 0xFFFFFFFF).bit_count() for p, q in zip(x[:n], y[:n], strict=True))
        best = max(best, 1 - differ / (32 * n))
    return best


def _signed(values: list[int]) -> list[int]:
    return [v - (1 << 32) if v >= 1 << 31 else v for v in values]


def file_key(media: MediaFile) -> str:
    return f"{media.size}:{media.mtime}"


class _Groups:
    """Union-find over file ids."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)

    def groups(self, ids: list[int]) -> list[list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for i in ids:
            out[self.find(i)].append(i)
        return [g for g in out.values() if len(g) > 1]


def candidates(files: list[MediaFile]) -> list[list[MediaFile]]:
    """Files that might be the same recording: the same MusicBrainz recording, or artist
    and title, and lengths close together."""
    by_id = {f.id: f for f in files}
    groups = _Groups()
    seen: dict[str, int] = {}
    for f in files:
        tags = f.tags or {}
        artist = tags.get("artist") or tags.get("albumartist")
        keys = []
        if tags.get("musicbrainz_trackid"):
            keys.append(f"mb:{tags['musicbrainz_trackid']}")
        if artist and tags.get("title"):
            keys.append(
                f"name:{normalise_artist(str(artist))}|{normalise_title(str(tags['title']))}"
            )
        for key in keys:
            if key in seen:
                groups.join(f.id, seen[key])
            else:
                seen[key] = f.id
    out = []
    for ids in groups.groups(list(by_id)):
        # Split by length: songs of the same name can be different recordings (a live one).
        ordered = sorted((by_id[i] for i in ids), key=lambda f: f.duration_ms or 0)
        run = [ordered[0]]
        for f in ordered[1:]:
            if (f.duration_ms or 0) - (run[-1].duration_ms or 0) <= LENGTH_SLACK_MS:
                run.append(f)
            else:
                if len(run) > 1:
                    out.append(run)
                run = [f]
        if len(run) > 1:
            out.append(run)
    return out


def kbps(media: MediaFile) -> int | None:
    if not media.duration_ms:
        return None
    return round(media.size * 8 / media.duration_ms)


def best(files: list[MediaFile]) -> MediaFile:
    """The copy to keep: lossless first, then the higher bitrate, then one Plex knows and
    with a MusicBrainz ID, then the shorter path."""
    return max(
        files,
        key=lambda f: (
            (f.format or "") in LOSSLESS,
            kbps(f) or 0,
            f.rating_key is not None,
            bool((f.tags or {}).get("musicbrainz_trackid")),
            -len(f.path),
        ),
    )


@dataclass
class DupeFile:
    file_id: int
    path: str
    format: str | None
    size: int
    duration_ms: int | None
    kbps: int | None
    rating_key: int | None
    title: str | None
    artist: str | None
    album: str | None
    sidecars: list[str]
    keep: bool


@dataclass
class DupeGroup:
    confirmed: bool  # the files sound the same (fingerprints compared)
    similarity: float | None  # the least alike pair
    files: list[DupeFile] = field(default_factory=list)


def _sidecars(root: Path, path: str) -> list[str]:
    out = []
    for suffix in SIDECARS:
        relative = str(PurePosixPath(path).with_suffix(suffix))
        candidate = root / relative
        if candidate.is_file() and not candidate.is_symlink():
            out.append(relative)
    return out


async def _fingerprints(
    db: AsyncSession, settings: Settings, root: Path, files: list[MediaFile]
) -> tuple[dict[int, list[int]], str | None]:
    """Each file's raw fingerprint (kept ones while the file is unchanged), and the error
    when fpcalc can't be run at all."""
    stored = {
        fp.media_file_id: fp
        for fp in await db.scalars(
            select(FileFingerprint).where(FileFingerprint.media_file_id.in_([f.id for f in files]))
        )
    }
    out: dict[int, list[int]] = {}
    for media in files:
        kept = stored.get(media.id)
        if kept is not None and kept.file_key == file_key(media):
            out[media.id] = [v & 0xFFFFFFFF for v in kept.values]
            continue
        try:
            values = await acoustid.raw_fingerprint(settings.fpcalc_path, root / media.path)
        except acoustid.FingerprintError as exc:
            if "can't be run" in str(exc):
                return out, str(exc)
            log.info("no fingerprint for %s: %s", media.path, exc)
            continue
        out[media.id] = values
        row = {"media_file_id": media.id, "file_key": file_key(media), "values": _signed(values)}
        stmt = insert(FileFingerprint).values(row)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["media_file_id"],
                set_={"file_key": stmt.excluded.file_key, "values": stmt.excluded["values"]},
            )
        )
    return out, None


def _confirm(
    group: list[MediaFile], prints: dict[int, list[int]]
) -> list[tuple[list[MediaFile], float]]:
    """The files of a candidate group that sound the same, with how alike the least alike
    pair is."""
    ids = [f.id for f in group if f.id in prints]
    links = _Groups()
    scores: dict[tuple[int, int], float] = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            score = similarity(prints[a], prints[b])
            scores[(a, b)] = score
            if score >= SAME:
                links.join(a, b)
    by_id = {f.id: f for f in group}
    out = []
    for members in links.groups(ids):
        pairs = [s for (a, b), s in scores.items() if a in members and b in members]
        out.append(([by_id[i] for i in members], min(pairs) if pairs else 1.0))
    return out


async def run_duplicates(db: AsyncSession, settings: Settings, job_id: int) -> None:
    job = await db.get(FileScan, job_id)
    if job is None or job.status != "queued" or job.kind != "dupes":
        return
    now = dt.datetime.now(dt.UTC)
    job.status, job.started_at, job.updated_at = "running", now, now
    await db.commit()
    try:
        server = await db.get(PlexServer, job.server_id)
        if server is None or not server.local_path_prefix:
            raise DuplicatesError("Set up the music folder first (Settings → Music files).")
        root = await asyncio.to_thread(Path(server.local_path_prefix).resolve)
        files = list(
            await db.scalars(
                select(MediaFile).where(
                    MediaFile.server_id == job.server_id, MediaFile.error.is_(None)
                )
            )
        )
        groups = candidates([f for f in files if not f.path.startswith(f"{TRASH}/")])
        todo = [f for g in groups for f in g]
        job.progress = {"done": 0, "total": len(todo)}
        await db.commit()
        prints: dict[int, list[int]] = {}
        problem: str | None = None
        for start in range(0, len(todo), 25):
            batch = todo[start : start + 25]
            found, problem = await _fingerprints(db, settings, root, batch)
            prints.update(found)
            job.progress = {"done": min(start + 25, len(todo)), "total": len(todo)}
            job.updated_at = dt.datetime.now(dt.UTC)
            await db.commit()
            if problem:
                break
        result: list[DupeGroup] = []
        for group in groups:
            found_groups: list[tuple[list[MediaFile], float | None]]
            if problem or any(f.id not in prints for f in group):
                found_groups = [(group, None)]  # not checked by sound
            else:
                found_groups = list(_confirm(group, prints))
            for members, score in found_groups:
                keep = best(members)
                described = []
                for f in sorted(members, key=lambda f: f.path):
                    tags = f.tags or {}
                    described.append(
                        DupeFile(
                            file_id=f.id,
                            path=f.path,
                            format=f.format,
                            size=f.size,
                            duration_ms=f.duration_ms,
                            kbps=kbps(f),
                            rating_key=f.rating_key,
                            title=tags.get("title"),
                            artist=tags.get("artist") or tags.get("albumartist"),
                            album=tags.get("album"),
                            sidecars=await asyncio.to_thread(_sidecars, root, f.path),
                            keep=f.id == keep.id,
                        )
                    )
                result.append(
                    DupeGroup(
                        confirmed=score is not None,
                        similarity=round(score, 3) if score is not None else None,
                        files=described,
                    )
                )
        result.sort(key=lambda g: (not g.confirmed, g.files[0].artist or "", g.files[0].path))
        job.stats = {
            "groups": [asdict(g) for g in result[:MAX_GROUPS]],
            "total_groups": len(result),
            "files": len(files),
            "fingerprint_error": problem,
        }
        job.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, DuplicatesError):
            log.exception("duplicates job %s failed", job_id)
        await db.rollback()
        failed = await db.get(FileScan, job_id, populate_existing=True)
        if failed is None:
            return
        job = failed
        job.status = "failed"
        job.error = (
            str(exc)
            if isinstance(exc, DuplicatesError)
            else "Unexpected error; see the worker log."
        )
    job.finished_at = job.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()


def moves_for(groups: list[dict[str, Any]], remove: set[int]) -> list[dict[str, Any]]:
    """The moves that take the chosen files (and their lyrics files) out of the library.
    Raises when a group would lose every copy, or an id isn't one of the job's files."""
    known = {f["file_id"] for g in groups for f in g["files"]}
    unknown = remove - known
    if unknown:
        raise DuplicatesError("Some of those files aren't duplicates found by this check.")
    moves = []
    for g in groups:
        chosen = [f for f in g["files"] if f["file_id"] in remove]
        if not chosen:
            continue
        if len(chosen) == len(g["files"]):
            raise DuplicatesError(f"Keep at least one copy of {g['files'][0]['path']}.")
        for f in chosen:
            moves.append(
                {
                    "file_id": f["file_id"],
                    "rating_key": f["rating_key"],
                    "source": f["path"],
                    "target": f"{TRASH}/{f['path']}",
                }
            )
            for extra in f["sidecars"]:
                moves.append(
                    {
                        "file_id": None,
                        "rating_key": None,
                        "source": extra,
                        "target": f"{TRASH}/{extra}",
                    }
                )
    return moves
