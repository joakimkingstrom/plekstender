"""HSTS over https, and claiming a new instance with the setup code from the log."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from plekstender.api.middleware import SecurityHeadersMiddleware
from plekstender.auth import setup
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from tests.fakes import CSRF, SETUP_CODE, FakePlex, client_for


async def _ok(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse("ok")(scope, receive, send)


@pytest.mark.parametrize(("hsts", "expected"), [(True, "max-age=31536000"), (False, None)])
async def test_hsts_only_over_https(hsts: bool, expected: str | None) -> None:
    app = SecurityHeadersMiddleware(_ok, hsts=hsts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/")
    assert resp.headers.get("strict-transport-security") == expected
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.db
async def test_claiming_needs_the_setup_code_from_the_log(
    clean_db: None, plex: FakePlex, caplog: pytest.LogCaptureFixture
) -> None:
    """Whoever signs in first becomes the admin, so they need the code only the log shows."""
    from plekstender.api.app import create_app

    application: FastAPI = create_app(get_settings())
    with caplog.at_level(logging.WARNING, logger="plekstender.auth.setup"):
        async with application.router.lifespan_context(application):
            logged = "\n".join(r.getMessage() for r in caplog.records)
            assert SETUP_CODE in logged and "hasn't been set up yet" in logged
            client = client_for(application)
            assert (await client.get("/api/auth/setup")).json() == {"claimed": False}

            # No code, or the wrong one: Plex sign-in doesn't even start.
            for body in (None, {"setup_code": "WRNG-CODE-0000"}):
                refused = await client.post("/api/auth/plex/pin", json=body, headers=CSRF)
                assert refused.status_code == 403
                assert "setup code" in refused.json()["detail"]

            # Right (spacing and case don't matter): signs in as the admin.
            started = await client.post(
                "/api/auth/plex/pin", json={"setup_code": " test code 2468 "}, headers=CSRF
            )
            assert started.status_code == 200
            plex.approved[plex.next_pin] = "owner-token"
            done = (await client.post("/api/auth/plex/check", headers=CSRF)).json()
            assert (done["status"], done["user"]["is_admin"]) == ("ok", True)
            assert (await client.get("/api/auth/setup")).json() == {"claimed": True}
            async with get_sessionmaker()() as db:
                assert await setup.current_hash(db) is None  # done with

            # Claimed: everyone else signs in without a code.
            friend = client_for(application)
            assert (await friend.post("/api/auth/plex/pin", headers=CSRF)).status_code == 200


@pytest.mark.db
async def test_a_new_setup_code_replaces_the_old_one(
    clean_db: None, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart before anyone has signed in makes a new code; a sign-in started with the
    old one can't claim the instance."""
    from plekstender.api.app import create_app

    application: FastAPI = create_app(get_settings())
    async with application.router.lifespan_context(application):
        client = client_for(application)
        started = await client.post(
            "/api/auth/plex/pin", json={"setup_code": SETUP_CODE}, headers=CSRF
        )
        assert started.status_code == 200
        monkeypatch.setattr(setup, "new_code", lambda: "NEWC-ODE0-1357")
        async with get_sessionmaker()() as db:
            await setup.issue(db, "http://testserver")  # as a restart would
        plex.approved[plex.next_pin] = "owner-token"
        done = (await client.post("/api/auth/plex/check", headers=CSRF)).json()
        assert done["status"] == "denied" and "setup code" in done["detail"]
        assert (await client.get("/api/auth/setup")).json() == {"claimed": False}


@pytest.mark.db
async def test_a_code_is_made_if_start_up_couldnt(clean_db: None, app: FastAPI) -> None:
    """No code yet (the database wasn't ready at start-up): sign-in makes and logs one."""
    async with get_sessionmaker()() as db:
        await setup.clear(db)
        await db.commit()
    client = client_for(app)
    first = await client.post("/api/auth/plex/pin", json={"setup_code": "x"}, headers=CSRF)
    assert first.status_code == 409 and "written to the web app's log" in first.json()["detail"]
    again = await client.post("/api/auth/plex/pin", json={"setup_code": SETUP_CODE}, headers=CSRF)
    assert again.status_code == 200
