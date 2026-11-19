"""Kodi-style .nfo files: artist.nfo (in an artist's folder) and album.nfo (in an album's),
as written by Kodi, MediaElch, tinyMediaManager, Lidarr and others.

They're XML; only the fields Plekstender shows or uses are kept (MusicBrainz IDs, genres,
styles, moods, dates, label, biography or review). Files that aren't this kind of XML (such
as "scene" release notes, also called .nfo) are skipped.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

NAMES = {"artist.nfo": "artist", "album.nfo": "album"}
MAX_BYTES = 1_000_000
MAX_TEXT = 20_000
MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class NfoError(Exception):
    pass


def kind_of(name: str) -> str | None:
    return NAMES.get(name.lower())


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text[:MAX_TEXT] or None


def _children(root: ET.Element) -> dict[str, list[ET.Element]]:
    out: dict[str, list[ET.Element]] = {}
    for child in root:
        out.setdefault(child.tag.lower(), []).append(child)
    return out


def _one(found: dict[str, list[ET.Element]], *names: str) -> str | None:
    for name in names:
        for el in found.get(name, []):
            if (text := _text(el)) is not None:
                return text
    return None


def _many(found: dict[str, list[ET.Element]], name: str) -> list[str]:
    values: list[str] = []
    for el in found.get(name, []):
        text = _text(el)
        if text:
            # Some writers put several in one element: "Rock / Pop", "Rock; Pop".
            values += [v.strip() for v in re.split(r"\s*[/;|]\s*", text) if v.strip()]
    return list(dict.fromkeys(values))[:50]


def _mbid(value: str | None) -> str | None:
    found = MBID.search((value or "").lower())
    return found.group(0) if found else None


def parse(path: Path) -> tuple[str, dict[str, Any]]:
    """The file's kind ("artist" or "album") and its fields."""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise NfoError("Too big to be an .nfo file.")
        raw = path.read_bytes()
    except OSError as exc:
        raise NfoError(f"Couldn't read it: {exc.strerror or exc}") from exc
    if b"<!ENTITY" in raw or b"<!DOCTYPE" in raw:
        raise NfoError("Not a Kodi .nfo file (it declares entities).")
    try:
        # Safe: documents declaring a DOCTYPE or entities were refused above, so there are
        # no entity expansions or external references to resolve.
        root = ET.fromstring(raw)  # noqa: S314
    except ET.ParseError as exc:
        raise NfoError("Not a Kodi .nfo file (not XML).") from exc
    found = _children(root)
    common = {
        "genres": _many(found, "genre"),
        "styles": _many(found, "style"),
        "moods": _many(found, "mood"),
        "themes": _many(found, "theme"),
    }
    tag = root.tag.lower()
    if tag == "album":
        credits = [
            _mbid(_one(_children(c), "musicbrainzartistid"))
            for c in found.get("albumartistcredits", [])
        ]
        data = {
            "title": _one(found, "title"),
            "artist": _one(found, "artistdesc", "artist"),
            "year": _one(found, "year"),
            "release_date": _one(found, "releasedate"),
            "label": _one(found, "label"),
            "type": _one(found, "type", "releasetype"),
            "review": _one(found, "review"),
            "musicbrainz_albumid": _mbid(_one(found, "musicbrainzalbumid")),
            "musicbrainz_releasegroupid": _mbid(_one(found, "musicbrainzreleasegroupid")),
            "musicbrainz_albumartistids": [c for c in credits if c],
            **common,
        }
        return "album", data
    if tag == "artist":
        data = {
            "name": _one(found, "name", "title"),
            "sort_name": _one(found, "sortname"),
            "type": _one(found, "type"),
            "gender": _one(found, "gender"),
            "disambiguation": _one(found, "disambiguation"),
            "born": _one(found, "born", "formed"),
            "died": _one(found, "died", "disbanded"),
            "years_active": _one(found, "yearsactive"),
            "biography": _one(found, "biography"),
            "musicbrainz_artistid": _mbid(_one(found, "musicbrainzartistid")),
            **common,
        }
        return "artist", data
    raise NfoError(f"Not an artist or album .nfo file (<{root.tag}>).")
