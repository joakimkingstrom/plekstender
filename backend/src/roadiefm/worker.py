"""Background job runner (procrastinate, backed by Postgres) and job scheduling helpers."""

from __future__ import annotations

import datetime as dt
import logging

import httpx
from procrastinate import App, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from roadiefm import __version__
from roadiefm.auth.sessions import delete_expired_sessions
from roadiefm.config import get_settings
from roadiefm.db import get_sessionmaker
from roadiefm.db.instance import get_library_settings, record_worker_heartbeat
from roadiefm.db.models import PlexServer, SyncRun, UserSectionSync, UserServer
from roadiefm.sync.popularity import recompute_popularity
from roadiefm.sync.runner import run_sync
from roadiefm.sync.schedule import due_sync

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


async def schedule_due_syncs(now: dt.datetime | None = None) -> dict[str, int]:
    """Queue a sync for every connection that's due one. Returns counts per kind."""
    now = now or dt.datetime.now(dt.UTC)
    queued: dict[str, int] = {"full": 0, "incremental": 0}
    async with get_sessionmaker()() as db:
        settings = await get_library_settings(db)
        connections = [us for us in await db.scalars(select(UserServer)) if us.music_section_keys]
        if not connections:
            return queued
        ids = [us.id for us in connections]
        last_attempts = dict(
            (
                await db.execute(
                    select(SyncRun.user_server_id, func.max(SyncRun.created_at))
                    .where(SyncRun.user_server_id.in_(ids))
                    .group_by(SyncRun.user_server_id)
                )
            ).all()
        )
        full_syncs: dict[tuple[int, str], dt.datetime | None] = {
            (row.user_server_id, row.section_key): row.last_full_sync_at
            for row in await db.scalars(
                select(UserSectionSync).where(UserSectionSync.user_server_id.in_(ids))
            )
        }
        for us in connections:
            fulls = [full_syncs.get((us.id, key)) for key in us.music_section_keys]
            last_full = None if any(f is None for f in fulls) else min(f for f in fulls if f)
            kind = due_sync(
                now,
                last_attempt=last_attempts.get(us.id),
                last_full=last_full,
                settings=settings,
            )
            if kind is not None:
                await enqueue_sync(db, us, kind, "schedule")
                queued[kind] += 1
    return queued


@app.periodic(cron="*/5 * * * *", periodic_id="schedule-syncs")
@app.task(queue="maintenance", queueing_lock="schedule-syncs")
async def schedule_syncs(timestamp: int) -> None:
    """Every 5 minutes: queue the syncs that are due (intervals are set on Settings)."""
    queued = await schedule_due_syncs()
    if any(queued.values()):
        log.info("queued syncs: %s", queued)


@app.task(queue="maintenance", queueing_lock="recompute-popularity")
async def recompute_all_popularity() -> None:
    """Re-score albums and artists on every server after the popularity settings change."""
    async with get_sessionmaker()() as db:
        settings = await get_library_settings(db)
        for server_id in list(await db.scalars(select(PlexServer.id))):
            await recompute_popularity(db, server_id, settings)
            await db.commit()


async def enqueue_popularity_recompute() -> None:
    try:
        await recompute_all_popularity.defer_async()
    except AlreadyEnqueued:
        pass  # one is already waiting and will use the latest settings


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
