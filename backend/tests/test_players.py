"""Remote control of Plex players: listing them, commands through the server or straight to
the player, and playing a song or an album."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.fakes import CSRF, RESOURCES, SERVER_URI, FakePlex, home_server, sign_in
from tests.test_sync import connect, owner_with_library

PLEXAMP = "plexamp-desktop-1"
PLEXAMP_URI = "http://192.168.1.20:32500"
PHONE = "iphone-7"


def plexamp_resource() -> dict[str, Any]:
    return {
        "name": "Plexamp (Mac)",
        "product": "Plexamp",
        "platform": "macOS",
        "productVersion": "4.12.0",
        "clientIdentifier": PLEXAMP,
        "provides": "client,player,pub-sub-player",
        "presence": True,
        "accessToken": "plexamp-token",
        "connections": [
            {"protocol": "http", "address": "203.0.113.9", "port": 32500,
             "uri": "http://203-0-113-9.x.plex.direct:32500", "local": False, "relay": True},
            {"protocol": "http", "address": "192.168.1.20", "port": 32500, "uri": PLEXAMP_URI,
             "local": True},
        ],
    }  # fmt: skip


@pytest.fixture
def players(plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Plexamp on the Mac (on the account, and the server knows it) and an iPhone the server
    knows of only; every command either of them gets."""
    monkeypatch.setitem(
        RESOURCES, "owner-token", [home_server(True, "srv-owner"), plexamp_resource()]
    )
    sent: list[httpx.Request] = []

    def clients(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"MediaContainer": {"Server": [
            {"name": "Plexamp (Mac)", "machineIdentifier": PLEXAMP, "product": "Plexamp",
             "address": "192.168.1.20", "port": 32500, "protocol": "plex"},
            {"name": "Joakim's iPhone", "machineIdentifier": PHONE, "product": "Plex for iOS",
             "address": "192.168.1.30", "port": 32500, "protocol": "plex"},
        ]}})  # fmt: skip

    def relayed(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        known = request.headers.get("X-Plex-Target-Client-Identifier") in (PLEXAMP, PHONE)
        return httpx.Response(200 if known else 404)

    def queue(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"MediaContainer": {
            "playQueueID": 77, "playQueueSelectedMetadataItemID": "300",
            "Metadata": [{"ratingKey": "300"}, {"ratingKey": "301"}],
        }})  # fmt: skip

    def direct(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    plex.router.get(f"{SERVER_URI}/clients").mock(side_effect=clients)
    plex.router.get(url__startswith=f"{SERVER_URI}/player/").mock(side_effect=relayed)
    plex.router.post(f"{SERVER_URI}/playQueues").mock(side_effect=queue)
    plex.router.get(url__startswith=f"{PLEXAMP_URI}/player/").mock(side_effect=direct)
    return sent


@pytest.mark.db
async def test_players_and_commands_through_the_server(
    app: FastAPI, plex: FakePlex, players: list[httpx.Request]
) -> None:
    owner, sid = await owner_with_library(app, plex)
    playing = plex.library.playing(1, 301, 60_000, "s1", lan=True, address="192.168.1.20")
    playing["Player"] |= {"machineIdentifier": PLEXAMP, "state": "paused"}
    playing |= {"grandparentTitle": "Radiohead", "parentTitle": "OK Computer"}
    plex.library.now_playing.append(playing)

    found = (await owner.get(f"/api/servers/{sid}/players")).json()
    assert found["direct_allowed"] is True
    mac, phone = found["players"]
    assert (mac["name"], mac["on_account"], mac["on_server"], mac["direct"]) == (
        "Plexamp (Mac)", True, True, True,
    )  # fmt: skip
    assert mac["playing"] == {
        "title": "Karma Police", "artist": "Radiohead", "album": "OK Computer",
        "state": "paused", "rating_key": 301,
    }  # fmt: skip
    assert (phone["name"], phone["on_account"], phone["on_server"]) == (
        "Joakim's iPhone", False, True,
    )  # fmt: skip

    resp = await owner.post(
        f"/api/servers/{sid}/players/{PLEXAMP}/command", json={"command": "play"}, headers=CSRF
    )
    assert resp.json() == {"ok": True, "route": "server", "detail": "The server passed it on."}
    [play] = players
    assert play.url.path == "/player/playback/play"
    assert play.headers["X-Plex-Target-Client-Identifier"] == PLEXAMP
    first_id = int(play.url.params["commandID"])

    resp = await owner.post(
        f"/api/servers/{sid}/players/gone/command", json={"command": "next"}, headers=CSRF
    )
    assert resp.json()["ok"] is False and "HTTP 404" in resp.json()["detail"]
    assert int(players[-1].url.params["commandID"]) > first_id  # players ignore older ids

    # An album: a play queue of it, then playMedia saying where the server is.
    resp = await owner.post(
        f"/api/servers/{sid}/players/{PHONE}/play", json={"rating_key": 200}, headers=CSRF
    )
    assert resp.json()["ok"] is True
    queue, media = players[-2:]
    assert queue.url.params["uri"].endswith("/library/metadata/200")
    assert media.url.path == "/player/playback/playMedia"
    assert dict(media.url.params) | {"commandID": "-"} == {
        "machineIdentifier": "home-machine", "protocol": "https",
        "address": "10-0-0-2.abc.plex.direct", "port": "32400", "token": "srv-owner",
        "key": "/library/metadata/300", "offset": "0",
        "containerKey": "/playQueues/77?window=100&own=1", "type": "music", "commandID": "-",
    }  # fmt: skip

    # Only what the person can see in their libraries.
    resp = await owner.post(
        f"/api/servers/{sid}/players/{PHONE}/play", json={"rating_key": 999}, headers=CSRF
    )
    assert resp.status_code == 404


@pytest.mark.db
async def test_commands_straight_to_a_player_are_for_admins(
    app: FastAPI, plex: FakePlex, players: list[httpx.Request]
) -> None:
    owner, sid = await owner_with_library(app, plex)
    resp = await owner.post(
        f"/api/servers/{sid}/players/{PLEXAMP}/command",
        json={"command": "pause", "route": "direct"},
        headers=CSRF,
    )
    assert resp.json() == {
        "ok": True, "route": "direct", "detail": "The player took it (192.168.1.20:32500).",
    }  # fmt: skip
    [pause] = players  # its local address, never the relay
    assert str(pause.url).startswith(f"{PLEXAMP_URI}/player/playback/pause")
    assert pause.headers["X-Plex-Token"] == "plexamp-token"

    # Someone else (not an admin) has the server relay their commands.
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = await connect(friend)
    resp = await friend.post(
        f"/api/servers/{friend_sid}/players/{PLEXAMP}/command",
        json={"command": "pause", "route": "direct"},
        headers=CSRF,
    )
    assert resp.status_code == 403
