"""Scrobbling new Plex plays to ListenBrainz."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi import FastAPI

from plekstender.worker import submit_listenbrainz
from tests.fakes import CSRF, FakeListenBrainz, FakePlex
from tests.test_plex_capture import capture
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db

TOKEN = "lb-token-1111"


async def connect_listenbrainz(owner: httpx.AsyncClient, token: str = TOKEN) -> dict:
    resp = await owner.put("/api/listens/listenbrainz", json={"token": token}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def status(owner: httpx.AsyncClient) -> dict:
    return (await owner.get("/api/listens/listenbrainz")).json()


async def submit(user_id: int = 1) -> None:
    """What the worker runs after a capture (and every 15 minutes)."""
    await submit_listenbrainz.func(user_id=user_id)


def later(seconds: int) -> int:
    """A Plex viewedAt after scrobbling was turned on (plays start 4 minutes earlier)."""
    return int(time.time()) + 300 + seconds


async def test_new_plex_plays_are_scrobbled(
    app: FastAPI, plex: FakePlex, listenbrainz: FakeListenBrainz
) -> None:
    lib = plex.library
    lib.play(1, 301, int(time.time()) - 86_400)  # before scrobbling was turned on
    owner, _ = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]

    assert (await status(owner))["connected"] is False
    bad = await owner.put(
        "/api/listens/listenbrainz", json={"token": "nope-nope-nope"}, headers=CSRF
    )
    assert bad.status_code == 400
    connected = await connect_listenbrainz(owner)
    assert (connected["username"], connected["pending"], connected["sent"]) == ("joakim_lb", 0, 0)

    lib.play(1, 300, later(0))
    await capture()
    assert (await status(owner))["pending"] == 1
    await submit(user_id)
    [body] = listenbrainz.submissions
    assert body["listen_type"] == "single"
    [listen] = body["payload"]
    assert listen["listened_at"] == later(0) - 240  # start of the play
    assert listen["track_metadata"] == {
        "artist_name": "Radiohead",
        "track_name": "Paranoid Android",
        "release_name": "OK Computer",
        "additional_info": {
            "media_player": "Plex",
            "submission_client": "Plekstender",
            "submission_client_version": listen["track_metadata"]["additional_info"][
                "submission_client_version"
            ],
            "duration_ms": 240_000,
        },
    }

    lib.play(1, 304, later(300))
    lib.play(1, 302, later(600))
    await capture()
    await submit(user_id)
    assert listenbrainz.submissions[-1]["listen_type"] == "import"
    assert [x["track_metadata"]["track_name"] for x in listenbrainz.listens] == [
        "Paranoid Android",
        "Glory Box",
        "Karma Police (Remastered)",
    ]
    s = await status(owner)
    assert (s["sent"], s["pending"], s["error"], s["paused"]) == (3, 0, None, False)
    assert s["last_submitted_at"] is not None

    await submit(user_id)  # nothing new: nothing sent twice
    assert len(listenbrainz.listens) == 3


async def test_failures_skip_refused_listens_and_retry_the_rest(
    app: FastAPI, plex: FakePlex, listenbrainz: FakeListenBrainz
) -> None:
    owner, _ = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await connect_listenbrainz(owner)

    # A listen ListenBrainz refuses doesn't hold up the others.
    listenbrainz.refused_tracks = {"Lucky"}
    plex.library.play(1, 303, later(0))  # Lucky
    plex.library.play(1, 304, later(300))
    await capture()
    await submit(user_id)
    assert [x["track_metadata"]["track_name"] for x in listenbrainz.listens] == ["Glory Box"]
    assert (await status(owner))["pending"] == 0

    # Server trouble: kept for the next run.
    listenbrainz.next_status = [503]
    plex.library.play(1, 300, later(600))
    await capture()
    await submit(user_id)
    s = await status(owner)
    assert (s["pending"], s["paused"]) == (1, False)
    assert "unavailable" in s["error"]
    await submit(user_id)
    assert (await status(owner))["pending"] == 0
    assert len(listenbrainz.listens) == 2

    # Rejected token: paused until a new token for the same account is pasted.
    del listenbrainz.tokens[TOKEN]
    plex.library.play(1, 301, later(900))
    await capture()
    await submit(user_id)
    s = await status(owner)
    assert (s["paused"], s["pending"]) == (True, 1)
    assert "Paste a new token" in s["error"]
    await submit(user_id)  # paused: nothing happens
    assert len(listenbrainz.listens) == 2

    resumed = await connect_listenbrainz(owner, "lb-token-2222")
    assert (resumed["paused"], resumed["sent"]) == (False, 2)
    await submit(user_id)
    assert len(listenbrainz.listens) == 3
    assert (await status(owner))["sent"] == 3

    assert (await owner.delete("/api/listens/listenbrainz", headers=CSRF)).status_code == 204
    assert (await status(owner))["connected"] is False
