"""Reading a music file's tags into one shape, whatever the format (via mutagen)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from plekstender.tags import keys as _keys  # noqa: F401 - registers MP4 keys

AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".oga", ".opus",
    ".wav", ".aif", ".aiff", ".wv", ".ape", ".wma", ".dsf",
}  # fmt: skip

# Easy-interface keys (the same for ID3, MP4 and Vorbis comments) and how they're kept.
TEXT_FIELDS = (
    "title", "artist", "album", "albumartist", "date", "originaldate", "composer",
    "tracknumber", "discnumber", "musicbrainz_trackid", "musicbrainz_albumid",
    "musicbrainz_artistid", "musicbrainz_albumartistid", "musicbrainz_releasegroupid",
    "musicbrainz_releasetrackid", "releasecountry", "rating", "label", "catalognumber",
    "barcode",
)  # fmt: skip
LIST_FIELDS = ("genre",)
# Bumped when more fields are read: the next scan re-reads every file once.
READER = 6
# The reader version that started describing embedded covers.
COVERS_READER = 4
# The fields each reader version added: a file read by an older reader may have them without
# Plekstender knowing (until it's read again).
ADDED = {
    2: frozenset({"musicbrainz_releasetrackid", "releasecountry"}),
    3: frozenset({"rating"}),
    4: frozenset(),  # embedded covers described (not a tag)
    5: frozenset({"label", "catalognumber", "barcode"}),  # and MP4 composers
    6: frozenset(),  # the kind of lyrics in the tags (not a tag field)
}
# The reader version that started noting the kind of lyrics in the tags.
LYRICS_READER = 6


def unread_fields(reader: int) -> frozenset[str]:
    """Fields a file read by this reader version may hold without Plekstender knowing."""
    return frozenset().union(*(fields for version, fields in ADDED.items() if version > reader))


MAX_VALUE = 500


class TagReadError(Exception):
    pass


def _first(values: Any) -> str | None:
    if isinstance(values, list):
        values = values[0] if values else None
    if values is None:
        return None
    text = str(values).strip()
    return text[:MAX_VALUE] or None


def _number(value: str | None) -> tuple[int | None, int | None]:
    """ "3/12" -> (3, 12); "3" -> (3, None)."""
    if not value:
        return None, None
    head, _, tail = value.partition("/")
    try:
        number = int(head)
    except ValueError:
        return None, None
    try:
        total = int(tail) if tail else None
    except ValueError:
        total = None
    return number, total


def _has_cover(path: Path, audio: Any) -> bool:
    if isinstance(audio, FLAC):
        return bool(audio.pictures)
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            return bool(ID3(path).getall("APIC"))
        if suffix in {".m4a", ".mp4", ".aac", ".alac"}:
            tags = MP4(path).tags
            return bool(tags and tags.get("covr"))
    except Exception:
        return False
    return False


def read_file(path: Path) -> dict[str, Any]:
    """Format, duration, normalised tags and whether there's an embedded cover."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception as exc:  # mutagen raises many kinds for broken files
        raise TagReadError(f"Couldn't read the file: {exc}") from exc
    if audio is None:
        raise TagReadError("Not an audio format Plekstender can read tags from.")
    raw = audio.tags or {}
    tags: dict[str, Any] = {}
    for field in TEXT_FIELDS:
        try:
            value = _first(raw.get(field))
        except KeyError, ValueError:
            value = None
        if value is not None:
            tags[field] = value
    for field in LIST_FIELDS:
        try:
            values = raw.get(field) or []
        except KeyError, ValueError:
            values = []
        cleaned = [str(v).strip()[:MAX_VALUE] for v in values if str(v).strip()]
        if cleaned:
            tags[field] = cleaned
    track, track_total = _number(tags.get("tracknumber"))
    disc, disc_total = _number(tags.get("discnumber"))
    tags.update(
        {
            k: v
            for k, v in {
                "track": track,
                "track_total": track_total,
                "disc": disc,
                "disc_total": disc_total,
            }.items()
            if v is not None
        }
    )
    length = getattr(audio.info, "length", None)
    return {
        "format": path.suffix.lower().lstrip(".")[:16] or None,
        "duration_ms": round(length * 1000) if length else None,
        "tags": tags,
        "has_cover": _has_cover(path, audio),
    }
