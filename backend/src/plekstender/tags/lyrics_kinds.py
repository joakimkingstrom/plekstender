"""What kind of lyrics a song has: plain, synced (a time per line), word-synced (a time per
word), or an instrumental marked as such. Read from a `.lrc`, `.txt`, a Lyricsfile
(`.lyricsfile.yaml`, https://github.com/tranxuanthang/lyricsfile) or the file's tags.

LRC lines start with `[mm:ss.xx]`; "enhanced" LRC also times words with `<mm:ss.xx>`. A
Lyricsfile is YAML: `lines` (each with `start_ms`, and `words` when word-synced) and/or
`plain`, or `metadata.instrumental: true`.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

Kind = Literal["word", "synced", "plain", "instrumental"]
# Best first: a song's kind is the best one any of its lyrics have.
RANK: tuple[Kind, ...] = ("word", "synced", "plain", "instrumental")
LYRICSFILE = ".lyricsfile.yaml"
SIDECARS = (".lrc", ".txt", LYRICSFILE)
MAX_BYTES = 2_000_000  # anything bigger isn't lyrics (and isn't parsed)

_LINE_TIME = re.compile(r"^\s*\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", re.MULTILINE)
_WORD_TIME = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")


def best(*kinds: Kind | None) -> Kind | None:
    found = [k for k in kinds if k is not None]
    return min(found, key=RANK.index) if found else None


def sidecar_path(path: str, suffix: str) -> str:
    """The lyrics file next to a song: "a/01 Song.flac" + ".lyricsfile.yaml" ->
    "a/01 Song.lyricsfile.yaml"."""
    song = PurePosixPath(path)
    return str(song.with_name(song.stem + suffix))


def of_text(text: str | None) -> Kind | None:
    """LRC or plain text (a .lrc, a .txt, or lyrics in the tags)."""
    if not text or not text.strip():
        return None
    if _LINE_TIME.search(text):
        return "word" if _WORD_TIME.search(text) else "synced"
    return "plain"


class _NoAliases(yaml.SafeLoader):
    """Safe loading that refuses anchors and aliases (the spec asks writers to avoid them,
    and they're how a small YAML file grows huge)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases aren't allowed")
        return super().compose_node(parent, index)


def parse_lyricsfile(text: str) -> dict[str, Any] | None:
    """A Lyricsfile's document, or None when it isn't a readable version 1.0 one."""
    if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
        return None
    try:
        doc = yaml.load(text, Loader=_NoAliases)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict) or str(doc.get("version")) != "1.0":
        return None
    return doc


def of_lyricsfile(text: str | None) -> Kind | None:
    doc = parse_lyricsfile(text or "")
    if doc is None:
        return None
    raw_metadata = doc.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    lines = [line for line in doc.get("lines") or [] if isinstance(line, dict)]
    if any(isinstance(line.get("words"), list) and line["words"] for line in lines):
        return "word"
    if lines:
        return "synced"
    plain = doc.get("plain")
    if isinstance(plain, str) and plain.strip():
        return "plain"
    if metadata.get("instrumental") is True:
        return "instrumental"
    return None


def of_sidecar(path: Path) -> Kind | None:
    """The kind of a lyrics file next to a song (None: unreadable or empty)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return None
    if path.name.endswith(LYRICSFILE):
        return of_lyricsfile(text)
    if path.suffix.lower() == ".txt":
        return "plain" if text.strip() else None
    return of_text(text)
