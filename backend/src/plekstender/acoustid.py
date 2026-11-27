"""Identifying songs by sound: Chromaprint fingerprints (fpcalc, worker only) looked up on
AcoustID, which knows the MusicBrainz recordings (and their releases) that sound like them.

AcoustID needs an application key (free, registered by the admin) and allows three requests a
second. Requests carry a generic User-Agent; only the fingerprint and length are sent.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from plekstender import __version__

USER_AGENT = f"Plekstender/{__version__} (self-hosted music library tool)"
INTERVAL = 0.34  # seconds between lookups (AcoustID's limit is 3 a second)
LENGTH = 120  # seconds of audio fingerprinted (what AcoustID expects)
FPCALC_TIMEOUT = 120
# An AcoustID track from AcoustID's documentation, to check a key against.
CHECK_TRACK = "9ff43b6a-4f16-427c-93c2-92307ca505e0"
MIN_SCORE = 0.5
MAX_RELEASES = 40

_lock = asyncio.Lock()
_last = 0.0


class AcoustIdError(Exception):
    pass


class AcoustIdKeyError(AcoustIdError):
    pass


class FingerprintError(Exception):
    pass


async def fingerprint(fpcalc: str, path: Path) -> tuple[str, int]:
    """The file's Chromaprint fingerprint and its length in seconds."""
    data = await _fpcalc(fpcalc, path)
    try:
        return str(data["fingerprint"]), round(float(data["duration"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise FingerprintError("fpcalc gave no fingerprint.") from exc


async def raw_fingerprint(fpcalc: str, path: Path) -> list[int]:
    """The file's fingerprint as Chromaprint's 32-bit numbers (one about every 0.12 s), to
    compare files with each other."""
    data = await _fpcalc(fpcalc, path, "-raw")
    values = data.get("fingerprint") if isinstance(data, dict) else None
    if not isinstance(values, list) or not values:
        raise FingerprintError("fpcalc gave no fingerprint.")
    try:
        return [int(v) & 0xFFFFFFFF for v in values]
    except (TypeError, ValueError) as exc:
        raise FingerprintError("fpcalc gave no fingerprint.") from exc


async def _fpcalc(fpcalc: str, path: Path, *options: str) -> Any:
    try:
        proc = await asyncio.create_subprocess_exec(
            fpcalc, "-json", *options, "-length", str(LENGTH), str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
    except OSError as exc:
        raise FingerprintError(
            f"fpcalc can't be run ({exc}); it comes with the Docker image, or set FPCALC_PATH."
        ) from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), FPCALC_TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise FingerprintError("fpcalc took too long.") from exc
    if proc.returncode != 0:
        message = err.decode(errors="replace").strip().splitlines()
        raise FingerprintError(message[-1] if message else f"fpcalc failed ({proc.returncode}).")
    try:
        return json.loads(out)
    except ValueError as exc:
        raise FingerprintError("fpcalc gave no fingerprint.") from exc


async def _request(http: httpx.AsyncClient, url: str, data: dict[str, str]) -> dict[str, Any]:
    global _last
    async with _lock:
        wait = _last + INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            resp = await http.post(
                f"{url}/lookup", data=data, headers={"User-Agent": USER_AGENT}, timeout=30
            )
        except httpx.HTTPError as exc:
            raise AcoustIdError(f"AcoustID can't be reached: {exc}") from exc
        finally:
            _last = time.monotonic()
    try:
        body: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise AcoustIdError(f"AcoustID answered {resp.status_code}.") from exc
    if body.get("status") != "ok":
        error = body.get("error") or {}
        if error.get("code") == 4:
            raise AcoustIdKeyError("AcoustID doesn't accept this application key.")
        raise AcoustIdError(error.get("message") or f"AcoustID answered {resp.status_code}.")
    return body


async def check_key(http: httpx.AsyncClient, url: str, key: str) -> None:
    await _request(http, url, {"client": key, "trackid": CHECK_TRACK})


async def lookup(
    http: httpx.AsyncClient, url: str, key: str, fp: str, duration: int
) -> list[dict[str, Any]]:
    """The recordings that sound like this (best first), with the releases they're on."""
    body = await _request(
        http,
        url,
        {
            "client": key,
            "duration": str(duration),
            "fingerprint": fp,
            "meta": "recordings releases tracks",
        },
    )
    return condense(body.get("results") or [])


def _seconds(value: object) -> int | None:
    """A recording's length in whole seconds (AcoustID gives some with a fraction)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return round(value)
    return None


def _credit(artists: list[dict[str, Any]] | None) -> str:
    return "".join(f"{a.get('name', '')}{a.get('joinphrase', '')}" for a in artists or []).strip()


def _first_id(artists: list[dict[str, Any]] | None) -> str | None:
    return next((str(a["id"]) for a in artists or [] if a.get("id")), None)


def _date(value: dict[str, Any] | None) -> str | None:
    if not value or not value.get("year"):
        return None
    parts = [f"{value['year']:04d}"]
    for key in ("month", "day"):
        if not value.get(key):
            break
        parts.append(f"{value[key]:02d}")
    return "-".join(parts)


def condense(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """AcoustID's answer, trimmed to what choosing and writing tags needs."""
    out = []
    for result in sorted(results, key=lambda r: -float(r.get("score") or 0))[:3]:
        score = float(result.get("score") or 0)
        if score < MIN_SCORE:
            continue
        recordings = []
        for rec in result.get("recordings") or []:
            if not rec.get("title"):
                continue  # a recording AcoustID knows only by ID
            releases = []
            for rel in rec.get("releases") or []:
                for medium in rel.get("mediums") or []:
                    for track in medium.get("tracks") or []:
                        releases.append(
                            {
                                "id": rel.get("id"),
                                "title": rel.get("title") or "",
                                "artist": _credit(rel.get("artists"))
                                or _credit(rec.get("artists")),
                                "artist_id": _first_id(rel.get("artists")),
                                "date": _date(rel.get("date")),
                                "country": rel.get("country"),
                                "format": medium.get("format"),
                                "disc": int(medium.get("position") or 1),
                                "discs": int(rel.get("medium_count") or 1),
                                "track_count": int(medium.get("track_count") or 0),
                                "position": int(track.get("position") or 0),
                                "track_id": track.get("id"),
                            }
                        )
            releases.sort(key=lambda r: r["date"] or "9999")
            recordings.append(
                {
                    "id": rec["id"],
                    "title": rec["title"],
                    "artist": _credit(rec.get("artists")),
                    "artist_id": _first_id(rec.get("artists")),
                    "duration": _seconds(rec.get("duration")),
                    "releases": releases[:MAX_RELEASES],
                }
            )
        if recordings:
            out.append(
                {
                    "acoustid": result.get("id"),
                    "score": round(score, 3),
                    "recordings": recordings[:5],
                }
            )
    return out
