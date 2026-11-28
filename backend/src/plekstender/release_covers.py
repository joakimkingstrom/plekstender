"""Release covers (Library → Releases), kept on disk under DATA_DIR/release-covers.

The Cover Art Archive's 250 and 500 px front covers of release groups, so the page doesn't
fetch them from the archive on every visit:

- the worker saves them for releases coming up and those out in the last 90 days, after each
  releases refresh (`save_recent`), a batch at a time;
- the cover endpoint serves what's saved, and fetches (and saves) any other it's asked for;
- the daily cleanup (`prune`) deletes covers of releases out more than 90 days ago.

A release group without a cover leaves a marker (`<mbid>.none`), so it isn't asked for on
every visit; it's tried again after a day (upcoming releases often get their cover late).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import musicbrainz
from plekstender.artwork import images
from plekstender.artwork.fetch import FetchError, fetch
from plekstender.artwork.images import ImageError
from plekstender.config import Settings
from plekstender.db.models import ReleaseGroup

log = logging.getLogger(__name__)

SIZES = (250, 500)
KEEP_FOR = dt.timedelta(days=90)  # after the release date
TRY_AGAIN_AFTER = 86_400.0  # seconds, for a release group that had no cover
PER_RUN = 100  # release groups the worker saves covers for in one go
PAUSE = 0.2  # seconds between them, to be gentle with the Cover Art Archive
MAX_BYTES = 5 * 1024 * 1024


def folder(settings: Settings) -> Path:
    return settings.data_dir / "release-covers"


def _path(settings: Settings, mbid: str, size: int) -> Path:
    return folder(settings) / f"{mbid}-{size}.img"


def _none(settings: Settings, mbid: str) -> Path:
    return folder(settings) / f"{mbid}.none"


def media_type(body: bytes) -> str:
    return "image/png" if body.startswith(b"\x89PNG") else "image/jpeg"


def _no_cover_lately(settings: Settings, mbid: str) -> bool:
    try:
        return time.time() - _none(settings, mbid).stat().st_mtime < TRY_AGAIN_AFTER
    except OSError:
        return False


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_bytes(body)
    part.replace(path)


async def _download(
    http: httpx.AsyncClient, settings: Settings, mbid: str, size: int
) -> bytes | None:
    url = f"{settings.coverartarchive_url.rstrip('/')}/release-group/{mbid}/front-{size}"
    try:
        data, _ = await fetch(http, url, max_bytes=MAX_BYTES)
        body, _ = await asyncio.to_thread(images.thumbnail, data, size)
    except FetchError, ImageError:
        return None
    return body


async def cover(http: httpx.AsyncClient, settings: Settings, mbid: str, size: int) -> bytes | None:
    """A release group's front cover at this size: saved, else fetched (and saved). None
    when it has none (remembered for a day)."""
    if not musicbrainz.MBID.fullmatch(mbid) or size not in SIZES:
        return None
    path = _path(settings, mbid, size)
    try:
        return await asyncio.to_thread(path.read_bytes)
    except OSError:
        pass
    if await asyncio.to_thread(_no_cover_lately, settings, mbid):
        return None
    body = await _download(http, settings, mbid, size)
    await asyncio.to_thread(_write, path if body else _none(settings, mbid), body or b"")
    return body


def _cutoff(today: dt.date | None = None) -> dt.date:
    return (today or dt.date.today()) - KEEP_FOR


async def save_recent(db: AsyncSession, http: httpx.AsyncClient, settings: Settings) -> int:
    """Save the covers of releases coming up and out in the last 90 days that aren't saved
    yet, a batch at a time. Returns how many release groups it fetched for."""
    mbids = list(
        await db.scalars(
            select(ReleaseGroup.mbid).where(ReleaseGroup.released >= _cutoff()).distinct()
        )
    )

    def todo() -> list[str]:
        return [
            m
            for m in mbids
            if not all(_path(settings, m, s).exists() for s in SIZES)
            and not _no_cover_lately(settings, m)
        ][:PER_RUN]

    fetched = 0
    for mbid in await asyncio.to_thread(todo):
        for size in SIZES:
            if await cover(http, settings, mbid, size) is None:
                break  # no cover: the other size won't have one either
        fetched += 1
        await asyncio.sleep(PAUSE)
    return fetched


async def prune(db: AsyncSession, settings: Settings, today: dt.date | None = None) -> int:
    """Delete the covers (and no-cover markers) of releases out more than 90 days ago, and of
    any release group the releases no longer list. Returns how many files went."""
    keep = set(
        await db.scalars(
            select(ReleaseGroup.mbid).where(ReleaseGroup.released >= _cutoff(today)).distinct()
        )
    )

    def sweep() -> int:
        removed = 0
        for path in folder(settings).glob("*"):
            mbid = path.name[:36]
            if mbid not in keep:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    return await asyncio.to_thread(sweep)
