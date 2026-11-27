"""Lidarr: connecting it, and adding albums and artists to it (against a small fake Lidarr)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI

from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import owner_with_library

LIDARR = "http://lidarr.test:8686"
API_KEY = "0123456789abcdef0123456789abcdef"
OK_COMPUTER = "b1392450-e666-3926-a536-22c65f834433"  # release group
NEW_ALBUM = "11111111-2222-4333-8444-555555555555"
RADIOHEAD = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


METADATA = {
    OK_COMPUTER: {
        "foreignAlbumId": OK_COMPUTER, "title": "OK Computer", "releaseDate": "1997-05-21",
        "artist": {"foreignArtistId": RADIOHEAD, "artistName": "Radiohead"},
    },
    NEW_ALBUM: {
        "foreignAlbumId": NEW_ALBUM, "title": "Coming Soon", "releaseDate": "2026-12-12",
        "artist": {"foreignArtistId": RADIOHEAD, "artistName": "Radiohead"},
    },
}  # fmt: skip
RELEASES = [
    {
        "guid": "small", "indexerId": 3, "indexer": "Indexer",
        "title": "Radiohead - OK Computer (MP3)",
        "size": 100_000_000, "quality": {"quality": {"name": "MP3-320"}}, "protocol": "torrent",
        "seeders": 50, "leechers": 2, "age": 400, "approved": False,
        "rejections": ["Not wanted in profile"],
    },
    {
        "guid": "flac", "indexerId": 3, "indexer": "Indexer",
        "title": "Radiohead - OK Computer (FLAC)",
        "size": 450_000_000, "quality": {"quality": {"name": "FLAC"}}, "protocol": "torrent",
        "seeders": 12, "leechers": 1, "age": 30, "approved": True, "rejections": [],
    },
]  # fmt: skip


class FakeLidarr:
    """Enough of Lidarr's v1 API: set-up, albums, artists, releases and commands."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.albums: dict[int, dict[str, Any]] = {}
        self.artists: dict[int, dict[str, Any]] = {}
        self.commands: list[dict[str, Any]] = []
        self.grabbed: list[dict[str, Any]] = []
        self.added: list[dict[str, Any]] = []
        router.route(host="lidarr.test").mock(side_effect=self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Api-Key") != API_KEY:
            return httpx.Response(401)
        path = request.url.path.removeprefix("/api/v1")
        params = dict(request.url.params)
        body = json.loads(request.content) if request.content else None
        method = request.method
        if path == "/system/status":
            return httpx.Response(200, json={"appName": "Lidarr", "version": "2.9.6"})
        if path == "/rootfolder":
            return httpx.Response(
                200, json=[{"id": 1, "path": "/music", "freeSpace": 4_000_000_000_000}]
            )
        if path == "/qualityprofile":
            return httpx.Response(
                200, json=[{"id": 1, "name": "Any"}, {"id": 2, "name": "Lossless"}]
            )
        if path == "/metadataprofile":
            return httpx.Response(200, json=[{"id": 1, "name": "Standard"}])
        if path == "/album" and method == "GET":
            mbid = params.get("foreignAlbumId")
            return httpx.Response(
                200, json=[a for a in self.albums.values() if a["foreignAlbumId"] == mbid]
            )
        if path == "/album/lookup":
            mbid = params["term"].removeprefix("lidarr:")
            return httpx.Response(200, json=[METADATA[mbid]] if mbid in METADATA else [])
        if path == "/album" and method == "POST":
            self.added.append(body)
            album_id = len(self.albums) + 1
            stats = {"trackFileCount": 0, "totalTrackCount": 12, "percentOfTracks": 0}
            self.albums[album_id] = {**body, "id": album_id, "statistics": stats}
            return httpx.Response(201, json=self.albums[album_id])
        if path == "/album/monitor":
            for album_id in body["albumIds"]:
                self.albums[album_id]["monitored"] = body["monitored"]
            return httpx.Response(202)
        if path == "/command":
            self.commands.append(body)
            return httpx.Response(201, json={"id": len(self.commands), **body})
        if path == "/release" and method == "GET":
            assert int(params["albumId"]) in self.albums
            return httpx.Response(200, json=RELEASES)
        if path == "/release" and method == "POST":
            self.grabbed.append(body)
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": f"no fake for {method} {path} {params} {body}"})


@pytest.fixture
def fake_lidarr(plex: FakePlex) -> FakeLidarr:
    return FakeLidarr(plex.router)


SETUP = {
    "url": f"{LIDARR}/",
    "api_key": API_KEY,
    "root_folder": "/music",
    "quality_profile_id": 2,
    "metadata_profile_id": 1,
    "access": "admins",
}


@pytest.mark.db
async def test_connecting_lidarr(app: FastAPI, plex: FakePlex, fake_lidarr: FakeLidarr) -> None:
    owner, _ = await owner_with_library(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")
    assert (await owner.get("/api/admin/lidarr")).json() == {
        "configured": False, "url": None, "root_folder": None, "quality_profile_id": None,
        "metadata_profile_id": None, "access": "admins",
    }  # fmt: skip
    assert (await owner.get("/api/lidarr")).json() == {"available": False}

    # Checked first: what it offers to choose from; a wrong key or address is said so.
    options = await owner.post(
        "/api/admin/lidarr/check", json={"url": LIDARR, "api_key": API_KEY}, headers=CSRF
    )
    assert options.status_code == 200, options.text
    assert options.json() == {
        "version": "2.9.6",
        "root_folders": [{"path": "/music", "free_space": 4_000_000_000_000}],
        "quality_profiles": [{"id": 1, "name": "Any"}, {"id": 2, "name": "Lossless"}],
        "metadata_profiles": [{"id": 1, "name": "Standard"}],
    }
    wrong = await owner.post(
        "/api/admin/lidarr/check", json={"url": LIDARR, "api_key": "nope"}, headers=CSRF
    )
    assert wrong.status_code == 502 and "API key" in wrong.json()["detail"]
    bad = await owner.post("/api/admin/lidarr/check", json={"url": "lidarr.test"}, headers=CSRF)
    assert bad.status_code in (422, 502)

    # Saved: only with a folder and profiles it has; the key isn't shown again.
    nope = await owner.put("/api/admin/lidarr", json={**SETUP, "root_folder": "/x"}, headers=CSRF)
    assert nope.status_code == 422
    assert (await friend.put("/api/admin/lidarr", json=SETUP, headers=CSRF)).status_code == 403
    saved = (await owner.put("/api/admin/lidarr", json=SETUP, headers=CSRF)).json()
    assert saved == {
        "configured": True, "url": LIDARR, "root_folder": "/music", "quality_profile_id": 2,
        "metadata_profile_id": 1, "access": "admins",
    }  # fmt: skip
    assert API_KEY not in json.dumps(saved)

    # Admins only, until everyone may use it (changed without giving the key again).
    assert (await owner.get("/api/lidarr")).json() == {"available": True}
    assert (await friend.get("/api/lidarr")).json() == {"available": False}
    everyone = {k: v for k, v in SETUP.items() if k != "api_key"} | {"access": "everyone"}
    assert (await owner.put("/api/admin/lidarr", json=everyone, headers=CSRF)).status_code == 200
    assert (await friend.get("/api/lidarr")).json() == {"available": True}

    assert (await owner.delete("/api/admin/lidarr", headers=CSRF)).status_code == 204
    assert (await friend.get("/api/lidarr")).json() == {"available": False}


async def connected(app: FastAPI, plex: FakePlex, access: str = "admins") -> tuple[Any, Any]:
    owner, _ = await owner_with_library(app, plex)
    resp = await owner.put("/api/admin/lidarr", json={**SETUP, "access": access}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    friend, _ = await sign_in(app, plex, "friend-token")
    return owner, friend


@pytest.mark.db
async def test_downloading_an_album(app: FastAPI, plex: FakePlex, fake_lidarr: FakeLidarr) -> None:
    owner, friend = await connected(app, plex)
    states = await owner.post(
        "/api/lidarr/albums/states", json={"mbids": [OK_COMPUTER, NEW_ALBUM]}, headers=CSRF
    )
    assert states.json() == {OK_COMPUTER: "absent", NEW_ALBUM: "absent"}
    # Admins only (as set up): others can't, until everyone may.
    refused = await friend.post("/api/lidarr/albums", json={"mbid": OK_COMPUTER}, headers=CSRF)
    assert refused.status_code == 403

    # Let Lidarr search: added monitored (its artist only for this album) and searched.
    added = await owner.post(
        "/api/lidarr/albums", json={"mbid": OK_COMPUTER, "search": True}, headers=CSRF
    )
    assert added.json() == {"id": 1, "title": "OK Computer", "state": "monitored"}
    [body] = fake_lidarr.added
    assert body["monitored"] is True and body["addOptions"] == {"searchForNewAlbum": True}
    assert body["artist"]["rootFolderPath"] == "/music"
    assert body["artist"]["qualityProfileId"] == 2 and body["artist"]["metadataProfileId"] == 1
    assert body["artist"]["addOptions"] == {"monitor": "none", "searchForMissingAlbums": False}
    assert body["artist"]["monitorNewItems"] == "none"

    # Already there: searched again, not added twice.
    again = await owner.post(
        "/api/lidarr/albums", json={"mbid": OK_COMPUTER, "search": True}, headers=CSRF
    )
    assert again.json()["id"] == 1 and len(fake_lidarr.added) == 1
    assert fake_lidarr.commands == [{"name": "AlbumSearch", "albumIds": [1]}]

    # Choosing by hand: what the indexers have, the ones Lidarr would take first.
    releases = (await owner.get("/api/lidarr/albums/1/releases")).json()
    assert [(r["guid"], r["quality"], r["approved"]) for r in releases] == [
        ("flac", "FLAC", True),
        ("small", "MP3-320", False),
    ]
    assert releases[1]["rejections"] == ["Not wanted in profile"]
    assert releases[0]["age_days"] == 30
    grabbed = await owner.post(
        "/api/lidarr/releases", json={"guid": "flac", "indexer_id": 3}, headers=CSRF
    )
    assert grabbed.status_code == 204
    assert fake_lidarr.grabbed == [{"guid": "flac", "indexerId": 3}]

    # Albums Lidarr's metadata doesn't know.
    unknown = await owner.post(
        "/api/lidarr/albums", json={"mbid": "99999999-9999-4999-8999-999999999999"}, headers=CSRF
    )
    assert unknown.status_code == 404

    # Downloaded, as the Releases page shows it.
    fake_lidarr.albums[1]["statistics"] = {"trackFileCount": 12, "totalTrackCount": 12}
    states = await owner.post(
        "/api/lidarr/albums/states", json={"mbids": [OK_COMPUTER]}, headers=CSRF
    )
    assert states.json() == {OK_COMPUTER: "downloaded"}


@pytest.mark.db
async def test_monitoring_an_album_coming_up(
    app: FastAPI, plex: FakePlex, fake_lidarr: FakeLidarr
) -> None:
    owner, friend = await connected(app, plex, access="everyone")
    # Anyone may (as set up): added monitored, not searched (it isn't out yet).
    added = await friend.post("/api/lidarr/albums", json={"mbid": NEW_ALBUM}, headers=CSRF)
    assert added.json() == {"id": 1, "title": "Coming Soon", "state": "monitored"}
    assert fake_lidarr.added[0]["addOptions"] == {"searchForNewAlbum": False}
    assert fake_lidarr.commands == []

    # In Lidarr but not monitored: monitored now.
    fake_lidarr.albums[1]["monitored"] = False
    states = await owner.post(
        "/api/lidarr/albums/states", json={"mbids": [NEW_ALBUM]}, headers=CSRF
    )
    assert states.json() == {NEW_ALBUM: "unmonitored"}
    again = await owner.post("/api/lidarr/albums", json={"mbid": NEW_ALBUM}, headers=CSRF)
    assert again.json()["state"] == "monitored" and fake_lidarr.albums[1]["monitored"] is True
    assert len(fake_lidarr.added) == 1
