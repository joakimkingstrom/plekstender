"""Stopping long jobs from the app: the page records a request on the job's row ("stop":
keep what's done; "cancel": also take it back where that's possible), and the job checks for
it between items, so it stops within one item.

A job no worker is running any more (no progress for a while) is stopped by the app itself.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

StopRequest = Literal["stop", "cancel"]
# A running job saves its progress at least this often; one that hasn't has no worker.
NO_WORKER_AFTER = dt.timedelta(minutes=5)


class JobStopped(Exception):
    """Raised inside a job when it's been asked to stop."""

    def __init__(self, request: StopRequest) -> None:
        super().__init__(request)
        self.request: StopRequest = request

    @property
    def cancel(self) -> bool:
        return self.request == "cancel"


async def check_stop(db: AsyncSession, model: Any, job_id: int) -> None:
    """Raise JobStopped when the job's been asked to stop (read fresh from the database)."""
    request = await db.scalar(select(model.stop_requested).where(model.id == job_id))
    if request in ("stop", "cancel"):
        raise JobStopped(request)


def abandoned(job: Any, now: dt.datetime | None = None) -> bool:
    """Running, but no worker has moved it on for a while (it stopped with its worker)."""
    now = now or dt.datetime.now(dt.UTC)
    return bool(job.status == "running" and now - job.updated_at > NO_WORKER_AFTER)
