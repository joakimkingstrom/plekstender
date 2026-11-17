"""End-to-end API tests against a real Postgres, with plex.tv and Plex servers mocked."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI

from roadiefm.api.app import create_app
from roadiefm.config import get_settings

pytestmark = pytest.mark.db

PLEX_TV = "https://plex.tv"
CLIENTS = "https://clients.plex.tv"
SERVER_URI = "https://10-0-0-2.abc.plex.direct:32400"
CSRF = {"X-RoadieFM": "1"}

ACCOUNTS = {
    "owner-token": {"id": 1, "uuid": "u1", "username": "owner", "email": "owner@example.com"},
    "friend-token": {"id": 2, "uuid": "u2", "username": "friend", "email": "friend@example.com"},
    "stranger-token": {"id": 3, "uuid": "u3", "username": "stranger", "email": None},
}


def home_server(owned: bool, token: str) -> dict[str, Any]:
    return {
        "name": "Home",
        "clientIdentifier": "home-machine",
        "provides": "server",
        "owned": owned,
        "accessToken": token,
        "presence": True,
        "productVersion": "1.42.0",
        "connections": [
            {
                "protocol": "https",
                "address": "10.0.0.2",
                "port": 32400,
                "uri": SERVER_URI,
                "local": True,
            }
        ],
    }


RESOURCES = {
    "owner-token": [home_server(True, "srv-owner")],
    "friend-token": [home_server(False, "srv-friend")],
    "stranger-token": [],
}


class FakePlex:
    """Mocks plex.tv and one Plex server. `approve(pin_id, token)` simulates the user
    approving the sign-in on plex.tv."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.next_pin = 100
        self.approved: dict[int, str] = {}
        router.post(f"{PLEX_TV}/api/v2/pins").mock(side_effect=self._create_pin)
        router.get(url__regex=rf"{PLEX_TV}/api/v2/pins/\d+").mock(side_effect=self._check_pin)
        router.get(f"{PLEX_TV}/api/v2/user").mock(side_effect=self._user)
        router.get(f"{CLIENTS}/api/v2/resources").mock(side_effect=self._resources)
        router.get(f"{SERVER_URI}/identity").mock(
            return_value=httpx.Response(
                200, json={"MediaContainer": {"machineIdentifier": "home-machine"}}
            )
        )
        router.get(f"{SERVER_URI}/library/sections").mock(side_effect=self._sections)

    def _create_pin(self, request: httpx.Request) -> httpx.Response:
        self.next_pin += 1
        return httpx.Response(201, json={"id": self.next_pin, "code": f"code{self.next_pin}"})

    def _check_pin(self, request: httpx.Request) -> httpx.Response:
        pin_id = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            200,
            json={"id": pin_id, "code": f"code{pin_id}", "authToken": self.approved.get(pin_id)},
        )

    def _user(self, request: httpx.Request) -> httpx.Response:
        account = ACCOUNTS.get(request.headers.get("X-Plex-Token", ""))
        return httpx.Response(200, json=account) if account else httpx.Response(401)

    def _resources(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESOURCES[request.headers["X-Plex-Token"]])

    def _sections(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Plex-Token") not in {"srv-owner", "srv-friend"}:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Directory": [
                        {"key": "3", "title": "Music", "type": "artist"},
                        {"key": "1", "title": "Movies", "type": "movie"},
                    ]
                }
            },
        )


@pytest.fixture
async def app(clean_db: None) -> AsyncIterator[FastAPI]:
    application = create_app(get_settings())
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def plex() -> AsyncIterator[FakePlex]:
    with respx.mock(assert_all_called=False) as router:
        yield FakePlex(router)


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def sign_in(app: FastAPI, plex: FakePlex, token: str) -> tuple[httpx.AsyncClient, Any]:
    client = client_for(app)
    resp = await client.post("/api/auth/plex/pin", headers=CSRF)
    assert resp.status_code == 200
    assert "app.plex.tv/auth" in resp.json()["auth_url"]

    pending = await client.post("/api/auth/plex/check", headers=CSRF)
    assert pending.json()["status"] == "pending"

    plex.approved[plex.next_pin] = token
    result = await client.post("/api/auth/plex/check", headers=CSRF)
    assert result.status_code == 200
    return client, result.json()


async def test_first_user_becomes_admin_and_connects_server(app: FastAPI, plex: FakePlex) -> None:
    client, result = await sign_in(app, plex, "owner-token")
    assert result["status"] == "ok"
    assert result["user"]["is_admin"] is True
    # Token never leaves the backend.
    assert "owner-token" not in str(result)

    me = await client.get("/api/auth/me")
    assert me.json()["username"] == "owner"

    available = (await client.get("/api/servers/available")).json()
    assert available == [
        {
            "machine_identifier": "home-machine",
            "name": "Home",
            "owned": True,
            "online": True,
            "version": "1.42.0",
            "connected": False,
            "allowed": True,
        }
    ]

    resp = await client.post(
        "/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF
    )
    assert resp.status_code == 200, resp.text
    server = resp.json()["server"]
    assert server["approved"] is True  # admin's own server is auto-approved
    assert server["connection_kind"] == "local"
    assert server["music_section_keys"] == ["3"]  # the only music library is auto-selected
    assert "srv-owner" not in resp.text

    sections = (await client.get(f"/api/servers/{server['id']}/sections")).json()
    assert sections == [{"key": "3", "title": "Music", "selected": True}]

    bad = await client.put(
        f"/api/servers/{server['id']}/sections", json={"keys": ["1"]}, headers=CSRF
    )
    assert bad.status_code == 422  # a movie library isn't a music library


async def test_servers_mode_admits_only_users_of_approved_servers(
    app: FastAPI, plex: FakePlex
) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")

    # Before the owner connects (and so approves) their server, the friend is denied.
    _, denied = await sign_in(app, plex, "friend-token")
    assert denied["status"] == "denied"

    await owner.post("/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF)
    friend, admitted = await sign_in(app, plex, "friend-token")
    assert admitted["status"] == "ok"
    assert admitted["user"]["is_admin"] is False

    _, stranger = await sign_in(app, plex, "stranger-token")
    assert stranger["status"] == "denied"

    # Friend connects the shared server with their own token.
    resp = await friend.post(
        "/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF
    )
    assert resp.status_code == 200
    friend_server_id = resp.json()["server"]["id"]
    assert resp.json()["server"]["is_owner"] is False

    # Users can't see or touch each other's connections.
    owner_servers = (await owner.get("/api/servers")).json()
    assert friend_server_id not in [s["id"] for s in owner_servers]
    assert (await owner.get(f"/api/servers/{friend_server_id}/sections")).status_code == 404
    deleted = await owner.delete(f"/api/servers/{friend_server_id}", headers=CSRF)
    assert deleted.status_code == 404

    # Non-admins can't reach admin endpoints.
    assert (await friend.get("/api/admin/users")).status_code == 403


async def test_admin_access_settings(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    resp = await owner.put(
        "/api/admin/access",
        json={"mode": "allowlist", "allowlist": [" Stranger ", ""]},
        headers=CSRF,
    )
    assert resp.json()["allowlist"] == ["stranger"]

    _, stranger = await sign_in(app, plex, "stranger-token")
    assert stranger["status"] == "ok"
    _, friend = await sign_in(app, plex, "friend-token")
    assert friend["status"] == "denied"

    users = (await owner.get("/api/admin/users")).json()
    assert [u["username"] for u in users] == ["owner", "stranger"]
    own_id = users[0]["id"]
    demote_self = await owner.put(
        f"/api/admin/users/{own_id}", json={"is_admin": False}, headers=CSRF
    )
    assert demote_self.status_code == 409


async def test_csrf_and_logout(app: FastAPI, plex: FakePlex) -> None:
    client, _ = await sign_in(app, plex, "owner-token")

    no_header = await client.post("/api/auth/logout")
    assert no_header.status_code == 403
    cross_origin = await client.post(
        "/api/auth/logout", headers=CSRF | {"Origin": "https://evil.example"}
    )
    assert cross_origin.status_code == 403

    assert (await client.post("/api/auth/logout", headers=CSRF)).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_unauthenticated_and_health(app: FastAPI) -> None:
    client = client_for(app)
    assert (await client.get("/api/servers")).status_code == 401
    assert (await client.post("/api/auth/plex/check", headers=CSRF)).json()["status"] == "expired"

    health = await client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["database"] is True
    assert body["worker"]["healthy"] is False  # no worker in tests
    assert health.headers["cache-control"] == "no-store"
    assert health.headers["x-content-type-options"] == "nosniff"
