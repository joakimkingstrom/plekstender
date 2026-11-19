"""Turn Plex metadata JSON into rows for the library mirror."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Any

Item = dict[str, Any]

# Plex `type` numbers for library listings.
PLEX_TYPES = {"artist": 8, "album": 9, "track": 10}

TAG_KINDS = {
    "Genre": "genre",
    "Mood": "mood",
    "Style": "style",
    "Country": "country",
    # Albums: release type (Album / EP / Single) and kind (Compilation / Live / Soundtrack).
    "Format": "format",
    "Subformat": "subformat",
}

# Bracketed or dashed suffixes that mark another release of the same recording,
# e.g. "Song (2011 Remaster)", "Song - Mono Version", "Song [Deluxe Edition Bonus Track]".
_SAME_RECORDING = (
    r"remaster(?:ed)?|mono|stereo|version|edit|deluxe|bonus|single|anniversary|expanded"
)
_SUFFIX = re.compile(
    rf"\s*(?:[\(\[][^\)\]]*\b(?:{_SAME_RECORDING})\b[^\)\]]*[\)\]]"
    rf"|\s[-\u2013]\s[^-\u2013]*\b(?:{_SAME_RECORDING})\b.*)$",
    re.IGNORECASE,
)
# Curly quotes and en/em dashes to their ASCII equivalents.
_PUNCT = str.maketrans(
    {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"}
)


def fold(text: str) -> str:
    """Case, accents, curly punctuation, "&" and spacing ignored."""
    text = unicodedata.normalize("NFKD", text.translate(_PUNCT))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", "and")
    return re.sub(r"\s+", " ", text).strip(" .!?'\"")


def normalise_title(title: str) -> str:
    """Key for grouping releases of the same song: case/accents/remaster suffixes ignored."""
    text = title.translate(_PUNCT)
    previous = None
    while previous != text:  # strip repeated suffixes: "Song (Mono) [2009 Remaster]"
        previous = text
        text = _SUFFIX.sub("", text)
    return fold(text) or title.strip().lower()


def normalise_artist(name: str) -> str:
    """Key for matching artist names across sources: "The Beatles" == "beatles"."""
    text = fold(name)
    return text.removeprefix("the ") or text or name.strip().lower()


def epoch(value: Any) -> dt.datetime | None:
    if value is None or value == "":
        return None
    try:
        return dt.datetime.fromtimestamp(int(value), dt.UTC)
    except TypeError, ValueError, OverflowError, OSError:
        return None


def to_epoch(value: dt.datetime) -> int:
    return int(value.timestamp())


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except TypeError, ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except TypeError, ValueError:
        return None


def _date(value: Any) -> dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def mbid(item: Item) -> str | None:
    for guid in item.get("Guid") or []:
        gid = str(guid.get("id", ""))
        if gid.startswith("mbid://"):
            return gid.removeprefix("mbid://")[:64]
    return None


def _common(item: Item, server_id: int, section_key: str, seen: dt.datetime) -> dict[str, Any]:
    return {
        "server_id": server_id,
        "rating_key": int(item["ratingKey"]),
        "section_key": section_key,
        "guid": item.get("guid"),
        "mbid": mbid(item),
        "title": str(item.get("title") or ""),
        "title_sort": item.get("titleSort"),
        "summary": item.get("summary") or None,
        "thumb": item.get("thumb"),
        "art": item.get("art"),
        "added_at": epoch(item.get("addedAt")),
        "plex_updated_at": epoch(item.get("updatedAt")),
        "last_seen_at": seen,
    }


def artist_row(item: Item, server_id: int, section_key: str, seen: dt.datetime) -> dict[str, Any]:
    row = _common(item, server_id, section_key, seen)
    return row | {"name_key": normalise_artist(row["title"])}


def album_row(item: Item, server_id: int, section_key: str, seen: dt.datetime) -> dict[str, Any]:
    return _common(item, server_id, section_key, seen) | {
        "artist_rating_key": _int(item.get("parentRatingKey")),
        "year": _int(item.get("year")),
        "released": _date(item.get("originallyAvailableAt")),
        "studio": item.get("studio"),
        "critic_rating": _float(item.get("rating")),
        "track_count": _int(item.get("leafCount")),
        "title_norm": normalise_title(str(item.get("title") or "")),
    }


def track_row(item: Item, server_id: int, section_key: str, seen: dt.datetime) -> dict[str, Any]:
    part: Item = {}
    media = item.get("Media") or []
    if media and media[0].get("Part"):
        part = media[0]["Part"][0]
    title = str(item.get("title") or "")
    return _common(item, server_id, section_key, seen) | {
        "album_rating_key": _int(item.get("parentRatingKey")),
        "artist_rating_key": _int(item.get("grandparentRatingKey")),
        "disc": _int(item.get("parentIndex")),
        "number": _int(item.get("index")),
        "duration_ms": _int(item.get("duration")),
        "popularity": _int(item.get("ratingCount")),
        "original_title": item.get("originalTitle"),
        "title_norm": normalise_title(title),
        "artist_key": normalise_artist(
            str(item.get("originalTitle") or item.get("grandparentTitle") or "")
        ),
        "part_key": part.get("key"),
        "file_path": part.get("file"),
    }


ROW_BUILDERS = {"artist": artist_row, "album": album_row, "track": track_row}


def state_row(
    item: Item,
    *,
    item_type: str,
    user_id: int,
    server_id: int,
    section_key: str,
    seen: dt.datetime,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "server_id": server_id,
        "rating_key": int(item["ratingKey"]),
        "item_type": item_type,
        "section_key": section_key,
        "user_rating": _float(item.get("userRating")),
        "view_count": _int(item.get("viewCount")) or 0,
        "skip_count": _int(item.get("skipCount")) or 0,
        "last_viewed_at": epoch(item.get("lastViewedAt")),
        "last_rated_at": epoch(item.get("lastRatedAt")),
        "seen_at": seen,
    }


def tags_of(item: Item) -> list[tuple[str, str]]:
    """(kind, name) pairs for genres, moods, styles, countries and release formats."""
    out: list[tuple[str, str]] = []
    for field, kind in TAG_KINDS.items():
        for tag in item.get(field) or []:
            name = str(tag.get("tag") or "").strip()
            if name:
                out.append((kind, name[:255]))
    return list(dict.fromkeys(out))


def similar_of(item: Item) -> list[str]:
    names = [str(s.get("tag") or "").strip()[:255] for s in item.get("Similar") or []]
    return list(dict.fromkeys(n for n in names if n))
