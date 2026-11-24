"""LRCLIB (lrclib.net): free, open lyrics, many of them synced to the music. No key needed.

LRCLIB asks for no more than about 90 requests a minute from an app that names itself in its
User-Agent, so requests wait their turn (one every 0.7 s, shared by all lookups).

Every answer has a Lyricsfile (YAML, possibly word-synced) as well as LRC and plain text.

A song is looked up by artist, title, album and length (LRCLIB matches the length to within
a couple of seconds); when that finds nothing, by a search on artist and title, taking a
result about as long as the file. Requests carry a generic User-Agent, nothing about the
person using Plekstender.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from plekstender import __version__

USER_AGENT = f"Plekstender/{__version__} (self-hosted music library tool)"
LENGTH_SLACK = 3  # seconds a search result's length may differ from the file's
MAX_LYRICS = 100_000  # characters: anything longer isn't lyrics
MAX_LYRICSFILE = 1_000_000  # word timings make these bigger
INTERVAL = 0.7  # seconds between requests (LRCLIB's limit for identified apps)

_lock = asyncio.Lock()
_last = 0.0


class LrclibError(Exception):
    """LRCLIB couldn't be reached, or answered with an error (not "not found")."""


@dataclass
class Found:
    id: int | None
    synced: str | None
    plain: str | None
    instrumental: bool
    lyricsfile: str | None = None
    word_synced: bool = False


def _found(data: dict[str, Any]) -> Found | None:
    synced = (data.get("syncedLyrics") or "").strip()[:MAX_LYRICS] or None
    plain = (data.get("plainLyrics") or "").strip()[:MAX_LYRICS] or None
    instrumental = bool(data.get("instrumental"))
    lyricsfile = (data.get("lyricsfile") or "").strip()[:MAX_LYRICSFILE] or None
    if not (synced or plain or instrumental):
        return None
    raw_id = data.get("id")
    return Found(
        id=raw_id if isinstance(raw_id, int) else None,
        synced=synced,
        plain=plain,
        instrumental=instrumental and not (synced or plain),
        lyricsfile=lyricsfile,
        word_synced=bool(data.get("hasWordSync")) and lyricsfile is not None,
    )


async def _get(http: httpx.AsyncClient, base: str, path: str, params: dict[str, str]) -> Any | None:
    """JSON from LRCLIB; None for "not found". Retries once after a pause when it's busy."""
    global _last
    for attempt in range(2):
        async with _lock:
            wait = _last + INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            _last = time.monotonic()
        try:
            resp = await http.get(
                f"{base.rstrip('/')}{path}",
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise LrclibError(f"LRCLIB can't be reached: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code in (429, 502, 503, 504) and attempt == 0:
            await asyncio.sleep(5)
            continue
        if resp.status_code >= 400:
            raise LrclibError(f"LRCLIB answered {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise LrclibError("LRCLIB's answer wasn't readable.") from exc
    raise LrclibError("LRCLIB is busy; try again later.")


async def find(
    http: httpx.AsyncClient,
    base: str,
    *,
    artist: str,
    title: str,
    album: str | None,
    duration_ms: int | None,
) -> Found | None:
    """A song's lyrics, or None when LRCLIB has none."""
    seconds = round(duration_ms / 1000) if duration_ms else None
    if seconds:
        params = {"artist_name": artist, "track_name": title, "duration": str(seconds)}
        if album:
            params["album_name"] = album
        data = await _get(http, base, "/api/get", params)
        if isinstance(data, dict) and (found := _found(data)) is not None:
            return found
    results = await _get(http, base, "/api/search", {"artist_name": artist, "track_name": title})
    if not isinstance(results, list):
        return None
    candidates = [r for r in results if isinstance(r, dict)]
    if seconds:
        candidates = [
            r
            for r in candidates
            if isinstance(r.get("duration"), int | float)
            and abs(float(r["duration"]) - seconds) <= LENGTH_SLACK
        ]
    # Synced lyrics first, then plain ones.
    candidates.sort(key=lambda r: (not r.get("syncedLyrics"), not r.get("plainLyrics")))
    for r in candidates:
        if (found := _found(r)) is not None:
            return found
    return None
