"""Background job runner (procrastinate, backed by Postgres)."""

from __future__ import annotations

import logging

from procrastinate import App, PsycopgConnector

from roadiefm.auth.sessions import delete_expired_sessions
from roadiefm.config import get_settings
from roadiefm.db import get_sessionmaker
from roadiefm.db.instance import record_worker_heartbeat

log = logging.getLogger(__name__)


def _connector() -> PsycopgConnector:
    return PsycopgConnector(conninfo=get_settings().libpq_url)


app = App(connector=_connector(), import_paths=["roadiefm.worker"])


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
        await db.commit()
    if removed:
        log.info("removed %d expired sessions", removed)
