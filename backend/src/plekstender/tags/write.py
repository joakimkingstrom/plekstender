"""Writing tags to a music file (worker only), safely.

All of a file's changes are written in one save. Before anything is written, each field is
compared with the value it's expected to have (the one recorded when the change was made):
if the file holds something else now, that change is a conflict and isn't written, so an
edit made elsewhere is never overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mutagen

from plekstender.tags import keys as _keys  # noqa: F401 - registers MP4 keys

# Fields that can be edited (mutagen's "easy" keys: the same for ID3, MP4 and Vorbis).
EDITABLE: tuple[str, ...] = (
    "title",
    "artist",
    "album",
    "albumartist",
    "date",
    "genre",
    "tracknumber",
    "discnumber",
    "composer",
    "originaldate",
    "releasecountry",
    "musicbrainz_trackid",
    "musicbrainz_releasetrackid",
    "musicbrainz_albumid",
    "musicbrainz_artistid",
    "musicbrainz_albumartistid",
    "musicbrainz_releasegroupid",
    "rating",  # 0-100 (see keys.py)
)
LIST_FIELDS = {"genre"}
MAX_VALUE = 500


class TagWriteError(Exception):
    pass


Value = str | list[str] | None


def clean(field: str, value: Any) -> Value:
    """A value as it's stored: trimmed text, a list for genres, None for "no tag"."""
    if field in LIST_FIELDS:
        items = value if isinstance(value, list) else ([value] if value else [])
        cleaned = [" ".join(str(v).split())[:MAX_VALUE] for v in items if str(v).strip()]
        return cleaned or None
    if value is None or (isinstance(value, list) and not value):
        return None
    text = " ".join(str(value[0] if isinstance(value, list) else value).split())[:MAX_VALUE]
    return text or None


def _open(path: Path) -> Any:
    try:
        audio = mutagen.File(path, easy=True)
    except Exception as exc:  # mutagen raises many kinds for broken files
        raise TagWriteError(f"Couldn't read the file: {exc}") from exc
    if audio is None:
        raise TagWriteError("Not an audio format Plekstender can write tags to.")
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception as exc:
            raise TagWriteError(f"This file can't hold tags: {exc}") from exc
    return audio


def current(audio: Any, field: str) -> Value:
    try:
        values = audio.tags.get(field)
    except KeyError, ValueError:
        values = None
    return clean(field, list(values) if values else None)


def snapshot(path: Path) -> dict[str, Any]:
    """Every tag the easy interface sees, as lists of text (for the first-touch record)."""
    audio = _open(path)
    out: dict[str, Any] = {}
    for key in list(audio.tags.keys()):
        try:
            out[key] = [str(v) for v in audio.tags[key]]
        except KeyError, ValueError:
            continue
    return out


@dataclass
class Planned:
    id: int
    field: str
    expected: Value
    new: Value
    # False: what the file holds wasn't known when the change was made (the file was scanned
    # before Plekstender read this field). Written whatever it holds; what it held is kept.
    known: bool = True


@dataclass
class Outcome:
    id: int
    status: str  # applied | conflict | failed
    error: str | None = None
    replaced: bool = False  # an unknown value was replaced: `found` is what the file held
    found: Value = None


def show(value: Value) -> str:
    if value is None:
        return "no value"
    return f"'{', '.join(value) if isinstance(value, list) else value}'"


def apply(path: Path, changes: list[Planned]) -> list[Outcome]:
    """Write a file's changes: those whose field still holds the expected value."""
    try:
        audio = _open(path)
    except TagWriteError as exc:
        return [Outcome(c.id, "failed", str(exc)) for c in changes]
    outcomes = []
    to_write = []
    found: dict[int, Value] = {}
    for change in changes:
        if change.field not in EDITABLE:
            outcomes.append(Outcome(change.id, "failed", f"{change.field} can't be edited."))
            continue
        now = current(audio, change.field)
        if not change.known:
            found[change.id] = now
        elif now != clean(change.field, change.expected):
            outcomes.append(
                Outcome(
                    change.id,
                    "conflict",
                    f"Left as it was: the file had {show(now)}, not "
                    f"{show(clean(change.field, change.expected))} (changed since it was scanned).",
                )
            )
            continue
        to_write.append(change)
    if not to_write:
        return outcomes
    try:
        for change in to_write:
            value = clean(change.field, change.new)
            if value is None:
                if change.field in audio.tags:
                    del audio.tags[change.field]
            else:
                audio.tags[change.field] = value if isinstance(value, list) else [value]
        audio.save()
    except Exception as exc:
        return outcomes + [Outcome(c.id, "failed", f"Couldn't write: {exc}") for c in to_write]
    return outcomes + [
        Outcome(c.id, "applied", replaced=c.id in found, found=found.get(c.id)) for c in to_write
    ]
