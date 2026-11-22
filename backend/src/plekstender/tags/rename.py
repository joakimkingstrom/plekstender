"""Renaming and moving music files into folders from a template (worker only).

A template like `{albumartist}/{album}/[{disc}-][{track:02} - ]{title}` gives each song its
path inside the music folder (the file's extension is kept). Parts in brackets are left out
when a field in them is empty, and `{disc}` is empty for one-disc albums.

Planning (a dry run) works out every move: the songs, the files that belong to them (lyrics
with the song's name; the cover, .cue, .nfo and other files of a folder whose songs all go to
one new folder), and what can't be moved (two songs would get the same name, or something is
already there). Moving never overwrites anything, and folders left empty are removed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import errno
import logging
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Album, FileScan, MediaFile, PlexServer, Track
from plekstender.tags.read import AUDIO_EXTENSIONS

log = logging.getLogger(__name__)

FIELDS = ("albumartist", "artist", "album", "year", "title", "track", "disc", "genre")
# Used when a field outside brackets is empty (a path needs a name).
PLACEHOLDERS = {
    "albumartist": "Unknown artist",
    "artist": "Unknown artist",
    "album": "Unknown album",
    "title": "Untitled",
}
MAX_FILES = 5000
MAX_PART_BYTES = 180
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TOKEN = re.compile(r"\{(\w+)(?::(0\d))?\}")


class TemplateError(ValueError):
    pass


# --- templates -------------------------------------------------------------------------------


@dataclass
class _Part:
    text: str
    optional: bool


def _parts(template: str) -> list[_Part]:
    """The template split into plain and [optional] parts, checked."""
    template = template.strip().strip("/")
    if not template:
        raise TemplateError("The template is empty.")
    if len(template) > 300:
        raise TemplateError("The template is too long.")
    parts: list[_Part] = []
    rest = template
    while rest:
        start = rest.find("[")
        if start < 0:
            parts.append(_Part(rest, False))
            break
        end = rest.find("]", start)
        if end < 0 or "[" in rest[start + 1 : end]:
            raise TemplateError("Brackets don't match: every [ needs a ] (and they don't nest).")
        if start:
            parts.append(_Part(rest[:start], False))
        parts.append(_Part(rest[start + 1 : end], True))
        rest = rest[end + 1 :]
    if any("]" in p.text for p in parts):
        raise TemplateError("Brackets don't match: every ] needs a [.")
    for part in parts:
        for match in TOKEN.finditer(part.text):
            if match.group(1) not in FIELDS:
                raise TemplateError(
                    f"{{{match.group(1)}}} isn't a field. Fields: "
                    + ", ".join(f"{{{f}}}" for f in FIELDS)
                )
        leftover = TOKEN.sub("", part.text)
        if "{" in leftover or "}" in leftover:
            raise TemplateError("Fields are written {like_this}, or {track:02} for 01, 02...")
    names = [part.text for part in parts]
    if any(segment.strip() in ("..", ".") for segment in "".join(names).split("/")):
        raise TemplateError("The template can't contain . or .. folders.")
    if "{title}" not in "".join(names).split("/")[-1]:
        raise TemplateError("Put {title} in the file name, so every song gets its own name.")
    return parts


def check_template(template: str) -> str:
    _parts(template)
    return template.strip().strip("/")


def _safe_value(value: str) -> str:
    """A field's value inside a path: slashes and other unsafe characters become _."""
    value = unicodedata.normalize("NFC", value)
    return " ".join(UNSAFE.sub("_", value).split())


def _safe_part(part: str, keep: int = 0) -> str:
    """One folder or file name: no unsafe characters, trailing dots or spaces, not too long
    (`keep` bytes are reserved for the extension)."""
    part = " ".join(UNSAFE.sub("_", part).split()).strip(" .")
    while len(part.encode()) > MAX_PART_BYTES - keep:
        part = part[:-1].rstrip(" .")
    return part or "_"


def render(template: str, values: dict[str, Any], extension: str) -> str:
    """A song's path (relative, with its extension) from the template and its values."""
    out = []
    for part in _parts(template):
        missing = False

        def fill(match: re.Match[str], optional: bool = part.optional) -> str:
            nonlocal missing
            name, pad = match.group(1), match.group(2)
            value = values.get(name)
            if value in (None, ""):
                if optional:
                    missing = True
                    return ""
                return PLACEHOLDERS.get(name, "")
            if pad and isinstance(value, int):
                return str(value).zfill(int(pad))
            return _safe_value(str(value))

        text = TOKEN.sub(fill, part.text)
        if not missing:
            out.append(text)
    segments = [s for s in "".join(out).split("/")]
    folders = [_safe_part(s) for s in segments[:-1] if s.strip(" .")]
    ext = extension.lower()
    name = _safe_part(segments[-1], keep=len(ext.encode())) + ext
    return str(PurePosixPath(*folders, name))


def values_for(tags: dict[str, Any], path: str, discs: int) -> dict[str, Any]:
    """The template's fields for a file, from its tags (and its name for a missing title)."""
    date = str(tags.get("date") or tags.get("originaldate") or "")
    genres = tags.get("genre") or []
    return {
        "albumartist": tags.get("albumartist") or tags.get("artist"),
        "artist": tags.get("artist") or tags.get("albumartist"),
        "album": tags.get("album"),
        "year": date[:4] if date[:4].isdigit() else None,
        "title": tags.get("title") or PurePosixPath(path).stem,
        "track": tags.get("track"),
        "disc": tags.get("disc") if discs > 1 and tags.get("disc") else None,
        "genre": genres[0] if genres else None,
    }


# --- planning (a dry run) ---------------------------------------------------------------------


@dataclass
class Move:
    kind: str  # song | sidecar (belongs to a song) | folder (a folder's cover, .cue...)
    source: str
    target: str
    file_id: int | None = None
    rating_key: int | None = None


@dataclass
class Skip:
    path: str
    target: str | None
    reason: str


async def _files(db: AsyncSession, server_id: int, scope: str, key: int) -> list[MediaFile]:
    query = (
        select(MediaFile)
        .join(
            Track,
            (Track.server_id == MediaFile.server_id) & (Track.rating_key == MediaFile.rating_key),
        )
        .outerjoin(
            Album,
            (Album.server_id == Track.server_id) & (Album.rating_key == Track.album_rating_key),
        )
        .where(MediaFile.server_id == server_id, MediaFile.error.is_(None))
    )
    if scope == "album":
        query = query.where(Track.album_rating_key == key)
    else:
        query = query.where(or_(Track.artist_rating_key == key, Album.artist_rating_key == key))
    return list(await db.scalars(query.order_by(MediaFile.path).limit(MAX_FILES + 1)))


def _has_audio(folder: Path) -> bool:
    for _, _, names in os.walk(folder):
        if any(Path(n).suffix.lower() in AUDIO_EXTENSIONS for n in names):
            return True
    return False


def plan_moves(root: Path, songs: list[Move]) -> tuple[list[Move], list[Skip], int]:
    """Every move (songs and what goes with them), what can't be moved, and how many songs
    are already where the template puts them. Reads the folders (worker)."""
    skipped: list[Skip] = []
    unchanged = sum(1 for s in songs if s.source == s.target)
    moving = [s for s in songs if s.source != s.target]
    # Two songs to one name (compared without case: many disks don't tell case apart).
    by_target: dict[str, list[Move]] = defaultdict(list)
    for song in songs:
        by_target[song.target.casefold()].append(song)
    clash = {id(s) for group in by_target.values() if len(group) > 1 for s in group}
    ok: list[Move] = []
    for song in moving:
        target = root / song.target
        if id(song) in clash:
            skipped.append(Skip(song.source, song.target, "Another song would get this name."))
        elif target.exists() and song.target.casefold() != song.source.casefold():
            skipped.append(Skip(song.source, song.target, "Another file is already there."))
        else:
            ok.append(song)

    moves = list(ok)
    taken = {m.target.casefold() for m in moves}
    by_folder: dict[str, list[Move]] = defaultdict(list)
    for song in ok:
        by_folder[str(PurePosixPath(song.source).parent)].append(song)
    for folder, folder_songs in by_folder.items():
        directory = root / folder
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        moved = {PurePosixPath(s.source).name for s in folder_songs}
        audio_here = [e for e in entries if e.is_file() and e.suffix.lower() in AUDIO_EXTENSIONS]
        targets = {str(PurePosixPath(s.target).parent) for s in folder_songs}
        whole = folder != "." and len(targets) == 1 and all(e.name in moved for e in audio_here)
        stems = {PurePosixPath(s.source).stem: s for s in folder_songs}
        for entry in entries:
            if entry.name in moved or entry.is_symlink():
                continue
            if entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
                continue  # a song that isn't moving
            source = str(PurePosixPath(folder) / entry.name)
            owner = next(
                (
                    s
                    for stem, s in stems.items()
                    if entry.is_file() and entry.name.startswith(stem + ".")
                ),
                None,
            )
            if owner is not None:  # "Song.lrc", "Song.en.lrc": renamed with the song
                rest = entry.name[len(PurePosixPath(owner.source).stem) :]
                dest = str(PurePosixPath(owner.target).with_suffix("")) + rest
                kind = "sidecar"
            elif whole:
                if entry.is_dir() and _has_audio(entry):
                    skipped.append(Skip(source, None, "A folder with other songs in it."))
                    continue
                dest = str(PurePosixPath(next(iter(targets))) / entry.name)
                kind = "folder"
            elif folder == ".":
                continue  # the music folder's own files never move along
            else:
                skipped.append(Skip(source, None, "Its folder's songs go to different places."))
                continue
            if dest == source:
                continue
            if dest.casefold() in taken or (root / dest).exists():
                skipped.append(Skip(source, dest, "Something is already there."))
                continue
            taken.add(dest.casefold())
            moves.append(Move(kind, source, dest))
    return moves, skipped, unchanged


async def run_rename_plan(db: AsyncSession, job_id: int) -> None:
    job = await db.get(FileScan, job_id)
    if job is None or job.status != "queued" or job.kind != "rename":
        return
    now = dt.datetime.now(dt.UTC)
    job.status, job.started_at, job.updated_at = "running", now, now
    await db.commit()
    try:
        server = await db.get(PlexServer, job.server_id)
        if server is None or not server.local_path_prefix:
            raise TemplateError("Set up the music folder first (Settings → Music files).")
        root = await asyncio.to_thread(Path(server.local_path_prefix).resolve)
        template = check_template(job.params["template"])
        files = await _files(db, job.server_id, job.params["scope"], int(job.params["key"]))
        if len(files) > MAX_FILES:
            raise TemplateError(f"More than {MAX_FILES} files: rename album by album.")
        # Discs per album (by album artist and album), for {disc}.
        discs: dict[tuple[Any, Any], int] = defaultdict(int)
        for f in files:
            t = f.tags or {}
            album = (t.get("albumartist") or t.get("artist"), t.get("album"))
            discs[album] = max(discs[album], int(t.get("disc") or 1), int(t.get("disc_total") or 1))
        songs = []
        for f in files:
            t = f.tags or {}
            album = (t.get("albumartist") or t.get("artist"), t.get("album"))
            target = render(
                template, values_for(t, f.path, discs[album]), PurePosixPath(f.path).suffix
            )
            songs.append(Move("song", f.path, target, f.id, f.rating_key))
        moves, skipped, unchanged = await asyncio.to_thread(plan_moves, root, songs)
        job.stats = {
            "template": template,
            "songs": len(files),
            "unchanged": unchanged,
            "moves": [asdict(m) for m in moves],
            "skipped": [asdict(s) for s in skipped],
        }
        job.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, TemplateError):
            log.exception("rename plan %s failed", job_id)
        await db.rollback()
        failed = await db.get(FileScan, job_id, populate_existing=True)
        if failed is None:
            return
        job = failed
        job.status = "failed"
        job.error = (
            str(exc) if isinstance(exc, TemplateError) else "Unexpected error; see the worker log."
        )
    job.finished_at = job.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()


# --- moving (part of applying an edit) --------------------------------------------------------


class MoveError(Exception):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status  # conflict | failed


def _within(root: Path, relative: str) -> Path:
    path = root / relative
    parent = path.parent.resolve()
    if not parent.is_relative_to(root) or ".." in PurePosixPath(relative).parts:
        raise MoveError("failed", "That's outside the music folder.")
    return parent / path.name


def move(root: Path, source: str, target: str) -> None:
    """Move a file or folder inside the music folder, never over something else."""
    src, dst = _within(root, source), _within(root, target)
    if src.is_symlink() or not src.exists():
        raise MoveError("conflict", f"Nothing is at {source} any more (moved elsewhere?).")
    same = dst.exists() and os.path.samefile(src, dst)  # only the case of the name differs
    if dst.exists() and not same:
        raise MoveError("conflict", f"Something is already at {target}.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if same:
            temporary = src.with_name(f".{src.name}.plekstender-move")
            os.rename(src, temporary)
            os.rename(temporary, dst)
        elif src.is_file():
            try:  # link + unlink: fails rather than replacing a file that appeared meanwhile
                os.link(src, dst)
                os.unlink(src)
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise MoveError("conflict", f"Something is already at {target}.") from exc
                if dst.exists():
                    raise
                os.rename(src, dst)  # the disk can't hard-link (some network shares)
        else:
            os.rename(src, dst)
    except MoveError:
        raise
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise MoveError("failed", "Can't move between disks.") from exc
        raise MoveError("failed", f"Couldn't move it: {exc.strerror or exc}") from exc


def remove_empty_folders(root: Path, folders: set[str]) -> None:
    """Remove folders that moving left empty, and their parents up to the music folder."""
    for folder in sorted(folders, key=lambda f: -len(PurePosixPath(f).parts)):
        current = root / folder
        while current != root and current.is_relative_to(root):
            try:
                current.rmdir()  # only succeeds when empty
            except OSError:
                break
            current = current.parent
