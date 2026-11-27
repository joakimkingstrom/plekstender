"""Settings → Jobs: every kind of job in one list, and what a waiting one waits for."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from plekstender.db import get_sessionmaker
from plekstender.db.models import FileScan, TagChange, TagChangeSet, UserServer
from tests.fakes import FakePlex, sign_in
from tests.test_sync import owner_with_library


@pytest.mark.db
async def test_jobs_and_what_they_wait_for(app: FastAPI, plex: FakePlex) -> None:
    from plekstender.worker import enqueue_duplicates, enqueue_file_scan

    owner, _ = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        server_id = await db.scalar(select(UserServer.server_id).where(UserServer.is_owner))
        dupes = FileScan(server_id=server_id, kind="dupes", status="running", params={})
        scan = FileScan(server_id=server_id, kind="scan", status="queued", params={})
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


@pytest.mark.db
async def test_jobs_a_stopped_worker_left_running_are_closed(app: FastAPI, plex: FakePlex) -> None:
    """A restart cuts a job off: its own record says "running" (or, once asked to stop,
    "stopping") for ever unless something closes it. Jobs a worker still runs are left alone,
    however long since they last moved on."""
    import datetime as dt

    from plekstender.job_list import close_abandoned
    from plekstender.worker import enqueue_file_scan

    await owner_with_library(app, plex)
    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=20)
    async with get_sessionmaker()() as db:
        server_id = await db.scalar(select(UserServer.server_id).where(UserServer.is_owner))
        cut_off = TagChangeSet(
            server_id=server_id, description="Identified by sound: 2 files", status="running",
            stop_requested="stop", updated_at=long_ago,
        )  # fmt: skip
        fresh = TagChangeSet(server_id=server_id, description="Just started", status="running")
        db.add_all([cut_off, fresh])
        await db.flush()
        db.add_all(
            [
                TagChange(change_set_id=cut_off.id, path="a.flac", field="title", status="applied"),
                TagChange(change_set_id=cut_off.id, path="b.flac", field="title"),
            ]
        )
        gone = FileScan(server_id=server_id, kind="identify", status="running", params={},
                        updated_at=long_ago)  # fmt: skip
        busy = FileScan(server_id=server_id, kind="scan", status="running", params={},
                        updated_at=long_ago)  # fmt: skip
        db.add_all([gone, busy])
        await db.commit()
        # The scan is still with a worker (reading pictures, say, between saves).
        await enqueue_file_scan(busy)
        await db.execute(
            text(
                "UPDATE procrastinate_jobs SET status = 'doing' "
                "WHERE task_name = 'plekstender.worker.scan_music_files'"
            )
        )
        await db.commit()

        closed = await close_abandoned(db)
        assert sorted(closed) == sorted([f"files {gone.id}", f"edits {cut_off.id}"])
        for job in (cut_off, fresh, gone, busy):
            await db.refresh(job)
        # What was written stays; the rest is marked as not written.
        assert (cut_off.status, cut_off.error) == (
            "partial",
            "Interrupted (the worker stopped): 1 change was not written.",
        )
        assert (gone.status, gone.finished_at is not None) == ("failed", True)
        assert fresh.status == busy.status == "running"
        statuses = await db.scalars(
            select(TagChange.status).where(TagChange.change_set_id == cut_off.id)
        )
        assert sorted(statuses) == ["applied", "failed"]
