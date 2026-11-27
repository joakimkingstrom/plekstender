"""Background job: send a user's whole listening history to their ListenBrainz account.

ListenBrainz ignores a listen it already has with the same time and the same metadata
(its `listen` table is unique on user, listened_at and `recording_msid`, a hash of the
names). History that got there another way - ListenBrainz's own Last.fm importer, or
another scrobbler - has slightly different names and would be doubled, so for each
batch we first read the user's listens on ListenBrainz around those times and leave out
any of ours with the same song within a minute.

Listens imported *from* ListenBrainz aren't sent back, and duplicates between sources
(the same play from Plex and Last.fm) are sent once. The job walks newest first, like
ListenBrainz's API, saving a cursor after each batch so it can be resumed. Running it
again is safe: everything already there is skipped.
"""

from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import functools
import logging
from typing import Any

import httpx
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import Listen, ListenBrainzLink, ListenImport
from plekstender.history.lastfm_import import Sleep, utcnow
from plekstender.history.listenbrainz import (
    LISTEN_MINIMUM_TS,
    TOKEN_PURPOSE,
    ListenBrainzClient,
    ScrobbleServiceError,
    listen_payload,
)
from plekstender.history.listenbrainz_import import (
    PAGE,
    ListenBrainzImportError,
    lb_get,
)
from plekstender.jobs import JobStopped, check_stop
from plekstender.security.crypto import token_cipher
from plekstender.sync.parse import normalise_title

log = logging.getLogger(__name__)

SOURCE = "lb_send"
BATCH = 500  # listens per submission (ListenBrainz allows 1000)
NEAR = 60  # seconds: a listen on ListenBrainz this close, of the same song, is the same play
MAX_ATTEMPTS = 5
RETRY_DELAY = 2.0
REQUEST_INTERVAL = 1.0


class SendError(Exception):
    """Stops the job with a message for the user."""


def _candidates(user_id: int, window_to: dt.datetime) -> tuple[Any, ...]:
    return (
        Listen.user_id == user_id,
        Listen.duplicate_of.is_(None),
        Listen.source != "listenbrainz",
        Listen.listened_at >= dt.datetime.fromtimestamp(LISTEN_MINIMUM_TS, dt.UTC),
        Listen.listened_at <= window_to,
    )


async def count_candidates(db: AsyncSession, user_id: int, window_to: dt.datetime) -> int:
    return await db.scalar(select(func.count()).where(*_candidates(user_id, window_to))) or 0


async def _retrying(sleep: Sleep, call: Any) -> Any:
    """Run `call()` again after transient ListenBrainz errors (rate limits, outages)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await call()
        except ListenBrainzImportError as exc:
            if not exc.transient or attempt == MAX_ATTEMPTS:
                raise SendError(str(exc)) from exc
            await sleep(max(exc.wait, RETRY_DELAY * 2 ** (attempt - 1)))
        except ScrobbleServiceError as exc:
            if exc.status == 401:
                raise SendError(
                    "ListenBrainz rejected your token; paste a new one under Connections."
                ) from exc
            if exc.status == 400 or attempt == MAX_ATTEMPTS:
                raise
            await sleep(RETRY_DELAY * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


async def already_there(
    http: httpx.AsyncClient,
    settings: Settings,
    username: str,
    newest: int,
    oldest: int,
    sleep: Sleep,
) -> dict[str, list[int]]:
    """The user's ListenBrainz listens from `oldest - NEAR` to `newest + NEAR`, as
    normalised song title -> sorted timestamps."""
    found: dict[str, list[int]] = {}
    max_ts = newest + NEAR + 1
    while True:
        data = await _retrying(
            sleep,
            functools.partial(
                lb_get, http, settings, username, "/listens", max_ts=max_ts, count=PAGE
            ),
        )
        listens: list[dict[str, Any]] = data.get("listens") or []
        stamps = []
        for item in listens:
            ts = item.get("listened_at")
            name = (item.get("track_metadata") or {}).get("track_name")
            if isinstance(ts, int) and isinstance(name, str):
                stamps.append(ts)
                found.setdefault(normalise_title(name), []).append(ts)
        if len(listens) < PAGE or not stamps or min(stamps) < oldest - NEAR:
            break
        # Overlap one second; if a whole page shares one timestamp, move past it.
        lowest = min(stamps)
        max_ts = lowest + 1 if lowest + 1 < max_ts else lowest
        await sleep(REQUEST_INTERVAL)
    for stamps in found.values():
        stamps.sort()
    return found


def is_there(listen: Listen, existing: dict[str, list[int]]) -> bool:
    stamps = existing.get(normalise_title(listen.track_name))
    if not stamps:
        return False
    ts = int(listen.listened_at.timestamp())
    i = bisect.bisect_left(stamps, ts - NEAR)
    return i < len(stamps) and stamps[i] <= ts + NEAR


async def _submit(
    client: ListenBrainzClient, listens: list[Listen], sleep: Sleep
) -> tuple[int, int]:
    """Send listens; ones ListenBrainz refuses are found one by one and left out.
    Returns (sent, refused)."""
    if not listens:
        return 0, 0
    payload = [listen_payload(listen) for listen in listens]
    try:
        await _retrying(sleep, functools.partial(client.submit, payload))
        return len(listens), 0
    except ScrobbleServiceError as exc:
        if exc.status != 400:
            raise SendError(str(exc)) from exc
    sent = refused = 0
    for listen in listens:
        single = [listen_payload(listen)]
        try:
            await _retrying(sleep, functools.partial(client.submit, single))
            sent += 1
        except ScrobbleServiceError as exc:
            if exc.status != 400:
                raise SendError(str(exc)) from exc
            log.warning("ListenBrainz refused listen %s: %s", listen.id, exc)
            refused += 1
    return sent, refused


async def run_listenbrainz_send(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    job_id: int,
    *,
    sleep: Sleep = asyncio.sleep,
) -> None:
    job = await db.get(ListenImport, job_id)
    if job is None or job.status != "queued":
        return
    now = utcnow()
    job.status = "running"
    job.started_at = job.started_at or now
    job.updated_at = now
    job.error = None
    await db.commit()

    try:
        link = await db.get(ListenBrainzLink, job.user_id)
        if link is None:
            raise SendError("Connect your ListenBrainz account first.")
        if link.paused:
            raise SendError("ListenBrainz rejected your token; paste a new one under Connections.")
        cipher = token_cipher(settings)
        client = ListenBrainzClient(
            http,
            settings.listenbrainz_api_url,
            cipher.decrypt(link.token_enc, purpose=TOKEN_PURPOSE),
        )
        username = link.username
        stats = {"sent": 0, "existing": 0, "refused": 0, **job.stats}
        progress = dict(job.progress)
        while True:
            await check_stop(db, ListenImport, job.id)
            where = list(_candidates(job.user_id, job.window_to))
            if "cursor_ts" in progress:
                cursor = (
                    dt.datetime.fromtimestamp(progress["cursor_ts"], dt.UTC),
                    int(progress["cursor_id"]),
                )
                where.append(tuple_(Listen.listened_at, Listen.id) < cursor)
            batch = list(
                await db.scalars(
                    select(Listen)
                    .where(*where)
                    .order_by(Listen.listened_at.desc(), Listen.id.desc())
                    .limit(BATCH)
                )
            )
            if not batch:
                break
            newest = int(batch[0].listened_at.timestamp())
            oldest = int(batch[-1].listened_at.timestamp())
            existing = await already_there(http, settings, username, newest, oldest, sleep)
            new = [listen for listen in batch if not is_there(listen, existing)]
            sent, refused = await _submit(client, new, sleep)
            stats["sent"] += sent
            stats["refused"] += refused
            stats["existing"] += len(batch) - len(new)
            last = batch[-1]
            progress = {
                **progress,
                "cursor_ts": last.listened_at.timestamp(),
                "cursor_id": last.id,
                "done": int(progress.get("done", 0)) + len(batch),
            }
            job.progress = progress
            job.stats = dict(stats)
            job.updated_at = utcnow()
            if sent:
                link.last_submitted_at = utcnow()
            await db.commit()
            await sleep(REQUEST_INTERVAL)
        job.status = "succeeded"
    except JobStopped:
        # Nothing to take back: what was sent is on ListenBrainz now.
        await db.rollback()
        job = await db.get(ListenImport, job_id, populate_existing=True) or job
        job.status = "failed"
        job.error = "Stopped. What it sent stays on ListenBrainz; resuming carries on from there."
    except Exception as exc:
        if not isinstance(exc, SendError):
            log.exception("sending listens to ListenBrainz (job %s) failed", job_id)
        await db.rollback()
        failed = await db.get(ListenImport, job_id, populate_existing=True)
        if failed is None:
            return
        job = failed
        job.status = "failed"
        job.error = (
            str(exc) if isinstance(exc, SendError) else "Unexpected error; see the worker log."
        )
    finished = utcnow()
    job.finished_at = finished
    job.updated_at = finished
    await db.commit()
