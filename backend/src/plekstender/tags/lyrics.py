"""Lyrics for music files (server owners): looked up on LRCLIB by the worker, then written as
an edit (previewed first, undoable) next to each song, as `.lrc` (synced to the music) or
`.txt` (plain), which Plex and Plexamp read, as a Lyricsfile (`.lyricsfile.yaml`, word-synced
when LRCLIB has that), and/or into the file's tags.

A lookup job (`FileScan`, kind "lyrics") covers the whole library or some albums. For each
file it records in `track_lyrics` what LRCLIB has and which lyrics the file already has, so
the preview and the edit can be worked out without touching the music folder.

Changes use four fields: "lrc", "txt" and "lyricsfile" (the file next to the song; the
change's `path` is that file) and "lyrics" (in the song's tags). Their old and new values
are the text (None: no lyrics), checked before anything is replaced, as for tags.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import FileScan, MediaFile, PlexServer, TagChange, Track, TrackLyrics
from plekstender.tags import embedded_lyrics, lrclib
from plekstender.tags.lyrics_kinds import LYRICSFILE, SIDECARS, best, of_sidecar, of_text
from plekstender.tags.lyrics_kinds import sidecar_path as sidecar
from plekstender.tags.rename import MoveError, _within

log = logging.getLogger(__name__)

FIELDS = ("lrc", "txt", "lyricsfile", "lyrics")
MAX_ERRORS = 5  # LRCLIB errors in a row before the job stops (the rest wait for next time)
LOOK_AGAIN_AFTER = dt.timedelta(days=30)  # songs LRCLIB didn't have
MAX_TEXT = 1_000_000  # a word-synced Lyricsfile is long


@dataclass(frozen=True)
class Destinations:
    """Where lyrics are written."""

    sidecar: bool = True  # .lrc (synced) or .txt (plain) next to the song
    lyricsfile: bool = False  # .lyricsfile.yaml next to the song
    embedded: bool = False  # the plain text in the song's tags


_TIME = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
_META = re.compile(r"^\[[a-z]+:[^\]]*\]$", re.IGNORECASE)


class LookupStopped(Exception):
    pass


def normalise(text: str | None) -> str | None:
    """Lyrics as compared and written: Unix line ends, no space around them."""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned[:MAX_TEXT] or None


def without_times(lrc: str | None) -> str | None:
    """Synced (LRC) lyrics as plain text: times and [ar:...] header lines removed."""
    lines = []
    for line in (lrc or "").splitlines():
        if _META.match(line.strip()):
            continue
        lines.append(_TIME.sub("", line).strip())
    return normalise("\n".join(lines))


# --- looking up (worker) ---------------------------------------------------------------------


def _read_text(root: Path, relative: str) -> str | None:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return normalise(path.read_text("utf-8", errors="replace"))
    except OSError:
        return None


def _existing(root: Path, media_path: str) -> dict[str, Any]:
    """The lyrics a song has (next to it and in its tags), and whether its tags can hold some."""
    path = root / media_path
    try:
        embedded = normalise(embedded_lyrics.read(path))
    except embedded_lyrics.LyricsError:
        embedded = None
    return {
        "existing_lrc": _read_text(root, sidecar(media_path, ".lrc")),
        "existing_txt": _read_text(root, sidecar(media_path, ".txt")),
        "existing_lyricsfile": _read_text(root, sidecar(media_path, LYRICSFILE)),
        "existing_embedded": embedded,
        "embeddable": embedded_lyrics.embeddable(path),
    }


def lyrics_kind(root: Path, media_path: str) -> tuple[str | None, str | None]:
    """(the best kind of lyrics a song has anywhere, the kind in its tags) - worker."""
    try:
        embedded = of_text(embedded_lyrics.read(root / media_path))
    except embedded_lyrics.LyricsError:
        embedded = None
    beside = [
        of_sidecar(root / sidecar(media_path, suffix))
        for suffix in SIDECARS
        if (root / sidecar(media_path, suffix)).is_file()
    ]
    return best(embedded, *beside), embedded


async def _scope(db: AsyncSession, server_id: int, params: dict[str, Any]) -> list[MediaFile]:
    query = select(MediaFile).where(MediaFile.server_id == server_id)
    albums = [int(k) for k in params.get("albums") or []]
    if albums:
        query = query.join(
            Track,
            (Track.server_id == MediaFile.server_id) & (Track.rating_key == MediaFile.rating_key),
        ).where(Track.album_rating_key.in_(albums))
    return list(await db.scalars(query.order_by(MediaFile.path)))


def _due(row: tuple[str, dt.datetime] | None, again: bool, now: dt.datetime) -> bool:
    if row is None or again:
        return True
    status, at = row
    if status == "error":
        return True
    return status in ("none", "untagged") and now - at > LOOK_AGAIN_AFTER


async def run_lookup(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, job_id: int
) -> None:
    job = await db.get(FileScan, job_id)
    if job is None or job.status != "queued" or job.kind != "lyrics":
        return
    now = dt.datetime.now(dt.UTC)
    job.status, job.started_at, job.updated_at = "running", now, now
    await db.commit()
    try:
        server = await db.get(PlexServer, job.server_id)
        if server is None or not server.local_path_prefix:
            raise LookupStopped("Set up the music folder first (Settings → Music files).")
        root = await asyncio.to_thread(Path(server.local_path_prefix).resolve)
        files = await _scope(db, job.server_id, job.params)
        # By server, not by the files' ids: a library can have more files than a query can
        # take parameters (65,535).
        known = {
            file_id: (status, at)
            for file_id, status, at in (
                await db.execute(
                    select(
                        TrackLyrics.media_file_id, TrackLyrics.status, TrackLyrics.looked_up_at
                    ).where(TrackLyrics.server_id == job.server_id)
                )
            ).all()
        }
        again = bool(job.params.get("again"))
        todo = [f for f in files if _due(known.get(f.id), again, now)]
        job.progress = {"done": 0, "total": len(todo)}
        await db.commit()
        errors = 0
        for done, media in enumerate(todo, 1):
            row, failed = await _look_up(http, settings, root, media)
            errors = errors + 1 if failed else 0
            stmt = insert(TrackLyrics).values(row)
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["media_file_id"],
                    set_={k: stmt.excluded[k] for k in row if k != "media_file_id"},
                )
            )
            if errors >= MAX_ERRORS:
                raise LookupStopped(
                    f"Stopped: {row['error']} Songs looked up so far are kept; "
                    "look again later for the rest."
                )
            if done % 20 == 0 or done == len(todo):
                job.progress = {"done": done, "total": len(todo)}
                job.updated_at = dt.datetime.now(dt.UTC)
                await db.commit()
        job.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, LookupStopped):
            log.exception("lyrics lookup %s failed", job_id)
        try:
            await db.commit()  # keep what was found
        except Exception:
            await db.rollback()  # the database refused something: what was saved is kept
        failed_job = await db.get(FileScan, job_id, populate_existing=True)
        if failed_job is None:
            return
        job = failed_job
        job.status = "failed"
        job.error = (
            str(exc) if isinstance(exc, LookupStopped) else "Unexpected error; see the worker log."
        )
    job.finished_at = job.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()


async def _look_up(
    http: httpx.AsyncClient, settings: Settings, root: Path, media: MediaFile
) -> tuple[dict[str, Any], bool]:
    """One file's row for `track_lyrics`, and whether LRCLIB failed."""
    row: dict[str, Any] = {
        "media_file_id": media.id,
        "server_id": media.server_id,
        "synced": None,
        "plain": None,
        "lyricsfile": None,
        "word_synced": False,
        "lrclib_id": None,
        "error": None,
        "looked_up_at": dt.datetime.now(dt.UTC),
    }
    row |= await asyncio.to_thread(_existing, root, media.path)
    tags = media.tags or {}
    artist = tags.get("artist") or tags.get("albumartist")
    title = tags.get("title")
    if not artist or not title:
        return row | {"status": "untagged"}, False
    try:
        found = await lrclib.find(
            http,
            settings.lrclib_url,
            artist=str(artist),
            title=str(title),
            album=tags.get("album"),
            duration_ms=media.duration_ms,
        )
    except lrclib.LrclibError as exc:
        return row | {"status": "error", "error": str(exc)}, True
    if found is None:
        return row | {"status": "none"}, False
    status = "synced" if found.synced else "plain" if found.plain else "instrumental"
    return row | {
        "status": status,
        "synced": normalise(found.synced),
        # Plain text too (from the synced lyrics when that's all there is), for the tags.
        "plain": normalise(found.plain) or without_times(found.synced),
        "lyricsfile": normalise(found.lyricsfile),
        "word_synced": found.word_synced,
        "lrclib_id": found.id,
    }, False


# --- planning the edit (web app, in the database) ------------------------------------------

# The files a job covers: the server's, or some albums'.
_SCOPE = """
    m.server_id = :server_id
    AND (cardinality(CAST(:albums AS bigint[])) = 0 OR m.rating_key IN (
        SELECT t.rating_key FROM tracks t
        WHERE t.server_id = :server_id AND t.album_rating_key = ANY(CAST(:albums AS bigint[]))
    ))
"""


def _sidecar_sql(suffix: str) -> str:
    """sidecar() in SQL: the song's path with its last extension replaced."""
    return f"regexp_replace(m.path, '\\.[^./]*$', '') || '{suffix}'"


# Per field: when it's written, what it held, what it gets, and the file changed. Synced
# lyrics go in a .lrc; plain ones in a .txt (not when there's a .lrc already); the plain text
# (also kept for synced lyrics) in the tags.
_WANT: dict[str, tuple[str, str, str, str]] = {
    "lrc": (
        "(:sidecar AND l.synced IS NOT NULL"
        " AND (l.existing_lrc IS NULL OR (:overwrite AND l.existing_lrc <> l.synced)))",
        "l.existing_lrc",
        "l.synced",
        _sidecar_sql(".lrc"),
    ),
    "txt": (
        "(:sidecar AND l.synced IS NULL AND l.plain IS NOT NULL AND l.existing_lrc IS NULL"
        " AND (l.existing_txt IS NULL OR (:overwrite AND l.existing_txt <> l.plain)))",
        "l.existing_txt",
        "l.plain",
        _sidecar_sql(".txt"),
    ),
    "lyricsfile": (
        "(:lyricsfile AND l.lyricsfile IS NOT NULL AND (l.existing_lyricsfile IS NULL"
        " OR (:overwrite AND l.existing_lyricsfile <> l.lyricsfile)))",
        "l.existing_lyricsfile",
        "l.lyricsfile",
        _sidecar_sql(LYRICSFILE),
    ),
    "lyrics": (
        "(:embedded AND l.embeddable AND l.plain IS NOT NULL"
        " AND (l.existing_embedded IS NULL OR (:overwrite AND l.existing_embedded <> l.plain)))",
        "l.existing_embedded",
        "l.plain",
        "m.path",
    ),
}
_ANY = " OR ".join(want for want, *_ in _WANT.values())

_SUMMARY = text(
    f"""
    SELECT count(*) AS files,
           count(l.media_file_id) AS looked_up,
           count(*) FILTER (WHERE l.status = 'synced') AS synced,
           count(*) FILTER (WHERE l.status = 'synced' AND l.word_synced) AS word_synced,
           count(*) FILTER (WHERE l.status = 'plain') AS plain,
           count(*) FILTER (WHERE l.status = 'instrumental') AS instrumental,
           count(*) FILTER (WHERE l.status = 'none') AS none,
           count(*) FILTER (WHERE l.status = 'untagged') AS untagged,
           count(*) FILTER (WHERE l.status = 'error') AS errors,
           count(*) FILTER (
               WHERE l.existing_lrc IS NOT NULL OR l.existing_txt IS NOT NULL
                  OR l.existing_lyricsfile IS NOT NULL OR l.existing_embedded IS NOT NULL
           ) AS had_lyrics,
           count(*) FILTER (WHERE {_WANT["lrc"][0]}) AS lrc,
           count(*) FILTER (WHERE {_WANT["txt"][0]}) AS txt,
           count(*) FILTER (WHERE {_WANT["lyricsfile"][0]}) AS lyricsfile,
           count(*) FILTER (WHERE {_WANT["lyrics"][0]}) AS lyrics,
           count(*) FILTER (WHERE {_ANY}) AS songs
    FROM media_files m
    LEFT JOIN track_lyrics l ON l.media_file_id = m.id
    WHERE {_SCOPE}
    """  # noqa: S608 - constant SQL fragments
)

_SAMPLE = text(
    f"""
    SELECT m.path, l.status,
           {_WANT["lrc"][0]} AS lrc, {_WANT["txt"][0]} AS txt,
           {_WANT["lyricsfile"][0]} AS lyricsfile, {_WANT["lyrics"][0]} AS lyrics,
           (l.existing_lrc IS NOT NULL OR l.existing_txt IS NOT NULL
            OR l.existing_lyricsfile IS NOT NULL OR l.existing_embedded IS NOT NULL) AS had_lyrics,
           l.word_synced,
           left(l.plain, 200) AS preview
    FROM media_files m
    JOIN track_lyrics l ON l.media_file_id = m.id
    WHERE {_SCOPE} AND ({_ANY})
    ORDER BY m.path
    LIMIT :limit
    """  # noqa: S608 - constant SQL fragments
)


def _insert(field: str) -> Any:
    want, old, new, path = _WANT[field]
    return text(
        f"""
        INSERT INTO tag_changes
            (change_set_id, media_file_id, path, rating_key, field, old_value, new_value, status)
        SELECT :set_id, m.id, {path}, m.rating_key, '{field}', to_jsonb({old}), to_jsonb({new}),
               'pending'
        FROM media_files m
        JOIN track_lyrics l ON l.media_file_id = m.id
        WHERE {_SCOPE} AND {want}
        ORDER BY m.path
        """  # noqa: S608 - constant SQL fragments
    )


def _params(server_id: int, albums: list[int], to: Destinations, overwrite: bool) -> dict[str, Any]:
    return {
        "server_id": server_id,
        "albums": albums,
        "sidecar": to.sidecar,
        "lyricsfile": to.lyricsfile,
        "embedded": to.embedded,
        "overwrite": overwrite,
    }


async def summary(
    db: AsyncSession, server_id: int, albums: list[int], to: Destinations, overwrite: bool
) -> tuple[dict[str, int], list[Any]]:
    """What was found for a job's files, what writing it would change (counts), and the
    first changes."""
    params = _params(server_id, albums, to, overwrite)
    counts = (await db.execute(_SUMMARY, params)).one()._asdict()
    sample = (await db.execute(_SAMPLE, params | {"limit": 100})).all()
    return {k: int(v or 0) for k, v in counts.items()}, list(sample)


async def add_changes(
    db: AsyncSession,
    set_id: int,
    server_id: int,
    albums: list[int],
    to: Destinations,
    overwrite: bool,
) -> int:
    """Record the edit's changes (straight from the found lyrics). Returns how many."""
    params = _params(server_id, albums, to, overwrite) | {"set_id": set_id}
    added = 0
    for field in _WANT:
        result = await db.execute(_insert(field), params)
        added += int(getattr(result, "rowcount", 0) or 0)
    return added


# --- applying (worker) -----------------------------------------------------------------------


def write_sidecar(root: Path, relative: str, expected: str | None, new: str | None) -> None:
    """Write (or remove) the lyrics file next to a song, if it still holds what's expected."""
    target = _within(root, relative)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise embedded_lyrics.LyricsError("Something other than a lyrics file is there.")
    if not target.parent.is_dir():
        raise embedded_lyrics.LyricsError("The song's folder isn't there any more.")
    current = _read_text(root, relative) if target.exists() else None
    if current != normalise(expected):
        raise embedded_lyrics.LyricsConflict(
            f"Left as it was: {PurePosixPath(relative).name} changed since it was looked at."
        )
    text = normalise(new)
    if text is None:
        target.unlink(missing_ok=True)
        return
    temporary = target.with_name(f".{target.name}.plekstender")
    temporary.write_text(text + "\n", "utf-8")
    os.replace(temporary, target)


async def apply_lyrics(
    db: AsyncSession,
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
    for n, change in enumerate(changes, 1):
        try:
            if change.field == "lyrics":
                media = (
                    await db.get(MediaFile, change.media_file_id) if change.media_file_id else None
                )
                if media is None:
                    raise embedded_lyrics.LyricsError(
                        "The file isn't in the music folder any more."
                    )
                path = _within(root, media.path)
                await asyncio.to_thread(
                    embedded_lyrics.write, path, change.old_value, change.new_value
                )
                st = await asyncio.to_thread(path.stat)
                media.size, media.mtime = st.st_size, st.st_mtime  # no need to re-read it
                folder = str(PurePosixPath(media.path).parent)
            else:
                await asyncio.to_thread(
                    write_sidecar, root, change.path, change.old_value, change.new_value
                )
                folder = str(PurePosixPath(change.path).parent)
            change.status, change.error = "applied", None
            touched.add((change.rating_key, folder))
            if change.media_file_id is not None:
                column = {
                    "lrc": "existing_lrc",
                    "txt": "existing_txt",
                    "lyricsfile": "existing_lyricsfile",
                    "lyrics": "existing_embedded",
                }
                await db.execute(
                    update(TrackLyrics)
                    .where(TrackLyrics.media_file_id == change.media_file_id)
                    .values({column[change.field]: normalise(change.new_value)})
                )
        except embedded_lyrics.LyricsConflict as exc:
            change.status, change.error = "conflict", str(exc)
        except (embedded_lyrics.LyricsError, MoveError, OSError) as exc:
            change.status, change.error = "failed", f"Couldn't write it: {exc}"
        if n % 50 == 0:
            await db.commit()
    # What each song has now (Library -> Lyrics counts them).
    ids = sorted({c.media_file_id for c in changes if c.status == "applied" and c.media_file_id})
    for start in range(0, len(ids), 1000):
        batch = ids[start : start + 1000]
        for media in await db.scalars(select(MediaFile).where(MediaFile.id.in_(batch))):
            media.lyrics, media.embedded_lyrics = await asyncio.to_thread(
                lyrics_kind, root, media.path
            )
        await db.commit()
