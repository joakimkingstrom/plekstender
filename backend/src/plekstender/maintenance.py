"""Keeping the database from growing for ever (worker, daily): the job queue's history, and
the records of finished background tasks that nothing reads once they're old.

Kept: what the app shows or works from (listens, play sessions, edits and their undo
history, lyrics found, release groups...). Postgres's autovacuum reuses the space freed.
"""

from __future__ import annotations

import datetime as dt
import logging

from procrastinate import App
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Background jobs in the queue (procrastinate), after they finished.
JOBS_SUCCEEDED = dt.timedelta(days=7)
JOBS_OTHERWISE = dt.timedelta(days=30)  # failed, cancelled or aborted: kept longer to look into
# Records of finished tasks (scans, lookups, dry runs, syncs, artwork jobs).
RECORDS = dt.timedelta(days=90)
KEEP_LATEST = 5  # of each kind, whatever their age (pages show the latest)

# Table, and what "of each kind" is for it.
_RECORDS = {
    "file_scans": "server_id, kind",
    "sync_runs": "user_server_id, kind",
    "artwork_jobs": "server_id, kind",
}
FINISHED = ("succeeded", "failed", "applied", "partial", "ok", "error")


async def prune_jobs(app: App) -> None:
    """Finished jobs in the queue (their events go with them)."""
    await app.job_manager.delete_old_jobs(nb_hours=int(JOBS_SUCCEEDED.total_seconds() // 3600))
    await app.job_manager.delete_old_jobs(
        nb_hours=int(JOBS_OTHERWISE.total_seconds() // 3600),
        include_failed=True,
        include_cancelled=True,
        include_aborted=True,
    )


async def prune_records(db: AsyncSession, now: dt.datetime | None = None) -> dict[str, int]:
    """Finished task records older than RECORDS, but the latest few of each kind. Returns how
    many went per table. Caller commits."""
    before = (now or dt.datetime.now(dt.UTC)) - RECORDS
    removed: dict[str, int] = {}
    for table, kind in _RECORDS.items():
        result = await db.execute(
            text(
                f"""
                DELETE FROM {table} WHERE id IN (
                    SELECT id FROM (
                        SELECT id, status, coalesce(finished_at, updated_at) AS at,
                               row_number() OVER (PARTITION BY {kind} ORDER BY id DESC) AS n
                        FROM {table}
                    ) ranked
                    WHERE n > :keep AND status = ANY(:finished) AND at < :before
                )
                """  # noqa: S608 - constant table and column names
            ),
            {"keep": KEEP_LATEST, "finished": list(FINISHED), "before": before},
        )
        removed[table] = int(getattr(result, "rowcount", 0) or 0)
    return removed
