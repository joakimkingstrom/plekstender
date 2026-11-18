"""Background job runner (procrastinate, backed by Postgres) and job scheduling helpers."""

from __future__ import annotations

import datetime as dt
import logging

import httpx
from procrastinate import App, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from roadiefm import __version__
from roadiefm.auth.sessions import delete_expired_sessions
from roadiefm.config import get_settings
from roadiefm.db import get_sessionmaker
from roadiefm.db.instance import record_worker_heartbeat
from roadiefm.db.models import SyncRun, UserServer
from roadiefm.sync.runner import run_sync

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
# A running sync updates its progress at least every page; silence this long means the
# worker died mid-run.
STALE_AFTER = dt.timedelta(minutes=30)


def _connector() -> PsycopgConnector:
    return PsycopgConnector(conninfo=get_settings().libpq_url)


app = App(connector=_connector(), import_paths=["roadiefm.worker"])


# --- sync -------------------------------------------------------------------------------


@app.task(queue="sync")
async def sync_library(run_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"RoadieFM/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_sync(db, http, get_settings(), run_id)


async def enqueue_sync(db: AsyncSession, us: UserServer, kind: str, trigger: str) -> SyncRun:
    """Queue a sync for one user's server connection, unless one is already pending.

    A pending incremental sync is upgraded when a full one is asked for. Syncs for the
    same server never run at the same time (procrastinate lock), whoever started them.
    """
    existing = await db.scalar(
        select(SyncRun)
        .where(SyncRun.user_server_id == us.id, SyncRun.status.in_(ACTIVE))
        .order_by(SyncRun.id.desc())
        .limit(1)
    )
    if existing is not None:
        if kind == "full" and existing.kind != "full" and existing.status == "queued":
            existing.kind = "full"
            await db.commit()
        return existing

    run = SyncRun(user_server_id=us.id, kind=kind, trigger=trigger, status="queued")
    db.add(run)
    await db.commit()
    try:
        await sync_library.configure(
            lock=f"sync-server-{us.server_id}", queueing_lock=f"sync-us-{us.id}"
        ).defer_async(run_id=run.id)
    except AlreadyEnqueued:
        run.status = "failed"
        run.error = "Another sync was already queued."
        await db.commit()
    return run


async def _schedule_all(kind_for: str) -> int:
    count = 0
    async with get_sessionmaker()() as db:
        connections = list(await db.scalars(select(UserServer)))
        for us in connections:
            if us.music_section_keys:
                await enqueue_sync(db, us, kind_for, "schedule")
                count += 1
    return count


@app.periodic(cron="*/30 * * * *", periodic_id="incremental-sync")
@app.task(queue="maintenance", queueing_lock="schedule-incremental-sync")
async def schedule_incremental_syncs(timestamp: int) -> None:
    """Pick up plays, ratings and library changes every 30 minutes."""
    count = await _schedule_all("incremental")
    log.info("queued incremental syncs for %d server connections", count)


@app.periodic(cron="30 4 * * 0", periodic_id="weekly-full-sync")
@app.task(queue="maintenance", queueing_lock="schedule-full-sync")
async def schedule_full_syncs(timestamp: int) -> None:
    """Weekly full sync: catches deletions and anything incremental syncs can't see."""
    count = await _schedule_all("full")
    log.info("queued full syncs for %d server connections", count)


# --- housekeeping --------------------------------------------------------------------


@app.periodic(cron="* * * * *", periodic_id="heartbeat")
@app.task(queue="maintenance", queueing_lock="heartbeat")
async def heartbeat(timestamp: int) -> None:
    """Lets the web app (and Docker healthcheck) know a worker is alive."""
    async with get_sessionmaker()() as db:
        await record_worker_heartbeat(db)
        await db.commit()


@app.periodic(cron="17 * * * *", periodic_id="cleanup-sessions")
@app.task(queue="maintenance", queueing_lock="cleanup-sessions")
async def cleanup_sessions(timestamp: int) -> None:
    async with get_sessionmaker()() as db:
        removed = await delete_expired_sessions(db)
        stale = await db.execute(
            update(SyncRun)
            .where(
                SyncRun.status == "running",
                SyncRun.updated_at < dt.datetime.now(dt.UTC) - STALE_AFTER,
            )
            .values(status="failed", error="Interrupted (the worker stopped during the sync).")
        )
        await db.commit()
    if removed:
        log.info("removed %d expired sessions", removed)
    if stale_count := int(getattr(stale, "rowcount", 0) or 0):
        log.warning("marked %d interrupted sync runs as failed", stale_count)
