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
