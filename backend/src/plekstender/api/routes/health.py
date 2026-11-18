"""Health endpoint used by Docker healthchecks and the UI's status indicator."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from plekstender import __version__
from plekstender.api.deps import DbDep
from plekstender.api.schemas import HealthOut, WorkerHealth
from plekstender.db.instance import get_worker_heartbeat

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["health"])

WORKER_STALE_AFTER = dt.timedelta(minutes=3)


def worker_is_healthy(last: dt.datetime | None, now: dt.datetime | None = None) -> bool:
    if last is None:
        return False
    return (now or dt.datetime.now(dt.UTC)) - last < WORKER_STALE_AFTER


@router.get("/health", response_model=HealthOut)
async def health(db: DbDep, response: Response) -> HealthOut:
    database = True
    heartbeat = None
    try:
        await db.execute(text("SELECT 1"))
        heartbeat = await get_worker_heartbeat(db)
    except SQLAlchemyError:
        log.exception("health check: database unavailable")
        database = False
    worker = WorkerHealth(healthy=worker_is_healthy(heartbeat), last_heartbeat=heartbeat)
    if not database:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        status="ok" if database and worker.healthy else "degraded",
        version=__version__,
        database=database,
        worker=worker,
    )
