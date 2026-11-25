"""The daily clean-up: the job queue's history, and old records of finished tasks."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender import maintenance
from plekstender.db import get_sessionmaker
from plekstender.db.models import FileScan, PlexServer, SyncRun
from tests.fakes import FakePlex
from tests.test_sync import owner_with_library


async def test_finished_jobs_leave_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from plekstender import worker

    calls: list[dict[str, Any]] = []

    async def delete_old_jobs(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(worker.app.job_manager, "delete_old_jobs", delete_old_jobs)
    await maintenance.prune_jobs(worker.app)
    everything = {"include_failed": True, "include_cancelled": True, "include_aborted": True}
    assert calls == [{"nb_hours": 7 * 24}, {"nb_hours": 30 * 24, **everything}]


@pytest.mark.db
async def test_old_task_records_go_but_the_latest_stay(app: FastAPI, plex: FakePlex) -> None:
    await owner_with_library(app, plex)  # one sync run, kept (the latest)
    now = dt.datetime.now(dt.UTC)
    old = now - dt.timedelta(days=200)
    async with get_sessionmaker()() as db:
        server = await db.scalar(select(PlexServer))
        assert server is not None

        def scan(kind: str, status: str, at: dt.datetime) -> FileScan:
            return FileScan(
                server_id=server.id, kind=kind, status=status, finished_at=at, updated_at=at
            )

        db.add_all([scan("scan", "succeeded", old) for _ in range(7)])
        await db.flush()
        db.add_all(
            [
                scan("scan", "failed", now),  # recent
                scan("scan", "running", old),  # still running (or stuck): left alone
                scan("lyrics", "succeeded", old),  # the only one of its kind
            ]
        )
        await db.commit()
        removed = await maintenance.prune_records(db)
        await db.commit()
        left = (await db.execute(select(FileScan.kind, FileScan.status))).all()
        runs = await db.scalar(select(SyncRun.id))
    # Scans, newest first: running, failed (recent), then seven old ones: the five latest are
    # kept (running, failed and three old), so four old ones go.
    assert removed == {"file_scans": 4, "sync_runs": 0, "artwork_jobs": 0}
    assert sorted(left) == sorted(
        [("scan", "succeeded")] * 3
        + [("scan", "failed"), ("scan", "running"), ("lyrics", "succeeded")]
    )
    assert runs is not None
