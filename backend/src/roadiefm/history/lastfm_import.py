"""Background job: import a user's Last.fm scrobbles into `listens`."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roadiefm.config import Settings
from roadiefm.db.instance import get_lastfm_api_key
from roadiefm.db.models import ListenImport
from roadiefm.history.lastfm import LastFmClient, LastFmError, RecentTracksPage
from roadiefm.history.store import insert_listens, scrobble_row

log = logging.getLogger(__name__)

PAGE_LIMIT = 200  # Last.fm's maximum
MAX_ATTEMPTS = 5
RETRY_DELAY = 2.0  # seconds, doubled per attempt
REQUEST_INTERVAL = 0.25  # stay well under Last.fm's ~5 requests per second

Sleep = Callable[[float], Awaitable[None]]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def next_window_start(db: AsyncSession, user_id: int, username: str) -> dt.datetime | None:
    """Where a new import for this Last.fm user can start: the end of the last finished one."""
    return await db.scalar(
        select(func.max(ListenImport.window_to)).where(
            ListenImport.user_id == user_id,
            ListenImport.source == "lastfm",
            ListenImport.status == "succeeded",
            func.lower(ListenImport.username) == username.lower(),
        )
    )


async def _fetch_page(
    client: LastFmClient, job: ListenImport, page: int, sleep: Sleep
) -> RecentTracksPage:
    from_ts = int(job.window_from.timestamp()) + 1 if job.window_from else None
    to_ts = int(job.window_to.timestamp())
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = await client.recent_tracks(
                job.username, page=page, limit=PAGE_LIMIT, from_ts=from_ts, to_ts=to_ts
            )
            # Last.fm occasionally returns an empty page in the middle of a long history.
            if result.scrobbles or page >= result.total_pages:
                return result
            error = LastFmError(
                f"Last.fm kept returning an empty page ({page} of {result.total_pages}). "
                "Resume the import to try again.",
                transient=True,
            )
        except LastFmError as exc:
            if not exc.transient:
                raise
            error = exc
        if attempt < MAX_ATTEMPTS:
            log.info("Last.fm page %s failed (%s); retrying", page, error)
            await sleep(RETRY_DELAY * 2 ** (attempt - 1))
    raise error


async def run_lastfm_import(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    import_id: int,
    *,
    sleep: Sleep = asyncio.sleep,
) -> None:
    job = await db.get(ListenImport, import_id)
    if job is None or job.status != "queued":
        log.info("listen import %s is not queued; skipping", import_id)
        return
    now = utcnow()
    job.status = "running"
    job.started_at = job.started_at or now
    job.updated_at = now
    job.error = None
    await db.commit()

    try:
        api_key, _ = await get_lastfm_api_key(db, settings)
        if not api_key:
            raise LastFmError("No Last.fm API key is configured. An admin can add one in Settings.")
        client = LastFmClient(http, api_key, settings.lastfm_api_url)
        stats = {"added": 0, "existing": 0, **job.stats}
        progress = dict(job.progress)
        page = int(progress.get("page", 0)) + 1  # resumes after the last stored page
        while True:
            result = await _fetch_page(client, job, page, sleep)
            rows = [scrobble_row(job.user_id, job.id, "lastfm", s) for s in result.scrobbles]
            added = await insert_listens(db, rows)
            stats["added"] += added
            stats["existing"] += len(rows) - added
            progress = {
                "page": page,
                "pages": result.total_pages,
                "done": int(progress.get("done", 0)) + len(result.scrobbles),
                "total": result.total,
            }
            job.progress = progress
            job.stats = dict(stats)
            job.updated_at = utcnow()
            await db.commit()
            if page >= result.total_pages or not result.scrobbles:
                break
            page += 1
            await sleep(REQUEST_INTERVAL)
        job.status = "succeeded"
    except Exception as exc:
        if not isinstance(exc, LastFmError):
            log.exception("listen import %s failed", import_id)
        await db.rollback()
        failed = await db.get(ListenImport, import_id, populate_existing=True)
        if failed is None:
            return
        job = failed
        job.status = "failed"
        job.error = (
            str(exc) if isinstance(exc, LastFmError) else "Unexpected error; see the worker log."
        )
    finished = utcnow()
    job.finished_at = finished
    job.updated_at = finished
    await db.commit()
