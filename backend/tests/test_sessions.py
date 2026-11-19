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
from plekstender.history import geo, places, sessions
from plekstender.worker import capture_connections
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_plex_capture import capture
from tests.test_sync import connect, owner_with_library, run_queued_syncs

PLACES = {
    "81.2.69.142": geo.Place("London", "United Kingdom", "GB", "EU"),
    "81.2.69.200": geo.Place("Oslo", "Norway", "NO", "EU"),  # the server's public address
    "203.0.113.9": geo.Place("Tokyo", "Japan", "JP", "AS"),
}


class FixedGeo:
    def available(self) -> bool:
        return True

    def place(self, address: str | None) -> geo.Place | None:
        return PLACES.get(address or "")

    def lookup(self, address: str | None) -> str | None:
        if address and address.startswith("192.168."):
            return "Local network"
        place = self.place(address)
        return place.text if place else None


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
    monkeypatch.setattr(places, "geo_lookup", lambda _settings: FixedGeo())


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


@pytest.mark.db
async def test_listens_map(app: FastAPI, plex: FakePlex) -> None:
    lib = plex.library
    owner, _ = await owner_with_library(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()

    # The owner plays at home (placed where the server is), the friend in London.
    lib.now_playing = [
        lib.playing(1, 300, 30_000, "s1", lan=True, address="192.168.1.20"),
        lib.playing(2, 304, 30_000, "s2", lan=False, address="81.2.69.142"),
    ]
    await poll()
    lib.now_playing = []
    await poll()
    now = int(time.time())
    lib.play(1, 300, now)
    lib.play(2, 304, now)
    await capture()

    mine = (await owner.get("/api/stats/places", params={"range": "7d"})).json()
    assert [(c["country_code"], c["continent_code"], c["listens"]) for c in mine["countries"]] == [
        ("NO", "EU", 1)
    ]
    assert (mine["located"], mine["total"]) == (1, 1)
    assert mine["attribution"] == "IP geolocation by DB-IP"
    theirs = (await friend.get("/api/stats/places", params={"range": "7d"})).json()
    assert [c["country_code"] for c in theirs["countries"]] == ["GB"]

    europe = (
        await owner.get("/api/stats/places/detail", params={"range": "7d", "continent": "eu"})
    ).json()
    assert europe["listens"] == 1
    assert [a["name"] for a in europe["top_artists"]] == ["Radiohead"]
    assert [t["name"] for t in europe["top_tracks"]] == ["Paranoid Android"]
    norway = (
        await owner.get("/api/stats/places/detail", params={"range": "7d", "country": "NO"})
    ).json()
    assert norway["cities"] == [{"name": "Local network", "listens": 1}]
    asia = (
        await owner.get("/api/stats/places/detail", params={"range": "7d", "continent": "AS"})
    ).json()
    assert (asia["listens"], asia["top_artists"]) == (0, [])
    bad = await owner.get("/api/stats/places/detail", params={"country": "N0R"})
    assert bad.status_code == 422


@pytest.mark.db
async def test_places_are_filled_in_later(app: FastAPI, plex: FakePlex) -> None:
    from sqlalchemy import select, update

    from plekstender.db.models import Listen, PlexServer

    owner, _ = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    async with get_sessionmaker()() as db:
        # A server connected before public addresses were stored.
        await db.execute(update(PlexServer).values(public_address=None))
        await db.commit()
        async with httpx.AsyncClient() as http:
            assert await places.refresh_public_addresses(db, http, get_settings()) == 1
        server = await db.scalar(select(PlexServer))
        assert server is not None and server.public_address == "81.2.69.200"

    from tests.test_listenbrainz_send import add

    await add(user_id, "plex", [(1_790_000_000 + i * 600, "Radiohead", "Lucky") for i in range(4)])
    async with get_sessionmaker()() as db:
        ids = list(await db.scalars(select(Listen.id).order_by(Listen.id)))
        sid = (await db.scalar(select(PlexServer))).id  # type: ignore[union-attr]
        for listen_id, (address, network) in zip(
            ids,
            [("203.0.113.9", "wan"), ("192.168.1.5", "lan"), ("8.8.8.8", "wan"), (None, None)],
            strict=True,
        ):
            await db.execute(
                update(Listen)
                .where(Listen.id == listen_id)
                .values(address=address, network=network, server_id=sid)
            )
        await db.commit()
        assert await places.fill_places(db, get_settings()) == 3
        rows = (
            await db.execute(
                select(Listen.country_code, Listen.continent_code, Listen.location).order_by(
                    Listen.id
                )
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("JP", "AS", "Tokyo, Japan"),
        ("NO", "EU", None),  # at home: the server's place
        ("", None, None),  # unknown to the database: not looked up again
        (None, None, None),  # no address (e.g. an imported scrobble)
    ]
    async with get_sessionmaker()() as db:
        assert await places.fill_places(db, get_settings()) == 0
