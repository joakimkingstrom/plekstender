"""Capturing plays from Plex's play history, and duplicates with Last.fm scrobbles."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from plekstender.db import get_sessionmaker
from plekstender.db.models import Listen, UserServer
from plekstender.worker import capture_connections, capture_plex_plays, schedule_play_capture
from tests.fakes import CSRF, LASTFM_KEY, FakeLastFm, FakePlex, sign_in
from tests.test_listens import run_queued_imports
from tests.test_sync import connect, owner_with_library, run_queued_syncs

pytestmark = pytest.mark.db

T = int(time.time()) - 86_400  # yesterday
DURATION = 240  # every fake track is 4 minutes long


async def capture() -> list[int]:
    """What the worker does every 5 minutes. Returns the connections it read."""
    async with get_sessionmaker()() as db:
        ids = [us.id for us in await capture_connections(db)]
    for us_id in ids:
        await capture_plex_plays.func(user_server_id=us_id)
    return ids


async def plex_listens() -> list[tuple[int, str, str]]:
    async with get_sessionmaker()() as db:
        rows = await db.execute(
            select(Listen.user_id, Listen.artist_name, Listen.track_name)
            .where(Listen.source == "plex")
            .order_by(Listen.listened_at)
        )
        return [tuple(r) for r in rows.all()]


async def owner_and_friend(
    app: FastAPI, plex: FakePlex
) -> tuple[httpx.AsyncClient, httpx.AsyncClient, int]:
    owner, sid = await owner_with_library(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()
    return owner, friend, sid


async def test_owner_connection_captures_everyones_plays(app: FastAPI, plex: FakePlex) -> None:
    lib = plex.library
    lib.play(1, 300, T)  # owner: Paranoid Android
    lib.play(1, 304, T + 600)  # owner: Glory Box
    lib.play(2, 304, T + 1200)  # friend
    lib.play(999, 301, T + 1800)  # someone who doesn't use Plekstender
    lib.play(1, 900, T + 100, section="1")  # a movie
    owner, friend, sid = await owner_and_friend(app, plex)

    async with get_sessionmaker()() as db:
        owner_us = await db.scalar(select(UserServer.id).where(UserServer.is_owner.is_(True)))
    assert await capture() == [owner_us]  # only the owner's connection is read
    assert await schedule_play_capture() == 1

    owner_items = (await owner.get("/api/listens")).json()["items"]
    assert [(i["artist_name"], i["track_name"], i["source"]) for i in owner_items] == [
        ("Portishead", "Glory Box", "plex"),
        ("Radiohead", "Paranoid Android", "plex"),
    ]
    # Stored as the start of the play: Plex records it when the track ends.
    assert owner_items[1]["listened_at"].startswith(
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(T - DURATION))
    )
    friend_items = (await friend.get("/api/listens")).json()["items"]
    assert [i["track_name"] for i in friend_items] == ["Glory Box"]
    assert len(await plex_listens()) == 3  # nothing for account 999 or the movie

    status = (await owner.get("/api/listens/plex")).json()
    assert [
        (
            s["server_id"],
            s["server_name"],
            s["via_owner"],
            s["backfilled"],
            s["listens"],
            s["error"],
        )
        for s in status
    ] == [(sid, "Home", True, True, 2, None)]
    assert status[0]["checked_at"] is not None
    friend_status = (await friend.get("/api/listens/plex")).json()
    assert [(s["via_owner"], s["listens"]) for s in friend_status] == [(True, 1)]

    # Reading again stores nothing twice; new plays are picked up.
    await capture()
    assert len(await plex_listens()) == 3
    lib.play(1, 301, int(time.time()))
    await capture()
    assert len(await plex_listens()) == 4
    assert lib.requests["history"] >= 3


async def test_shared_users_own_connection_when_owner_is_absent(
    app: FastAPI, plex: FakePlex
) -> None:
    plex.library.play(1, 300, T)
    plex.library.play(2, 304, T + 600)
    owner, friend, sid = await owner_and_friend(app, plex)
    assert (await owner.delete(f"/api/servers/{sid}", headers=CSRF)).status_code == 204

    await capture()  # now through the friend's connection, which only sees their plays
    assert [(a, t) for _, a, t in await plex_listens()] == [("Portishead", "Glory Box")]
    assert [i["track_name"] for i in (await friend.get("/api/listens")).json()["items"]] == [
        "Glory Box"
    ]
    status = (await friend.get("/api/listens/plex")).json()
    assert [(s["via_owner"], s["backfilled"], s["listens"]) for s in status] == [(False, True, 1)]

    plex.library.history_denied.add("srv-friend")
    plex.library.play(2, 300, int(time.time()))
    await capture()
    status = (await friend.get("/api/listens/plex")).json()
    assert "didn't allow" in status[0]["error"]
    assert len(await plex_listens()) == 1


async def test_lastfm_scrobbles_of_plex_plays_count_once(
    app: FastAPI, plex: FakePlex, lastfm: FakeLastFm
) -> None:
    plex.library.play(1, 300, T)  # Paranoid Android, started at T - 240
    plex.library.play(1, 300, T + DURATION)  # played again straight after
    owner, _, _ = await owner_and_friend(app, plex)
    await capture()

    def scrobble(uts: int, track: str = "Paranoid Android") -> dict[str, Any]:
        return {
            "uts": uts,
            "artist": "Radiohead",
            "album": "OK Computer",
            "track": track,
            "mbid": "",
        }

    lastfm.users["Joakim"] = [
        scrobble(T - DURATION + 5),  # the first Plex play
        scrobble(T + 3),  # the second one
        scrobble(T - 3 * 86_400),  # days earlier: a separate play
        scrobble(T - DURATION + 5, "Airbag"),  # another song at the same time
    ]
    assert (
        await owner.put("/api/admin/lastfm", json={"api_key": LASTFM_KEY}, headers=CSRF)
    ).status_code == 200
    await owner.post("/api/listens/lastfm", json={"username": "Joakim"}, headers=CSRF)
    [done] = await run_queued_imports()
    assert done.status == "succeeded"

    summary = (await owner.get("/api/listens/summary")).json()
    assert summary["total"] == 4  # 2 Plex plays + 2 Last.fm-only scrobbles
    assert summary["by_source"] == {"plex": 2, "lastfm": 2}
    async with get_sessionmaker()() as db:
        duplicates = await db.scalar(
            select(func.count()).select_from(Listen).where(Listen.duplicate_of.is_not(None))
        )
    assert duplicates == 2
    items = (await owner.get("/api/listens", params={"limit": 10})).json()["items"]
    assert sum(i["duplicate"] for i in items) == 2
