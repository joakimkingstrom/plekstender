"""End-to-end API tests against a real Postgres, with plex.tv and Plex servers mocked."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from tests.fakes import CSRF, FakePlex, client_for, sign_in

pytestmark = pytest.mark.db


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


GAME = {
    "round_seconds": 30,
    "max_guesses": 3,
    "artist_feedback": True,
    "playlist": "Guess the intro",
    "popularity": "popular",
}


@pytest.mark.db
async def test_preferences_follow_the_account(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    me = (await owner.get("/api/auth/me")).json()
    assert me["preferences"] == {
        "accent": None,
        "skip_threshold": 80,
        "phone_start": "rate",
        "game": GAME,
        "theme": None,
        "achievements": True,
        "instance_notifications": True,
        "cover_source": "deezer",
        "best_cover_sources": ["deezer", "itunes", "tidal", "coverartarchive"],
    }
    saved = await owner.put("/api/auth/me/preferences", json={"accent": "#3e63dd"}, headers=CSRF)
    assert saved.status_code == 200
    assert saved.json()["preferences"] == {
        "accent": "#3e63dd",
        "skip_threshold": 80,
        "phone_start": "rate",
        "game": GAME,
        "theme": None,
        "achievements": True,
        "instance_notifications": True,
        "cover_source": "deezer",
        "best_cover_sources": ["deezer", "itunes", "tidal", "coverartarchive"],
    }
    # Another browser: a new session for the same account sees it.
    again, _ = await sign_in(app, plex, "owner-token")
    assert (await again.get("/api/auth/me")).json()["preferences"]["accent"] == "#3e63dd"
    bad = await owner.put("/api/auth/me/preferences", json={"accent": "blue"}, headers=CSRF)
    assert bad.status_code == 422
