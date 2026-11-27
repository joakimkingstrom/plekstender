"""Bringing playlists in from elsewhere (Spotify, Apple Music, Tidal… via their exports):
CSV (Exportify, TuneMyMusic and most others), M3U / M3U8 and JSPF (ListenBrainz).

Each song is matched to the library the user can see on a server: the same title (ignoring
case, accents and "Remastered"-style suffixes) by one of its artists, preferring the same
album and the closest length; else a lone song of that title and about that length.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from plekstender.api.routes.library import visible
from plekstender.db.models import Album, Artist, Track, UserServer
from plekstender.sync.parse import fold, normalise_artist, normalise_title

MAX_SONGS = 5000
LENGTH_SLACK_MS = 4000


class PlaylistFileError(Exception):
    pass


@dataclass
class Entry:
    title: str
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    duration_ms: int | None = None


@dataclass
class Match:
    rating_key: int
    title: str
    artist: str | None
    album: str | None
    duration_ms: int | None
    how: Literal["exact", "title"]


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except TypeError, ValueError:
        return None


_ARTIST_SPLIT = re.compile(r"\s*(?:;|,|\s&\s|\sfeat\.?\s|\sft\.?\s|\sx\s)\s*", re.I)


def split_artists(text: str) -> list[str]:
    """ "A, B & C" → [the whole, A, B, C] (the whole first: "Simon & Garfunkel")."""
    text = " ".join(text.split())
    parts = [p for p in _ARTIST_SPLIT.split(text) if p]
    return list(dict.fromkeys([text, *parts])) if text else []


# --- CSV ----------------------------------------------------------------------------------------

TITLE = ("track name", "track", "title", "song", "song name", "name")
ARTIST = ("artist name(s)", "artist name", "artist", "artists", "creator", "performer")
ALBUM = ("album name", "album", "release")
DURATION_MS = ("duration (ms)", "duration_ms", "duration ms", "track duration (ms)")
DURATION_S = ("duration", "length", "time")
PLAYLIST_NAME = ("playlist name", "playlist")


def parse_csv(text: str) -> tuple[str | None, list[Entry]]:
    try:
        dialect: Any = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = {(h or "").strip().lower(): h for h in reader.fieldnames or []}

    def column(names: tuple[str, ...]) -> str | None:
        return next((headers[n] for n in names if n in headers), None)

    title_col, artist_col = column(TITLE), column(ARTIST)
    if not title_col or not artist_col:
        raise PlaylistFileError(
            "The CSV needs a header row with the song title and artist columns."
        )
    album_col, ms_col, s_col, name_col = (
        column(ALBUM),
        column(DURATION_MS),
        column(DURATION_S),
        column(PLAYLIST_NAME),
    )
    name, entries = None, []
    for row in reader:
        title = (row.get(title_col) or "").strip()
        artists = split_artists(row.get(artist_col) or "")
        if not title or not artists:
            continue
        duration = _int(row.get(ms_col)) if ms_col else None
        if duration is None and s_col:
            raw = (row.get(s_col) or "").strip()
            if ":" in raw:
                parts = [_int(p) or 0 for p in raw.split(":")]
                duration = sum(p * 60**i for i, p in enumerate(reversed(parts))) * 1000
            elif (seconds := _int(raw)) is not None:
                duration = seconds * 1000 if seconds < 36_000 else seconds
        if name is None and name_col:
            name = (row.get(name_col) or "").strip() or None
        album = ((row.get(album_col) or "").strip() or None) if album_col else None
        entries.append(Entry(title, artists, album, duration))
        if len(entries) >= MAX_SONGS:
            break
    return name, entries


# --- M3U ------------------------------------------------------------------------------------------

# The length, any attributes (not starting with a digit, so the two can't share digits: a
# long line without a comma would otherwise take quadratic time), then the label.
_EXTINF = re.compile(r"^#EXTINF:\s*(-?\d+)(?:[^,\d][^,]*)?,(.*)$", re.I)
_NUMBER = re.compile(r"^\d{1,3}(?:[-.]\d{1,3})?[\s.\-_]+")


def _from_path(path: str) -> Entry | None:
    path = unquote(path.strip())
    pure = PureWindowsPath(path) if "\\" in path else PurePosixPath(path)
    stem = _NUMBER.sub("", pure.stem).strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return Entry(title.strip(), split_artists(artist), None, None)
    parents = pure.parts[:-1]
    if len(parents) >= 2 and stem:  # …/Artist/Album/01 - Title.flac
        return Entry(stem, split_artists(parents[-2]), parents[-1], None)
    return None


def parse_m3u(text: str) -> tuple[str | None, list[Entry]]:
    name, entries, pending = None, [], None
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.upper().startswith("#PLAYLIST:"):
            name = line.split(":", 1)[1].strip() or None
        elif m := _EXTINF.match(line):
            seconds, label = int(m[1]), m[2].strip()
            if " - " in label:
                artist, title = label.split(" - ", 1)
                pending = Entry(
                    title.strip(),
                    split_artists(artist),
                    None,
                    seconds * 1000 if seconds > 0 else None,
                )
            else:
                pending = None
        elif not line.startswith("#"):
            entry = pending or _from_path(line)
            if entry:
                entries.append(entry)
            pending = None
            if len(entries) >= MAX_SONGS:
                break
    return name, entries


# --- JSPF -----------------------------------------------------------------------------------------


def parse_jspf(text: str) -> tuple[str | None, list[Entry]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PlaylistFileError("That isn't a JSPF (JSON) playlist.") from exc
    playlist = data.get("playlist") if isinstance(data, dict) else None
    if not isinstance(playlist, dict):
        raise PlaylistFileError('That isn\'t a JSPF playlist (no "playlist").')
    entries = []
    for track in playlist.get("track") or []:
        if not isinstance(track, dict):
            continue
        title, creator = str(track.get("title") or "").strip(), str(track.get("creator") or "")
        if title and creator:
            entries.append(
                Entry(
                    title,
                    split_artists(creator),
                    (str(track.get("album") or "").strip() or None),
                    _int(track.get("duration")),
                )
            )
        if len(entries) >= MAX_SONGS:
            break
    return (str(playlist.get("title") or "").strip() or None), entries


def parse(filename: str, text: str) -> tuple[str | None, list[Entry]]:
    """Read a playlist file of any supported kind (by its name, else its content)."""
    lower = filename.lower()
    stripped = text.lstrip("﻿ \r\n\t")
    if lower.endswith((".jspf", ".json")) or stripped.startswith("{"):
        name, entries = parse_jspf(text)
    elif lower.endswith((".m3u", ".m3u8")) or stripped.upper().startswith("#EXTM3U"):
        name, entries = parse_m3u(text)
    else:
        name, entries = parse_csv(text)
    if not entries:
        raise PlaylistFileError("No songs found in that file.")
    if name is None:
        stem = PurePosixPath(filename.replace("\\", "/")).stem.strip()
        name = stem or None
    return name, entries


# --- matching to the library ----------------------------------------------------------------------


async def match(
    db: AsyncSession, user_id: int, us: UserServer, entries: list[Entry]
) -> list[Match | None]:
    """Each entry's library song on this server (None if it isn't there)."""
    titles = {normalise_title(e.title) for e in entries}
    state, cond, where = visible(Track, user_id, us)
    album_artist = aliased(Artist)
    candidates: dict[str, list[Any]] = defaultdict(list)
    keys = sorted(titles)
    for start in range(0, len(keys), 500):
        rows = await db.execute(
            select(
                Track.rating_key,
                Track.title,
                Track.title_norm,
                Track.duration_ms,
                Track.artist_key,
                Track.original_title,
                Album.title.label("album"),
                album_artist.title.label("album_artist"),
                album_artist.name_key.label("album_artist_key"),
            )
            .join(state, cond)
            .outerjoin(
                Album,
                (Album.server_id == Track.server_id) & (Album.rating_key == Track.album_rating_key),
            )
            .outerjoin(
                album_artist,
                (album_artist.server_id == Track.server_id)
                & (album_artist.rating_key == Track.artist_rating_key),
            )
            .where(*where, Track.title_norm.in_(keys[start : start + 500]))
        )
        for row in rows.all():
            candidates[row.title_norm].append(row)

    out: list[Match | None] = []
    for entry in entries:
        found = candidates.get(normalise_title(entry.title), [])
        wanted = {normalise_artist(a) for a in entry.artists}
        by_artist = [r for r in found if wanted & {r.artist_key, r.album_artist_key}]

        def score(r: Any, entry: Entry = entry) -> tuple[int, int]:
            same_album = bool(entry.album and r.album and fold(entry.album) == fold(r.album))
            off = (
                abs((r.duration_ms or 0) - entry.duration_ms)
                if entry.duration_ms and r.duration_ms
                else 10**7
            )
            return (0 if same_album else 1, off)

        best, how = None, None
        if by_artist:
            best, how = min(by_artist, key=score), "exact"
        elif len(found) == 1 and entry.duration_ms and found[0].duration_ms:
            if abs(found[0].duration_ms - entry.duration_ms) <= LENGTH_SLACK_MS:
                best, how = found[0], "title"
        if best is None:
            out.append(None)
            continue
        out.append(
            Match(
                rating_key=best.rating_key,
                title=best.title,
                artist=best.original_title or best.album_artist,
                album=best.album,
                duration_ms=best.duration_ms,
                how=how,  # type: ignore[arg-type]
            )
        )
    return out
