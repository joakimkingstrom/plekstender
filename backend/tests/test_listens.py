"""Listening history: Last.fm API key, Last.fm import, listing and deleting listens."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select

from roadiefm.config import get_settings
from roadiefm.db import get_sessionmaker
from roadiefm.db.models import Listen, ListenImport
from roadiefm.history import lastfm_import
from roadiefm.history.lastfm import parse_scrobble
from roadiefm.history.lastfm_import import run_lastfm_import
from roadiefm.sync.parse import normalise_artist, normalise_title
from tests.fakes import CSRF, LASTFM_KEY, FakeLastFm, FakePlex, sign_in
from tests.test_sync import connect

START = 1_600_000_000  # Sept 2020


def test_names_are_normalised_for_matching() -> None:
    assert normalise_artist("The Beatles") == normalise_artist("beatles")
    assert normalise_artist("Beyoncé") == "beyonce"
    assert normalise_artist("Simon & Garfunkel") == "simon and garfunkel"
    assert normalise_artist("The The") == "the"
    assert normalise_title("Karma Police (2017 Remaster)") == "karma police"


def test_parse_scrobble_skips_now_playing_and_incomplete_entries() -> None:
    assert (
        parse_scrobble({"name": "Y", "artist": {"#text": "X"}, "@attr": {"nowplaying": "true"}})
        is None
    )
    assert parse_scrobble({"name": "Y", "artist": {"#text": "X"}}) is None  # no date
    s = parse_scrobble(
        {
            "name": " Airbag ",
            "artist": {"#text": "Radiohead", "mbid": "a74b1b7f-71a5-4011-9441-d0b5e4122711"},
            "album": {"#text": ""},
            "mbid": "not-an-mbid",
            "date": {"uts": "1600000000"},
        }
    )
    assert s is not None
    assert (s.track, s.artist, s.album) == ("Airbag", "Radiohead", None)
    assert s.artist_mbid == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    assert s.recording_mbid is None


async def no_sleep(_: float) -> None:
    return None


async def run_queued_imports() -> list[ListenImport]:
    """What the worker does: run queued imports. Returns them afterwards."""
    sessions = get_sessionmaker()
    async with sessions() as db:
        ids = list(
            await db.scalars(
                select(ListenImport.id)
                .where(ListenImport.status == "queued")
                .order_by(ListenImport.id)
            )
        )
    async with httpx.AsyncClient() as http:
        for import_id in ids:
            async with sessions() as db:
                await run_lastfm_import(db, http, get_settings(), import_id, sleep=no_sleep)
    async with sessions() as db:
        return list(
            await db.scalars(
                select(ListenImport).where(ListenImport.id.in_(ids)).order_by(ListenImport.id)
            )
        )


@pytest.fixture(autouse=True)
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lastfm_import, "PAGE_LIMIT", 50)


async def admin_with_key(app: FastAPI, plex: FakePlex) -> httpx.AsyncClient:
    owner, _ = await sign_in(app, plex, "owner-token")
    resp = await owner.put("/api/admin/lastfm", json={"api_key": LASTFM_KEY}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    return owner


@pytest.mark.db
async def test_admin_sets_lastfm_key(app: FastAPI, plex: FakePlex, lastfm: FakeLastFm) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    assert (await owner.get("/api/admin/lastfm")).json() == {"configured": False, "source": None}
    status = (await owner.get("/api/listens/lastfm")).json()
    assert status["available"] is False

    bad_format = await owner.put("/api/admin/lastfm", json={"api_key": "nope"}, headers=CSRF)
    assert bad_format.status_code == 422
    rejected = await owner.put("/api/admin/lastfm", json={"api_key": "f" * 32}, headers=CSRF)
    assert rejected.status_code == 400
    assert "Last.fm Pro" in rejected.json()["detail"]

    ok = await owner.put("/api/admin/lastfm", json={"api_key": LASTFM_KEY}, headers=CSRF)
    assert ok.json() == {"configured": True, "source": "settings"}
    assert (await owner.get("/api/listens/lastfm")).json()["available"] is True

    cleared = await owner.put("/api/admin/lastfm", json={"api_key": None}, headers=CSRF)
    assert cleared.json() == {"configured": False, "source": None}


@pytest.mark.db
async def test_lastfm_key_from_environment(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "lastfm_api_key", SecretStr(LASTFM_KEY))
    owner, _ = await sign_in(app, plex, "owner-token")
    assert (await owner.get("/api/admin/lastfm")).json() == {
        "configured": True,
        "source": "environment",
    }


@pytest.mark.db
async def test_lastfm_key_is_admin_only(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    await connect(owner)  # approves the server so the friend may sign in
    friend, _ = await sign_in(app, plex, "friend-token")
    assert (await friend.get("/api/admin/lastfm")).status_code == 403
    put = await friend.put("/api/admin/lastfm", json={"api_key": LASTFM_KEY}, headers=CSRF)
    assert put.status_code == 403


@pytest.mark.db
async def test_lastfm_import(app: FastAPI, plex: FakePlex, lastfm: FakeLastFm) -> None:
    lastfm.add_scrobbles("Joakim", 120, start=START)
    owner = await admin_with_key(app, plex)

    unknown = await owner.post("/api/listens/lastfm", json={"username": "nobody"}, headers=CSRF)
    assert unknown.status_code == 400
    assert "no user called" in unknown.json()["detail"]

    started = await owner.post("/api/listens/lastfm", json={"username": "joakim"}, headers=CSRF)
    assert started.status_code == 201, started.text
    job = started.json()
    assert (job["username"], job["status"], job["window_from"]) == ("Joakim", "queued", None)
    again = await owner.post("/api/listens/lastfm", json={"username": "joakim"}, headers=CSRF)
    assert again.status_code == 409

    [done] = await run_queued_imports()
    assert done.status == "succeeded", done.error
    assert done.stats == {"added": 120, "existing": 0}
    assert done.progress == {"page": 3, "pages": 3, "done": 120, "total": 120}

    summary = (await owner.get("/api/listens/summary")).json()
    assert summary["total"] == 120
    assert summary["by_source"] == {"lastfm": 120}
    page = (await owner.get("/api/listens", params={"limit": 2})).json()
    assert page["total"] == 120
    assert [i["track_name"] for i in page["items"]] == ["Song 119", "Song 118"]
    assert page["items"][0]["artist_name"] == "The Beatles"
    async with get_sessionmaker()() as db:
        first = await db.scalar(select(Listen).where(Listen.track_name == "Song 0"))
    assert first is not None
    assert (first.artist_key, first.album_key, first.track_key) == (
        "radiohead",
        "ok computer",
        "song 0",
    )
    assert first.recording_mbid == "c3e6f8a2-1111-4222-8333-944455556666"

    status = (await owner.get("/api/listens/lastfm")).json()
    assert status["username"] == "Joakim"
    assert status["active"] is None
    assert status["last"]["status"] == "succeeded"

    # A second import only asks for scrobbles after the first one's window.
    lastfm.add_scrobbles("Joakim", 3, start=int(time.time()) + 60)
    lastfm.requests.clear()
    second = (
        await owner.post("/api/listens/lastfm", json={"username": "Joakim"}, headers=CSRF)
    ).json()
    assert second["window_from"] == job["window_to"]
    [done] = await run_queued_imports()
    assert done.stats == {"added": 0, "existing": 0}  # the new ones are after window_to
    assert all("from" in r for r in lastfm.requests if r["method"] == "user.getRecentTracks")


@pytest.mark.db
async def test_import_retries_and_resumes(
    app: FastAPI, plex: FakePlex, lastfm: FakeLastFm, monkeypatch: pytest.MonkeyPatch
) -> None:
    lastfm.add_scrobbles("Joakim", 120, start=START)
    owner = await admin_with_key(app, plex)

    lastfm.fail_pages = {2: 2}  # two temporary errors: retried within the job
    await owner.post("/api/listens/lastfm", json={"username": "Joakim"}, headers=CSRF)
    [done] = await run_queued_imports()
    assert done.status == "succeeded"
    assert done.stats["added"] == 120

    await owner.delete("/api/listens", params={"source": "lastfm"}, headers=CSRF)
    monkeypatch.setattr(lastfm_import, "MAX_ATTEMPTS", 2)
    lastfm.fail_pages = {2: 5}  # keeps failing: the job stops after page 1
    await owner.post("/api/listens/lastfm", json={"username": "Joakim"}, headers=CSRF)
    [failed] = await run_queued_imports()
    assert failed.status == "failed"
    assert "temporary error" in (failed.error or "")
    assert failed.progress["page"] == 1
    assert (await owner.get("/api/listens/summary")).json()["total"] == 50

    lastfm.fail_pages = {}
    resumed = await owner.post(f"/api/listens/imports/{failed.id}/resume", headers=CSRF)
    assert resumed.status_code == 200
    lastfm.requests.clear()
    [done] = await run_queued_imports()
    assert done.status == "succeeded"
    assert done.stats == {"added": 120, "existing": 0}
    assert [r["page"] for r in lastfm.requests] == ["2", "3"]  # resumed after page 1
    assert (await owner.get("/api/listens/summary")).json()["total"] == 120

    not_failed = await owner.post(f"/api/listens/imports/{failed.id}/resume", headers=CSRF)
    assert not_failed.status_code == 409


@pytest.mark.db
async def test_private_profile_fails_with_explanation(
    app: FastAPI, plex: FakePlex, lastfm: FakeLastFm
) -> None:
    owner = await admin_with_key(app, plex)
    started = await owner.post("/api/listens/lastfm", json={"username": "hidden"}, headers=CSRF)
    assert started.status_code == 201
    [failed] = await run_queued_imports()
    assert failed.status == "failed"
    assert "hides its recent listening" in (failed.error or "")


@pytest.mark.db
async def test_rejected_key_during_import(app: FastAPI, plex: FakePlex, lastfm: FakeLastFm) -> None:
    lastfm.add_scrobbles("Joakim", 10, start=START)
    owner = await admin_with_key(app, plex)
    await owner.post("/api/listens/lastfm", json={"username": "Joakim"}, headers=CSRF)
    lastfm.valid_keys.clear()  # e.g. 1 December, without Pro
    [failed] = await run_queued_imports()
    assert failed.status == "failed"
    assert "Last.fm Pro" in (failed.error or "")


@pytest.mark.db
async def test_listens_are_private_and_deletable(
    app: FastAPI, plex: FakePlex, lastfm: FakeLastFm
) -> None:
    lastfm.add_scrobbles("Joakim", 10, start=START)
    owner = await admin_with_key(app, plex)
    await connect(owner)
    friend, _ = await sign_in(app, plex, "friend-token")
    await owner.post("/api/listens/lastfm", json={"username": "Joakim"}, headers=CSRF)
    await run_queued_imports()

    assert (await friend.get("/api/listens")).json() == {"items": [], "total": 0}
    assert (await friend.get("/api/listens/lastfm")).json()["last"] is None
    listen_id: Any = (await owner.get("/api/listens")).json()["items"][0]["id"]
    assert (await friend.delete(f"/api/listens/{listen_id}", headers=CSRF)).status_code == 404
    deleted = await friend.delete("/api/listens", headers=CSRF)
    assert deleted.json() == {"deleted": 0}

    assert (await owner.delete(f"/api/listens/{listen_id}", headers=CSRF)).status_code == 204
    assert (await owner.get("/api/listens")).json()["total"] == 9
    deleted = await owner.delete("/api/listens", params={"source": "lastfm"}, headers=CSRF)
    assert deleted.json() == {"deleted": 9}
    status = (await owner.get("/api/listens/lastfm")).json()
    assert (status["last"], status["username"]) == (None, None)
    async with get_sessionmaker()() as db:
        assert await db.scalar(select(func.count()).select_from(Listen)) == 0
