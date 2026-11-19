"""Sending the whole listening history to ListenBrainz."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi import FastAPI

from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.history import listenbrainz_send
from plekstender.history.lastfm import Scrobble
from plekstender.history.listenbrainz_send import run_listenbrainz_send
from plekstender.history.store import insert_listens, scrobble_row
from tests.fakes import CSRF, FakeListenBrainz, FakePlex
from tests.test_listenbrainz import connect_listenbrainz
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db

T = 1_700_000_000


async def add(user_id: int, source: str, plays: list[tuple[int, str, str]]) -> None:
    rows = [
        scrobble_row(
            user_id,
            None,
            source,
            Scrobble(
                listened_at=dt.datetime.fromtimestamp(ts, dt.UTC),
                artist=artist,
                album=None,
                track=track,
                artist_mbid=None,
                release_mbid=None,
                recording_mbid=None,
            ),
        )
        for ts, artist, track in plays
    ]
    async with get_sessionmaker()() as db:
        await insert_listens(db, rows)
        await db.commit()


async def no_wait(_: float) -> None:
    return None


async def run(job_id: int) -> None:
    async with httpx.AsyncClient() as http, get_sessionmaker()() as db:
        await run_listenbrainz_send(db, http, get_settings(), job_id, sleep=no_wait)


async def test_send_history(
    app: FastAPI,
    plex: FakePlex,
    listenbrainz: FakeListenBrainz,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(listenbrainz_send, "BATCH", 2)  # several batches, and the cursor
    owner, _ = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]

    resp = await owner.post("/api/listens/listenbrainz/send", headers=CSRF)
    assert resp.status_code == 400  # not connected
    await connect_listenbrainz(owner)

    await add(
        user_id,
        "lastfm",
        [
            (T, "Radiohead", "Karma Police"),
            (T + 1000, "Radiohead", "Lucky"),
            (T + 2000, "Portishead", "Glory Box"),
            (T + 3000, "Portishead", "Roads"),
            (T + 4000, "Radiohead", "Airbag"),
        ],
    )
    await add(user_id, "listenbrainz", [(T + 5000, "Muse", "Hysteria")])  # came from there
    # Already on ListenBrainz (e.g. from its own Last.fm importer), named a bit differently.
    listenbrainz.add_history("joakim_lb", T + 30, "Radiohead", "karma police")
    listenbrainz.add_history("joakim_lb", T + 2000, "Portishead", "Glory Box (Remastered)")
    # The same song much later is another play: Lucky still gets sent.
    listenbrainz.add_history("joakim_lb", T + 1000 + 600, "Radiohead", "Lucky")
    listenbrainz.refused_tracks.add("Roads")
    listenbrainz.next_status = [429]  # one rate limit, retried

    resp = await owner.post("/api/listens/listenbrainz/send", headers=CSRF)
    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert (job["source"], job["status"], job["progress"]) == ("lb_send", "queued", {"total": 5})
    again = await owner.post("/api/listens/listenbrainz/send", headers=CSRF)
    assert again.status_code == 409

    await run(job["id"])
    state = (await owner.get("/api/listens/listenbrainz/send")).json()
    last = state["last"]
    assert last["status"] == "succeeded", last["error"]
    assert last["stats"] == {"sent": 2, "existing": 2, "refused": 1}
    assert last["progress"]["done"] == 5
    sent = sorted(
        (x["listened_at"], x["track_metadata"]["track_name"]) for x in listenbrainz.listens
    )
    assert sent == [(T + 1000, "Lucky"), (T + 4000, "Airbag")]
    assert all(
        body["listen_type"] == "import"
        for body in listenbrainz.submissions
        if len(body["payload"]) > 1
    )

    # Running it again sends nothing new: it's all there now.
    for x in listenbrainz.listens:
        listenbrainz.add_history(
            "joakim_lb",
            x["listened_at"],
            x["track_metadata"]["artist_name"],
            x["track_metadata"]["track_name"],
        )
    before = len(listenbrainz.listens)
    job = (await owner.post("/api/listens/listenbrainz/send", headers=CSRF)).json()
    await run(job["id"])
    assert len(listenbrainz.listens) == before


async def test_send_stops_on_a_rejected_token_and_resumes(
    app: FastAPI, plex: FakePlex, listenbrainz: FakeListenBrainz
) -> None:
    owner, _ = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await connect_listenbrainz(owner)
    await add(user_id, "plex", [(T, "Radiohead", "Lucky")])
    listenbrainz.add_history("joakim_lb", T - 10_000, "Someone", "Else")
    listenbrainz.next_status = [401]

    job = (await owner.post("/api/listens/listenbrainz/send", headers=CSRF)).json()
    await run(job["id"])
    last = (await owner.get("/api/listens/listenbrainz/send")).json()["last"]
    assert last["status"] == "failed"
    assert "rejected your token" in last["error"]

    resp = await owner.post(f"/api/listens/imports/{job['id']}/resume", headers=CSRF)
    assert resp.status_code == 200, resp.text
    await run(job["id"])
    assert [x["track_metadata"]["track_name"] for x in listenbrainz.listens] == ["Lucky"]
