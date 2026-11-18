"""Importing ListenBrainz history, duplicates with Last.fm, and exporting listens."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import Listen, ListenImport
from plekstender.history import listenbrainz_import
from plekstender.history.lastfm import Scrobble
from plekstender.history.listenbrainz_import import run_listenbrainz_import
from plekstender.history.store import insert_listens, scrobble_row
from tests.fakes import CSRF, FakeListenBrainz, FakePlex, sign_in

pytestmark = pytest.mark.db

START = 1_700_000_000
MBID = "b4b5b1f0-1111-4222-8333-944455556666"


@pytest.fixture(autouse=True)
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(listenbrainz_import, "PAGE", 50)


async def no_sleep(_: float) -> None:
    return None


async def run_imports() -> list[ListenImport]:
    sessions = get_sessionmaker()
    async with sessions() as db:
        ids = list(await db.scalars(select(ListenImport.id).where(ListenImport.status == "queued")))
    async with httpx.AsyncClient() as http:
        for import_id in ids:
            async with sessions() as db:
                await run_listenbrainz_import(db, http, get_settings(), import_id, sleep=no_sleep)
    async with sessions() as db:
        return list(await db.scalars(select(ListenImport).where(ListenImport.id.in_(ids))))


def history(lb: FakeListenBrainz, count: int, start: int = START) -> None:
    for i in range(count):
        lb.add_history("joakim_lb", start + i * 600, "Radiohead", f"Song {i}", album="OK Computer")


async def test_listenbrainz_import(
    app: FastAPI, plex: FakePlex, listenbrainz: FakeListenBrainz
) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    history(listenbrainz, 120)
    # Three listens sharing one timestamp, right at a page boundary.
    for name in ("Same A", "Same B", "Same C"):
        listenbrainz.add_history("joakim_lb", START + 70 * 600 + 1, "Portishead", name)
    listenbrainz.add_history(
        "joakim_lb", START + 5, "Portishead", "Glory Box", duration_ms=301_000, recording_mbid=MBID
    )

    unknown = await owner.post(
        "/api/listens/imports/listenbrainz", json={"username": "nobody"}, headers=CSRF
    )
    assert unknown.status_code == 400
    assert "no user called" in unknown.json()["detail"]
    started = await owner.post(
        "/api/listens/imports/listenbrainz", json={"username": "joakim_lb"}, headers=CSRF
    )
    assert started.status_code == 201, started.text
    assert (started.json()["source"], started.json()["progress"]) == (
        "listenbrainz",
        {"total": 124},
    )

    [done] = await run_imports()
    assert done.status == "succeeded", done.error
    assert done.stats["added"] == 124
    async with get_sessionmaker()() as db:
        glory = await db.scalar(select(Listen).where(Listen.track_name == "Glory Box"))
    assert glory is not None
    assert (glory.source, glory.duration_ms, glory.recording_mbid) == (
        "listenbrainz",
        301_000,
        MBID,
    )
    status = (await owner.get("/api/listens/imports/listenbrainz")).json()
    assert (status["available"], status["username"], status["last"]["status"]) == (
        True,
        "joakim_lb",
        "succeeded",
    )

    # A later import stops at the end of the previous one.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ListenImport).values(
                window_to=dt.datetime.fromtimestamp(START + 200 * 600, dt.UTC)
            )
        )
        await db.commit()
    history(listenbrainz, 3, start=START + 201 * 600)
    listenbrainz.listen_requests.clear()
    await owner.post(
        "/api/listens/imports/listenbrainz", json={"username": "joakim_lb"}, headers=CSRF
    )
    [again] = await run_imports()
    assert again.stats == {"added": 3, "existing": 0}
    assert len(listenbrainz.listen_requests) == 1


async def test_failed_import_resumes(
    app: FastAPI, plex: FakePlex, listenbrainz: FakeListenBrainz, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    history(listenbrainz, 120)
    monkeypatch.setattr(listenbrainz_import, "MAX_ATTEMPTS", 1)
    await owner.post(
        "/api/listens/imports/listenbrainz", json={"username": "joakim_lb"}, headers=CSRF
    )
    listenbrainz.fail_listens_from = 2  # first page fine, then ListenBrainz fails
    sessions = get_sessionmaker()
    [failed] = await run_imports()
    assert failed.status == "failed"
    assert "unavailable" in (failed.error or "")
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Listen)) == 50

    listenbrainz.fail_listens_from = None
    resumed = await owner.post(f"/api/listens/imports/{failed.id}/resume", headers=CSRF)
    assert resumed.status_code == 200
    [done] = await run_imports()
    assert done.status == "succeeded"
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Listen)) == 120


async def test_listenbrainz_copies_of_lastfm_scrobbles_count_once_and_export(
    app: FastAPI, plex: FakePlex, listenbrainz: FakeListenBrainz
) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    lastfm = [
        Scrobble(
            listened_at=dt.datetime.fromtimestamp(START + i * 600, dt.UTC),
            artist="Radiohead",
            album="OK Computer",
            track=f"Song {i}",
            artist_mbid=None,
            release_mbid=None,
            recording_mbid=None,
        )
        for i in range(10)
    ]
    async with get_sessionmaker()() as db:
        await insert_listens(db, [scrobble_row(user_id, None, "lastfm", s) for s in lastfm])
        await db.commit()
    history(listenbrainz, 15)  # the same 10 (imported from Last.fm into ListenBrainz) + 5 more

    await owner.post(
        "/api/listens/imports/listenbrainz", json={"username": "joakim_lb"}, headers=CSRF
    )
    await run_imports()
    summary = (await owner.get("/api/listens/summary")).json()
    assert summary["total"] == 15
    assert summary["by_source"] == {"lastfm": 10, "listenbrainz": 5}

    export = await owner.get("/api/listens/export.jsonl")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in export.headers["content-disposition"]
    lines = [json.loads(line) for line in export.text.splitlines()]
    assert len(lines) == 15  # duplicates left out
    assert [x["listened_at"] for x in lines] == sorted(x["listened_at"] for x in lines)
    first = lines[0]
    assert first["track_metadata"]["artist_name"] == "Radiohead"
    assert first["track_metadata"]["release_name"] == "OK Computer"
    assert "media_player" not in first["track_metadata"]["additional_info"]

    # Deleting ListenBrainz listens forgets that import too.
    deleted = await owner.delete("/api/listens", params={"source": "listenbrainz"}, headers=CSRF)
    assert deleted.json() == {"deleted": 15}
    assert (await owner.get("/api/listens/imports/listenbrainz")).json()["last"] is None
