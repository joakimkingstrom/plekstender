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


@pytest.fixture(autouse=True)
def fresh_caches() -> None:
    """plex.tv's device lists and the addresses that answered are kept a while in-process."""
    from plekstender.api.routes import players as routes
    from plekstender.plex import players as plex_players

    routes._resources.clear()
    routes._clients.clear()
    plex_players._answered.clear()


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
        f"/api/servers/{sid}/players/gone/command",
        json={"command": "next", "route": "server"},
        headers=CSRF,
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


TIMELINE = b"""<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer commandID="5" location="fullScreenMusic">
  <Timeline type="video" state="stopped"/>
  <Timeline type="music" state="playing" time="61000" duration="264000" ratingKey="301"
    key="/library/metadata/301" playQueueID="88" playQueueItemID="902" shuffle="1"
    repeat="2" controllable="playPause,stop,skipPrevious,skipNext,seekTo,shuffle,repeat"
    machineIdentifier="home-machine">
    <Track title="Karma Police" grandparentTitle="Radiohead" parentTitle="OK Computer"
      parentThumb="/library/metadata/200/thumb/1" duration="264000" ratingKey="301"/>
  </Timeline>
</MediaContainer>"""


@pytest.fixture
def plexamp_only(plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Plexamp on the account that the server isn't in touch with (as with Plexamp 4.50 on
    another network than the server): only straight to it works."""
    monkeypatch.setitem(
        RESOURCES, "owner-token", [home_server(True, "srv-owner"), plexamp_resource()]
    )
    sent: list[httpx.Request] = []

    def direct(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/player/timeline/poll":
            return httpx.Response(200, content=TIMELINE, headers={"content-type": "text/xml"})
        return httpx.Response(200)

    def queue(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"MediaContainer": {
            "playQueueID": 88, "playQueueSelectedItemID": 902,
            "Metadata": [
                {"playQueueItemID": 901, "ratingKey": "300", "key": "/library/metadata/300",
                 "title": "Paranoid Android", "grandparentTitle": "Radiohead",
                 "parentTitle": "OK Computer", "duration": 383000},
                {"playQueueItemID": 902, "ratingKey": "301", "key": "/library/metadata/301",
                 "title": "Karma Police", "grandparentTitle": "Radiohead",
                 "parentTitle": "OK Computer", "duration": 264000},
            ],
        }})  # fmt: skip

    plex.router.get(f"{SERVER_URI}/clients").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"size": 0}})
    )
    plex.router.get(f"{SERVER_URI}/playQueues/88").mock(side_effect=queue)
    plex.router.get(url__startswith=f"{PLEXAMP_URI}/player/").mock(side_effect=direct)
    return sent


@pytest.mark.db
async def test_the_player_widget_straight_to_plexamp(
    app: FastAPI, plex: FakePlex, plexamp_only: list[httpx.Request]
) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/players/{PLEXAMP}"

    # What it's playing, as Plexamp says itself.
    state = (await owner.get(f"{base}/state")).json()
    assert state | {"detail": None} == {
        "reachable": True, "detail": None, "state": "playing", "position_ms": 61000,
        "duration_ms": 264000, "rating_key": 301, "queue_id": 88, "queue_item_id": 902,
        "shuffle": True, "repeat": 2,
        "controllable": ["playPause", "stop", "skipPrevious", "skipNext", "seekTo", "shuffle",
                         "repeat"],
        "title": "Karma Police", "artist": "Radiohead", "album": "OK Computer",
        "thumb": "/library/metadata/200/thumb/1",
    }  # fmt: skip

    queue = (await owner.get(f"{base}/queue", params={"queue_id": 88})).json()
    assert queue["current_item_id"] == 902
    assert [(i["item_id"], i["title"]) for i in queue["items"]] == [
        (901, "Paranoid Android"), (902, "Karma Police"),
    ]  # fmt: skip

    # The server isn't in touch with it: "auto" goes straight to it.
    for body in (
        {"command": "seek", "value": 120_000},
        {"command": "shuffle", "value": 0},
        {"command": "repeat", "value": 1},
        {"command": "skip_to", "value": 901, "key": "/library/metadata/300"},
    ):
        resp = await owner.post(f"{base}/command", json=body, headers=CSRF)
        assert resp.json()["ok"] is True and resp.json()["route"] == "direct", resp.text
    seek, shuffle, repeat, skip = plexamp_only[-4:]
    assert (seek.url.path, seek.url.params["offset"]) == ("/player/playback/seekTo", "120000")
    assert shuffle.url.params["shuffle"] == "0" and repeat.url.params["repeat"] == "1"
    assert (skip.url.params["key"], skip.url.params["playQueueItemID"]) == (
        "/library/metadata/300", "901",
    )  # fmt: skip
    bad = await owner.post(f"{base}/command", json={"command": "seek"}, headers=CSRF)
    assert bad.status_code == 422

    # Someone else: only once an admin lets everyone control their players.
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = await connect(friend)
    monkeypatch_resources = RESOURCES["owner-token"]
    RESOURCES["friend-token"] = monkeypatch_resources  # the same Plexamp, on their account
    try:
        url = f"/api/servers/{friend_sid}/players/{PLEXAMP}/state"
        assert (await friend.get(url)).status_code == 403
        allowed = await owner.put("/api/admin/players", json={"access": "everyone"}, headers=CSRF)
        assert allowed.json() == {"access": "everyone"}
        assert (await friend.get(url)).json()["state"] == "playing"
    finally:
        RESOURCES["friend-token"] = [home_server(False, "srv-friend")]


@pytest.mark.db
async def test_no_answer_from_a_player_says_to_check_the_firewall(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        RESOURCES, "owner-token", [home_server(True, "srv-owner"), plexamp_resource()]
    )
    plex.router.get(f"{SERVER_URI}/clients").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"size": 0}})
    )
    plex.router.get(url__startswith=f"{PLEXAMP_URI}/player/").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    owner, sid = await owner_with_library(app, plex)
    resp = await owner.post(
        f"/api/servers/{sid}/players/{PLEXAMP}/command", json={"command": "pause"}, headers=CSRF
    )
    detail = resp.json()["detail"]
    assert resp.json()["ok"] is False
    assert "No answer from Plexamp (Mac)" in detail and "firewall" in detail and "32500" in detail
    state = (await owner.get(f"/api/servers/{sid}/players/{PLEXAMP}/state")).json()
    assert state["reachable"] is False and "firewall" in state["detail"]
