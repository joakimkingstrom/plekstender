"""Settings → Jobs: every kind of job in one list, and what a waiting one waits for."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import text

from plekstender.db import get_sessionmaker
from plekstender.db.models import FileScan
from tests.fakes import FakePlex, sign_in
from tests.test_sync import owner_with_library


@pytest.mark.db
async def test_jobs_and_what_they_wait_for(app: FastAPI, plex: FakePlex) -> None:
    from plekstender.worker import enqueue_duplicates, enqueue_file_scan

    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        dupes = FileScan(server_id=sid, kind="dupes", status="running", params={})
        scan = FileScan(server_id=sid, kind="scan", status="queued", params={})
        db.add_all([dupes, scan])
        await db.commit()
        await enqueue_duplicates(dupes)
        await enqueue_file_scan(scan)
        # The worker took the duplicates job: it holds the music folder's lock.
        await db.execute(
            text(
                "UPDATE procrastinate_jobs SET status = 'doing' "
                "WHERE task_name = 'plekstender.worker.find_duplicates'"
            )
        )
        await db.execute(
            text(
                "INSERT INTO procrastinate_events (job_id, type) SELECT id, 'started' "
                "FROM procrastinate_jobs WHERE task_name = 'plekstender.worker.find_duplicates'"
            )
        )
        await db.commit()
        dupes_id, scan_id = dupes.id, scan.id

    jobs = (await owner.get("/api/jobs")).json()
    assert [(j["kind"], j["id"], j["status"]) for j in jobs["active"]] == [
        ("files", dupes_id, "running"),
        ("files", scan_id, "queued"),
    ]
    running, waiting = jobs["active"]
    assert running["what"] == "Finding duplicate files" and running["stoppable"] is True
    assert waiting["what"] == "Reading the music files' tags"
    assert waiting["waiting_for"]["what"] == "Finding duplicate files"
    assert waiting["waiting_for"]["since"] is not None
    # The library sync that ran when the server was connected, among the finished ones.
    assert any(j["kind"] == "sync" and j["status"] == "succeeded" for j in jobs["finished"])

    # Someone else sees none of it (it's the owner's server).
    friend, _ = await sign_in(app, plex, "friend-token")
    others = (await friend.get("/api/jobs")).json()
    assert not any(j["kind"] == "files" for j in others["active"] + others["finished"])
