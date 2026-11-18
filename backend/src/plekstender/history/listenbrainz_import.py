"""Background job: import a user's public ListenBrainz listens into `listens`."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import __version__
from plekstender.config import Settings
from plekstender.db.models import ListenImport
from plekstender.history.duplicates import mark_duplicates
from plekstender.history.lastfm import Scrobble, clean_mbid
from plekstender.history.lastfm_import import Sleep, utcnow
from plekstender.history.store import insert_listens, scrobble_row

log = logging.getLogger(__name__)

PAGE = 1000  # ListenBrainz's maximum per request
MAX_ATTEMPTS = 5
RETRY_DELAY = 2.0
REQUEST_INTERVAL = 1.0  # be gentle: a full history is a few hundred requests


class ListenBrainzImportError(Exception):
    def __init__(self, message: str, *, transient: bool = False, wait: float = 0) -> None:
        super().__init__(message)
        self.transient = transient
        self.wait = wait


async def lb_get(
    http: httpx.AsyncClient, settings: Settings, username: str, path: str, **params: Any
) -> dict[str, Any]:
    url = f"{settings.listenbrainz_api_url.rstrip('/')}/1/user/{quote(username, safe='')}{path}"
    try:
        resp = await http.get(
            url,
            params=params,
            timeout=60,
            headers={"User-Agent": f"Plekstender/{__version__}"},
        )
    except httpx.HTTPError as exc:
        raise ListenBrainzImportError("Could not reach ListenBrainz.", transient=True) from exc
    if resp.status_code == 404:
        raise ListenBrainzImportError(f"ListenBrainz has no user called {username!r}.")
    if resp.status_code == 429:
        wait = float(resp.headers.get("X-RateLimit-Reset-In", "10") or 10)
        raise ListenBrainzImportError("ListenBrainz asked to slow down.", transient=True, wait=wait)
    if resp.status_code >= 500:
        raise ListenBrainzImportError(
            f"ListenBrainz is unavailable (HTTP {resp.status_code}).", transient=True
        )
    if resp.status_code != 200:
        raise ListenBrainzImportError(
            f"Unexpected response from ListenBrainz (HTTP {resp.status_code})."
        )
    data: dict[str, Any] = resp.json().get("payload") or {}
    return data


async def listen_count(http: httpx.AsyncClient, settings: Settings, username: str) -> int:
    data = await lb_get(http, settings, username, "/listen-count")
    return int(data.get("count") or 0)


def parse_listen(item: dict[str, Any]) -> tuple[Scrobble, int | None] | None:
    """A listen from the API as a scrobble plus its duration (ms), or None if incomplete."""
    meta = item.get("track_metadata") or {}
    info = meta.get("additional_info") or {}
    mapping = meta.get("mbid_mapping") or {}
    artist, track = meta.get("artist_name"), meta.get("track_name")
    ts = item.get("listened_at")
    if not isinstance(ts, int) or not artist or not track:
        return None
    artist_mbids = info.get("artist_mbids") or mapping.get("artist_mbids") or []
    duration = info.get("duration_ms")
    if not isinstance(duration, int) and isinstance(info.get("duration"), int):
        duration = info["duration"] * 1000
    scrobble = Scrobble(
        listened_at=dt.datetime.fromtimestamp(ts, dt.UTC),
        artist=str(artist).strip(),
        album=(str(meta["release_name"]).strip() or None) if meta.get("release_name") else None,
        track=str(track).strip(),
        artist_mbid=clean_mbid(artist_mbids[0] if artist_mbids else None),
        release_mbid=clean_mbid(info.get("release_mbid") or mapping.get("release_mbid")),
        recording_mbid=clean_mbid(info.get("recording_mbid") or mapping.get("recording_mbid")),
    )
    return scrobble, duration if isinstance(duration, int) and duration > 0 else None


async def _fetch(
    http: httpx.AsyncClient, settings: Settings, username: str, max_ts: int, sleep: Sleep
) -> list[dict[str, Any]]:
    error: ListenBrainzImportError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = await lb_get(http, settings, username, "/listens", max_ts=max_ts, count=PAGE)
            listens: list[dict[str, Any]] = data.get("listens") or []
            return listens
        except ListenBrainzImportError as exc:
            if not exc.transient:
                raise
            error = exc
            if attempt < MAX_ATTEMPTS:
                await sleep(max(exc.wait, RETRY_DELAY * 2 ** (attempt - 1)))
    assert error is not None
    raise error


async def run_listenbrainz_import(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    import_id: int,
    *,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Walk the user's listens newest first, down to the end of the previous import.

    The cursor (`max_ts`, exclusive) is saved after every page so a failed import can be
    resumed. Pages overlap by one second so listens sharing the boundary timestamp aren't
    skipped; the overlap is ignored on insert.
    """
    job = await db.get(ListenImport, import_id)
    if job is None or job.status != "queued":
        return
    now = utcnow()
    job.status = "running"
    job.started_at = job.started_at or now
    job.updated_at = now
    job.error = None
    await db.commit()

    try:
        floor = int(job.window_from.timestamp()) if job.window_from else None
        progress = dict(job.progress)
        stats = {"added": 0, "existing": 0, **job.stats}
        cursor = int(progress.get("cursor") or int(job.window_to.timestamp()) + 1)
        while True:
            listens = await _fetch(http, settings, job.username, cursor, sleep)
            parsed = [p for p in (parse_listen(i) for i in listens) if p]
            rows = []
            for scrobble, duration in parsed:
                if floor is not None and scrobble.listened_at.timestamp() <= floor:
                    continue
                row = scrobble_row(job.user_id, job.id, "listenbrainz", scrobble)
                row["duration_ms"] = duration
                rows.append(row)
            added = await insert_listens(db, rows)
            stats["added"] += added
            stats["existing"] += len(rows) - added

            oldest = min(
                (i["listened_at"] for i in listens if isinstance(i.get("listened_at"), int)),
                default=None,
            )
            finished = (
                not listens
                or len(listens) < PAGE
                or oldest is None
                or (floor is not None and oldest <= floor)
            )
            if not finished:
                assert oldest is not None
                # Overlap one second; if a whole page shares one timestamp, move past it.
                cursor = oldest + 1 if oldest + 1 < cursor else oldest
            progress = {
                "cursor": cursor,
                "done": int(progress.get("done", 0)) + len(rows),
                "total": progress.get("total", 0),
            }
            job.progress = progress
            job.stats = dict(stats)
            job.updated_at = utcnow()
            await db.commit()
            if finished:
                break
            await sleep(REQUEST_INTERVAL)
        await mark_duplicates(db, job.user_id)
        job.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, ListenBrainzImportError):
            log.exception("listen import %s failed", import_id)
        await db.rollback()
        failed = await db.get(ListenImport, import_id, populate_existing=True)
        if failed is None:
            return
        job = failed
        job.status = "failed"
        job.error = (
            str(exc)
            if isinstance(exc, ListenBrainzImportError)
            else "Unexpected error; see the worker log."
        )
    finished_at = utcnow()
    job.finished_at = finished_at
    job.updated_at = finished_at
    await db.commit()
