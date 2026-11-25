"""Lyrics inside a music file's tags (worker only): ID3 USLT (MP3, AIFF, WAV), the LYRICS
Vorbis comment (FLAC, Ogg, Opus) and MP4's ©lyr (M4A).

Not through mutagen's easy interface, which has no lyrics for ID3 and MP4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mutagen
from mutagen.id3 import ID3, USLT
from mutagen.mp4 import MP4Tags

VORBIS_KEYS = ("LYRICS", "UNSYNCEDLYRICS")
MP4_KEY = "\xa9lyr"


class LyricsError(Exception):
    pass


class LyricsConflict(LyricsError):
    pass


def _open(path: Path) -> Any:
    try:
        audio = mutagen.File(path)
    except Exception as exc:  # mutagen raises many kinds for broken files
        raise LyricsError(f"Couldn't read the file: {exc}") from exc
    if audio is None:
        raise LyricsError("Not an audio format Plekstender can read.")
    return audio


def _kind(audio: Any) -> str | None:
    tags = audio.tags
    if isinstance(tags, ID3):
        return "id3"
    if isinstance(tags, MP4Tags):
        return "mp4"
    if tags is not None and hasattr(tags, "vendor"):  # Vorbis comments (FLAC, Ogg, Opus)
        return "vorbis"
    if tags is None:  # no tags yet: which kind the format would get
        name = type(audio).__name__
        if name in ("MP3", "AIFF", "WAVE"):
            return "id3"
        if name == "MP4":
            return "mp4"
        if name in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC", "OggSpeex"):
            return "vorbis"
    return None


def embeddable(path: Path) -> bool:
    try:
        return _kind(_open(path)) is not None
    except LyricsError:
        return False


def _text(value: str | None) -> str | None:
    text = (value or "").replace("\r\n", "\n").strip()
    return text or None


def read(path: Path) -> str | None:
    """The file's embedded (unsynced) lyrics, if any."""
    audio = _open(path)
    kind, tags = _kind(audio), audio.tags
    if tags is None or kind is None:
        return None
    if kind == "id3":
        frames = tags.getall("USLT")
        return _text(frames[0].text) if frames else None
    if kind == "mp4":
        values = tags.get(MP4_KEY) or []
        return _text(values[0]) if values else None
    for key in VORBIS_KEYS:
        values = tags.get(key) or []
        if values:
            return _text(values[0])
    return None


def write(path: Path, expected: str | None, new: str | None) -> None:
    """Replace the embedded lyrics, if they're still what was expected (else a conflict)."""
    if read(path) != _text(expected):
        raise LyricsConflict("The file's lyrics changed since it was looked at.")
    audio = _open(path)
    kind = _kind(audio)
    if kind is None:
        raise LyricsError("Lyrics can't be written into this kind of file.")
    if audio.tags is None:
        audio.add_tags()
    tags, text = audio.tags, _text(new)
    if kind == "id3":
        tags.delall("USLT")
        if text:
            tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
    elif kind == "mp4":
        if text:
            tags[MP4_KEY] = [text]
        elif MP4_KEY in tags:
            del tags[MP4_KEY]
    else:
        for key in VORBIS_KEYS:
            if key in tags:
                del tags[key]
        if text:
            tags["LYRICS"] = [text]
    try:
        audio.save()
    except Exception as exc:
        raise LyricsError(f"Couldn't write: {exc}") from exc
