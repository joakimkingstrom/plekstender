"""Easy-interface keys mutagen lacks, registered so every format reads and writes the same
field names: Picard's MP4 freeform atoms for some MusicBrainz and release tags, the MP4
composer atom, "label" in ID3 (the publisher frame, as Picard writes it), and "rating".

Ratings are kept as 0-100 (Plex's 0-10 times ten: 90 is 4½ stars). In FLAC, Ogg and M4A files
that's a RATING tag as is; MP3s use POPM, with the byte values MusicBee and Windows Media
Player use (whole stars 1, 64, 128, 196, 255; half stars in between).
"""

from __future__ import annotations

from typing import Any

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags
from mutagen.id3 import ID3, POPM

FREEFORM = {
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_releasetrackid": "MusicBrainz Release Track Id",
    "originaldate": "ORIGINALDATE",
    "rating": "RATING",
    "label": "LABEL",
    "catalognumber": "CATALOGNUMBER",
    "barcode": "BARCODE",
}
POPM_EMAIL = "Windows Media Player 9 Series"
# Plex's 0-10 (half stars) -> POPM's 0-255.
TO_POPM = {1: 13, 2: 1, 3: 54, 4: 64, 5: 118, 6: 128, 7: 186, 8: 196, 9: 242, 10: 255}
FROM_POPM = {v: k for k, v in TO_POPM.items()}


def popm_to_rating(byte: int) -> int | None:
    """A POPM byte as 0-10 (None: not rated)."""
    if byte <= 0:
        return None
    if byte in FROM_POPM:
        return FROM_POPM[byte]
    for top, stars in ((31, 2), (95, 4), (159, 6), (223, 8)):
        if byte <= top:
            return stars
    return 10


def _popm_get(id3: Any, key: str) -> list[str]:
    frames = id3.getall("POPM")
    frames.sort(key=lambda f: f.email != POPM_EMAIL)  # ours first
    for frame in frames:
        rating = popm_to_rating(int(frame.rating))
        if rating is not None:
            return [str(rating * 10)]
    raise KeyError(key)


def _popm_set(id3: Any, key: str, value: list[str]) -> None:
    rating = min(10, max(1, round(int(str(value[0]).strip()) / 10)))
    frames = id3.getall("POPM")
    for frame in frames:  # keep their play counts, change the rating everywhere
        frame.rating = TO_POPM[rating]
    if not frames:
        id3.add(POPM(email=POPM_EMAIL, rating=TO_POPM[rating], count=0))


def _popm_delete(id3: Any, key: str) -> None:
    id3.delall("POPM")


for _key, _name in FREEFORM.items():
    if _key not in EasyMP4Tags.Get:
        EasyMP4Tags.RegisterFreeformKey(_key, _name)
if "composer" not in EasyMP4Tags.Get:
    EasyMP4Tags.RegisterTextKey("composer", "\xa9wrt")
if "label" not in EasyID3.Get:
    EasyID3.RegisterTextKey("label", "TPUB")
if "rating" not in EasyID3.Get:
    EasyID3.RegisterKey("rating", _popm_get, _popm_set, _popm_delete)


class _EasyFrames(EasyID3):
    """The easy names over another format's own ID3 tags (AIFF, WAV, DSF...), saved the way
    that format saves them (they don't take EasyID3's `v1`)."""

    def __init__(self, id3: ID3) -> None:
        super().__init__()
        self._EasyID3__id3 = id3

    def save(self, filething: Any = None, **kwargs: Any) -> None:
        kwargs.pop("v1", None)
        self._EasyID3__id3.save(filething, **kwargs)


def easy_tags(audio: Any) -> Any:
    """Give a file's ID3 tags the easy names when mutagen hasn't: it only does for MP3s, so
    AIFF, WAV and DSF files' tags came as raw frames ("TIT2", "UFID:...") and read as
    missing (or broke a snapshot)."""
    if audio is not None and isinstance(audio.tags, ID3) and not isinstance(audio.tags, EasyID3):
        audio.tags = _EasyFrames(audio.tags)
    return audio


def open_easy(path: Any) -> Any:
    """mutagen.File(path, easy=True), with every format's ID3 tags under the easy names."""
    return easy_tags(mutagen.File(path, easy=True))
