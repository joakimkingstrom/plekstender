"""Stopping and cancelling long jobs (POST /api/jobs/{kind}/{id}/stop): a running job stops at
its next item, keeping what it did, or with cancel takes it back; a queued one, or one whose
worker has gone, is wound up by the app itself."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mutagen.flac import FLAC
from sqlalchemy import func, select, update

from plekstender.artwork import undo
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import (
    AlbumCover,
    ArtworkJob,
    ListenImport,
    MediaFile,
    TagChange,
    TagChangeSet,
    UserServer,
)
from plekstender.history import lastfm_import
from plekstender.plex.server import client_for
from plekstender.tags import apply
from tests.fakes import CSRF, FakeLastFm, FakePlex, sign_in
from tests.test_album_covers import SIZES, measure, web  # noqa: F401 - the cover sources fixture
from tests.test_files import music  # noqa: F401 - the music folder fixture
from tests.test_listens import START, admin_with_key, run_queued_imports
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lastfm_import, "PAGE_LIMIT", 50)


def _asking(
    module: Any, model: Any, monkeypatch: pytest.MonkeyPatch, at: int, request: str
) -> None:
    """Make `module`'s job be asked to stop (or cancel) just before its `at`-th check."""
    real = module.check_stop
    calls = 0

    async def check(db: Any, job_model: Any, job_id: int) -> None:
        nonlocal calls
        calls += 1
        if calls == at:
            await db.execute(update(model).where(model.id == job_id).values(stop_requested=request))
            await db.commit()
        await real(db, job_model, job_id)

    monkeypatch.setattr(module, "check_stop", check)


async def _plays(owner: httpx.AsyncClient) -> int:
    return int((await owner.get("/api/listens/summary")).json()["total"])


async def test_stopping_and_cancelling_an_import(
    app: FastAPI, plex: FakePlex, lastfm: FakeLastFm, monkeypatch: pytest.MonkeyPatch
) -> None:
    lastfm.add_scrobbles("Joakim", 120, start=START)  # three pages of 50
    owner = await admin_with_key(app, plex)

    # Stopped after its first page: those plays are kept.
    await owner.post("/api/listens/lastfm", json={"username": "joakim"}, headers=CSRF)
    _asking(lastfm_import, ListenImport, monkeypatch, 2, "stop")
    [stopped] = await run_queued_imports()
    assert (stopped.status, stopped.stop_requested) == ("failed", "stop")
    assert stopped.error == "Stopped. What it imported is kept; resuming carries on from there."
    assert await _plays(owner) == 50

    # Cancelled after its first page: the plays this import added are taken out again (the
    # 50 the first one added are only counted as already there).
    await owner.post("/api/listens/lastfm", json={"username": "joakim"}, headers=CSRF)
    _asking(lastfm_import, ListenImport, monkeypatch, 3, "cancel")
    [cancelled] = await run_queued_imports()
    assert cancelled.error == "Cancelled: the 50 plays it imported were removed."
    assert cancelled.stats["added"] == 0
    assert await _plays(owner) == 50


async def test_the_stop_endpoint(app: FastAPI, plex: FakePlex, lastfm: FakeLastFm) -> None:
    lastfm.add_scrobbles("Joakim", 3, start=START)
    owner = await admin_with_key(app, plex)
    await owner.post("/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF)
    job = (
        await owner.post("/api/listens/lastfm", json={"username": "joakim"}, headers=CSRF)
    ).json()
    stop = f"/api/jobs/imports/{job['id']}/stop"

    friend, _ = await sign_in(app, plex, "friend-token")
    assert (await friend.post(stop, json={}, headers=CSRF)).status_code == 404  # not theirs
    missing = await owner.post("/api/jobs/imports/999999/stop", json={}, headers=CSRF)
    assert missing.status_code == 404
    unknown = await owner.post(f"/api/jobs/nonsense/{job['id']}/stop", json={}, headers=CSRF)
    assert unknown.status_code == 422

    # Running, with a worker: it's asked to stop, and does so at its next page.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ListenImport).where(ListenImport.id == job["id"]).values(status="running")
        )
        await db.commit()
    asked = (await owner.post(stop, json={"cancel": True}, headers=CSRF)).json()
    assert asked == {"status": "running", "error": None, "stop_requested": "cancel"}
    listed = (await owner.get("/api/listens/lastfm")).json()
    assert listed["active"]["stop_requested"] == "cancel"

    # Its worker gone (nothing heard for a while): the app winds it up itself.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ListenImport)
            .where(ListenImport.id == job["id"])
            .values(updated_at=func.now() - dt.timedelta(minutes=10))
        )
        await db.commit()
    gone = (await owner.post(stop, json={}, headers=CSRF)).json()
    assert (gone["status"], gone["stop_requested"]) == ("failed", "stop")
    assert (await owner.post(stop, json={}, headers=CSRF)).status_code == 409  # not running

    # Queued: stopped there and then; the worker leaves it alone.
    queued = (
        await owner.post("/api/listens/lastfm", json={"username": "joakim"}, headers=CSRF)
    ).json()
    done = await owner.post(f"/api/jobs/imports/{queued['id']}/stop", json={}, headers=CSRF)
    assert done.json()["status"] == "failed"
    assert await run_queued_imports() == []
    assert await _plays(owner) == 0


async def _edit_of_two(owner: httpx.AsyncClient, sid: int, music: Path) -> int:  # noqa: F811
    """An edit retitling both OK Computer files (one from Plex's metadata, plus one more)."""
    from plekstender.worker import scan_music_files

    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])
    edit = (await owner.post(f"{base}/plex-sync", json={"scope": "library"}, headers=CSRF)).json()
    async with get_sessionmaker()() as db:
        other = await db.scalar(select(MediaFile).where(MediaFile.path == "300.flac"))
        assert other is not None
        db.add(
            TagChange(
                change_set_id=edit["id"],
                media_file_id=other.id,
                path="300.flac",
                rating_key=300,
                field="title",
                old_value="Paranoid Android",
                new_value="Paranoid Android (Edit)",
            )
        )
        await db.commit()
    return int(edit["id"])


def _titles(music: Path) -> list[str]:  # noqa: F811
    return [FLAC(music / name)["title"][0] for name in ("300.flac", "301.flac")]


async def test_stopping_and_cancelling_an_edit(
    app: FastAPI,
    plex: FakePlex,
    music: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plekstender.worker import apply_tag_changes

    owner, sid = await owner_with_library(app, plex)
    before = _titles(music)
    monkeypatch.setattr(apply, "FILE_BATCH", 1)
    real_check = apply.check_stop

    # Cancelled after the first file: the other isn't written, and an undo edit takes the
    # first one back.
    set_id = await _edit_of_two(owner, sid, music)
    _asking(apply, TagChangeSet, monkeypatch, 2, "cancel")
    await apply_tag_changes.func(set_id=set_id)
    edits = (await owner.get(f"/api/servers/{sid}/files/changes")).json()
    [undo_edit, edit] = edits[:2]
    assert (edit["id"], edit["status"], edit["counts"]) == (
        set_id,
        "partial",
        {"applied": 1, "failed": 1},
    )
    assert edit["error"] == ("Stopped: 1 change was not written. What was written is being undone.")
    assert edit["stop_requested"] == "cancel"
    assert (undo_edit["kind"], undo_edit["status"], undo_edit["total"]) == ("undo", "queued", 1)
    assert _titles(music) != before
    await apply_tag_changes.func(set_id=undo_edit["id"])
    assert _titles(music) == before

    # Queued: stopped by the app, nothing written.
    queued = await _edit_of_two(owner, sid, music)
    stopped = await owner.post(f"/api/jobs/edits/{queued}/stop", json={}, headers=CSRF)
    assert stopped.json() == {
        "status": "failed",
        "error": "Stopped: 2 changes were not written.",
        "stop_requested": "stop",
    }
    await apply_tag_changes.func(set_id=queued)  # the worker leaves it alone
    assert _titles(music) == before

    # Written, but its worker never said it was done: cancelling undoes what it wrote.
    monkeypatch.setattr(apply, "check_stop", real_check)
    lost = await _edit_of_two(owner, sid, music)
    await apply_tag_changes.func(set_id=lost)
    assert _titles(music) != before
    async with get_sessionmaker()() as db:
        await db.execute(
            update(TagChangeSet)
            .where(TagChangeSet.id == lost)
            .values(status="running", updated_at=func.now() - dt.timedelta(hours=1))
        )
        await db.commit()
    cancelled = (
        await owner.post(f"/api/jobs/edits/{lost}/stop", json={"cancel": True}, headers=CSRF)
    ).json()
    assert cancelled["status"] == "applied"  # all of it was written
    assert cancelled["error"] == (
        "Stopped: 0 changes were not written. What was written is being undone."
    )
    [undo_edit] = (await owner.get(f"/api/servers/{sid}/files/changes")).json()[:1]
    assert (undo_edit["kind"], undo_edit["total"]) == ("undo", 2)
    await apply_tag_changes.func(set_id=undo_edit["id"])
    assert _titles(music) == before


async def test_cancelling_artwork_puts_the_old_pictures_back(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    # OK Computer's cover: Plex's own, selected; then a job uploads a new one.
    plex.library.posters[200] = [
        {"ratingKey": "metadata://posters/agent", "provider": "agent", "selected": True}
    ]
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        us = await db.get(UserServer, sid)  # the app's server ids are the user's links
        assert us is not None
        server_id = us.server_id
        db.add(AlbumCover(server_id=server_id, rating_key=200, source="plex"))
        job = ArtworkJob(
            server_id=server_id,
            user_server_id=us.id,
            kind="covers",
            status="running",
            started_at=dt.datetime.now(dt.UTC),
        )
        db.add(job)
        await db.flush()
        client = client_for(get_settings(), http, us)
        before = await undo.remember(db, client, job, "albums", 200)
        assert before["poster"] == "metadata://posters/agent"
        await client.upload_poster(200, b"new cover")
        cover = await db.get(AlbumCover, (server_id, 200))
        assert cover is not None
        cover.source, cover.source_url = "itunes", "https://is1-ssl.mzstatic.com/x.jpg"
        undo.keep(job, "albums", 200, before)
        job_id = job.id
        await db.commit()
    stop = f"/api/jobs/artwork/{job_id}/stop"
    friend, _ = await sign_in(app, plex, "friend-token")
    assert (await friend.post(stop, json={}, headers=CSRF)).status_code == 404

    # Running: asked to, and the worker would do it. Its worker gone: the app does it.
    asked = (await owner.post(stop, json={"cancel": True}, headers=CSRF)).json()
    assert (asked["status"], asked["stop_requested"]) == ("running", "cancel")
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ArtworkJob)
            .where(ArtworkJob.id == job_id)
            .values(updated_at=func.now() - dt.timedelta(hours=1))
        )
        await db.commit()
    cancelled = (await owner.post(stop, json={"cancel": True}, headers=CSRF)).json()
    assert cancelled == {
        "status": "failed",
        "error": "Cancelled: 1 picture was put back as they were.",
        "stop_requested": "cancel",
    }
    assert plex.library.selected == [(200, "metadata://posters/agent")]
    async with get_sessionmaker()() as db:
        cover = await db.get(AlbumCover, (server_id, 200))
        assert cover is not None and (cover.source, cover.source_url) == ("plex", None)
        job_row = await db.get(ArtworkJob, job_id)
        assert job_row is not None and job_row.undo == {}


async def test_cancelling_a_covers_job(
    app: FastAPI,
    plex: FakePlex,
    web: dict[str, list[httpx.Request]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker's side: a covers job cancelled part way selects the covers it replaced
    again in Plex."""
    from plekstender.artwork import covers
    from plekstender.worker import artwork_job

    monkeypatch.setattr(covers, "BEST_PACE", 0)
    monkeypatch.setitem(SIZES, 203, (400, 400))  # Secret Album small too, after OKNOTOK
    owner, sid = await owner_with_library(app, plex)
    await measure()
    plex.library.posters[201] = [
        {"ratingKey": "metadata://posters/small", "provider": "agent", "selected": True}
    ]
    job = (
        await owner.post(
            f"/api/servers/{sid}/artwork/albums/best",
            json={"show": "needs", "sources": ["itunes"]},
            headers=CSRF,
        )
    ).json()
    _asking(covers, ArtworkJob, monkeypatch, 2, "cancel")  # before Secret Album
    await artwork_job.func(job_id=job["id"])
    [done] = (await owner.get(f"/api/servers/{sid}/artwork/jobs")).json()[:1]
    assert done["stats"]["applied"] == 1, done
    assert (done["status"], done["error"]) == (
        "failed",
        "Cancelled: 1 picture was put back as they were.",
    )
    assert plex.library.uploaded[-1][0] == 201
    assert plex.library.selected == [(201, "metadata://posters/small")]
