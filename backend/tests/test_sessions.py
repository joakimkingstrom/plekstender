"""The session watcher: skips, players and locations, and the IP location database."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI

from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.history import geo, sessions
from plekstender.worker import capture_connections
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_plex_capture import capture
from tests.test_sync import connect, owner_with_library, run_queued_syncs


class FixedGeo:
    def lookup(self, address: str | None) -> str | None:
        return {"81.2.69.142": "London, United Kingdom"}.get(address or "")


async def no_sleep(_: float) -> None:
    return None


async def poll() -> None:
    """One round of what the worker does every 10 seconds."""
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        connections = await capture_connections(db)
        await sessions.watch(db, http, get_settings(), connections, 1, no_sleep)


@pytest.fixture(autouse=True)
def fixed_geo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "geo_lookup", lambda _settings: FixedGeo())


@pytest.mark.db
async def test_watcher_records_skips_players_and_places(app: FastAPI, plex: FakePlex) -> None:
    lib = plex.library
    owner, sid = await owner_with_library(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()

    owner_play = lib.playing(1, 300, 30_000, "s1", lan=True, address="192.168.1.20")
    friend_play = lib.playing(2, 304, 100_000, "s2", lan=False, address="81.2.69.142")
    lib.now_playing = [owner_play, friend_play]
    await poll()
    lib.now_playing = [{**owner_play, "viewOffset": 60_000}]  # the friend stopped
    await poll()
    lib.now_playing = []  # the owner skipped at 1:00 of 4:00
    await poll()

    skipped = (await owner.get("/api/stats/skips", params={"server_id": sid, "range": "7d"})).json()
    assert (skipped["threshold"], skipped["watching"]) == (80, True)
    assert [(i["name"], i["artist"], i["skips"], i["plays"]) for i in skipped["items"]] == [
        ("Paranoid Android", "Radiohead", 1, 1)
    ]
    by_artist = (
        await owner.get("/api/stats/skips", params={"server_id": sid, "kind": "artist"})
    ).json()
    assert [i["name"] for i in by_artist["items"]] == ["Radiohead"]
    # Only plays where less than the threshold was heard count: at 20%, 1:00 of 4:00 isn't one.
    await owner.put("/api/auth/me/preferences", json={"skip_threshold": 20}, headers=CSRF)
    lenient = (await owner.get("/api/stats/skips", params={"server_id": sid})).json()
    assert (lenient["threshold"], lenient["items"]) == (20, [])

    # When Plex records the friend's play, it gets the player and place seen live.
    lib.play(2, 304, int(time.time()))
    await capture()
    [listen] = (await friend.get("/api/listens")).json()["items"]
    assert (listen["client"], listen["network"], listen["address"], listen["location"]) == (
        "Living room · Plexamp",
        "wan",
        "81.2.69.142",
        "London, United Kingdom",
    )


def test_player_names() -> None:
    assert sessions.describe_player({"title": "iPhone", "product": "Plexamp"}) == "iPhone · Plexamp"
    assert sessions.describe_player({"title": "Plexamp", "product": "Plexamp"}) == "Plexamp"
    assert sessions.describe_player({}) is None


def test_local_addresses_need_no_database(tmp_path: Any) -> None:
    lookup = geo.GeoLookup(tmp_path / "missing.mmdb")
    assert lookup.lookup("192.168.1.20") == "Local network"
    assert lookup.lookup("81.2.69.142") is None  # no database yet
    assert lookup.lookup("not an ip") is None


async def test_database_download_falls_back_and_never_raises(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as dt

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    now = dt.datetime(2026, 12, 1, tzinfo=dt.UTC)
    with respx.mock() as router:
        current = router.get("https://download.db-ip.com/free/dbip-city-lite-2026-12.mmdb.gz").mock(
            return_value=httpx.Response(404)
        )
        previous = router.get(
            "https://download.db-ip.com/free/dbip-city-lite-2026-11.mmdb.gz"
        ).mock(return_value=httpx.Response(200, content=b"not gzip"))
        async with httpx.AsyncClient() as http:
            assert await geo.update_database(http, get_settings(), now) is False
    assert current.called and previous.called
    assert not (tmp_path / "geoip" / geo.FILE).exists()
