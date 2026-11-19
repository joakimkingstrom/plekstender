"""Applying a MusicBrainz release to an album's files: which file is which track, and which
tags would change.

Files are matched to the release's tracks by disc and track number, title and length (the
person can change any match before applying). Only tags that differ are changed: "2" already
is track 2, and a full date isn't replaced by just its year.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from plekstender.musicbrainz import Release, ReleaseTrack
from plekstender.tags.plexsync import _number
from plekstender.tags.write import clean

MB_FIELDS = (
    "title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date",
    "originaldate", "releasecountry", "musicbrainz_trackid", "musicbrainz_releasetrackid",
    "musicbrainz_albumid", "musicbrainz_artistid", "musicbrainz_albumartistid",
    "musicbrainz_releasegroupid",
)  # fmt: skip
IDS = {f for f in MB_FIELDS if f.startswith("musicbrainz_")}


@dataclass
class AlbumFile:
    id: int
    path: str
    rating_key: int | None
    tags: dict[str, Any]
    duration_ms: int | None


def _norm(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    text = re.sub(r"\s*[(\[].*?[)\]]", "", text)  # "(Remastered)", "[Live]"
    return re.sub(r"[^\w]+", " ", text).strip()


def score(f: AlbumFile, t: ReleaseTrack, one_disc: bool) -> float:
    """How likely a file is a release track: numbers, title and length."""
    s = 0.0
    if f.tags.get("track") == t.position:
        s += 2 if one_disc or (f.tags.get("disc") or 1) == t.disc else 1
    a, b = _norm(f.tags.get("title")), _norm(t.title)
    if a and b:
        s += 3 * SequenceMatcher(None, a, b).ratio()
    if f.duration_ms and t.length_ms:
        diff = abs(f.duration_ms - t.length_ms) / 1000
        s += 2 if diff <= 3 else 1 if diff <= 10 else -2 if diff > 30 else 0
    return s


def match(files: list[AlbumFile], release: Release) -> dict[int, str | None]:
    """The most likely track for each file (each track used once; None: no good match)."""
    one_disc = release.discs <= 1
    pairs = sorted(
        ((score(f, t, one_disc), f.id, t.id) for f in files for t in release.tracks),
        key=lambda p: -p[0],
    )
    out: dict[int, str | None] = {f.id: None for f in files}
    used: set[str] = set()
    for s, file_id, track_id in pairs:
        if s < 2:
            break
        if out[file_id] is None and track_id not in used:
            out[file_id] = track_id
            used.add(track_id)
    return out


def wanted(release: Release, track: ReleaseTrack) -> dict[str, Any]:
    """Every tag a release track gives a file."""
    return {
        "title": track.title,
        "artist": track.artist,
        "album": release.title,
        "albumartist": release.artist,
        "tracknumber": (track.position, release.tracks_on(track.disc)),
        "discnumber": (track.disc, release.discs),
        "date": release.date,
        "originaldate": release.original_date,
        "releasecountry": release.country,
        "musicbrainz_trackid": track.recording_id,
        "musicbrainz_releasetrackid": track.id,
        "musicbrainz_albumid": release.id,
        "musicbrainz_artistid": track.artist_id,
        "musicbrainz_albumartistid": release.artist_id,
        "musicbrainz_releasegroupid": release.release_group_id,
    }


def plan_file(tags: dict[str, Any], values: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    """The new values for one file (only the fields that differ)."""
    out: dict[str, Any] = {}
    for field in MB_FIELDS:
        if field not in fields or not values.get(field):
            continue
        old = clean(field, tags.get(field))
        if field in ("tracknumber", "discnumber"):
            number, total = values[field]
            mine, my_total = _number(old)
            if field == "discnumber" and old is None and total <= 1:
                continue  # a one-disc album needs no disc number
            if mine == number and my_total in (None, str(total)):
                continue
            out[field] = f"{number}/{total}"
            continue
        new = clean(field, values[field])
        if new is None:
            continue
        if field in ("date", "originaldate") and old and str(old).startswith(str(new)):
            continue  # "1997-05-21" already is 1997
        if field in IDS and old and str(old).lower() == str(new).lower():
            continue
        if new != old:
            out[field] = new
    return out
