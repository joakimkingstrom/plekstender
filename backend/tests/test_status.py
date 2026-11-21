"""The Home page's Status card: every connection, the user's own and the instance's."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender.api.routes import statuses
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.instance import _put, get_jambase_usage
from plekstender.db.models import ScrobbleTarget, User
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs


@pytest.mark.db
async def test_connections_status(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "searxng_url", "http://searxng:8080")
    monkeypatch.setattr(statuses, "_searxng", None)
    plex.router.get("http://searxng:8080/healthz").mock(return_value=httpx.Response(200, text="OK"))
    plex.router.get("https://api.data.jambase.com/v3/quota").mock(
        return_value=httpx.Response(200, json={"quota": 1000, "remainingCalls": 1000})
    )
    owner, _ = await owner_with_library(app, plex)
    assert (
        await owner.put(
            "/api/admin/jambase", json={"api_key": "jbd_test_0123456789abcdef"}, headers=CSRF
        )
    ).status_code == 200
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        usage = await get_jambase_usage(db)
        usage.calls = 765  # 85 % of the 900 cap
        await _put(db, "jambase_usage", usage.model_dump(mode="json"))
        db.add(ScrobbleTarget(
            user_id=user_id, kind="listenbrainz", service="koito", api_url="https://koito.example",
            token_enc="x", enabled_at=dt.datetime.now(dt.UTC), submitted_through_id=0, sent_count=0,
            paused=True, last_error="Koito rejected the token, so scrobbling is paused.",
        ))  # fmt: skip
        await db.commit()

    found = {c["name"]: c for c in (await owner.get("/api/status/connections")).json()}
    assert found["Plex"] == {
        "name": "Plex",
        "group": "yours",
        "state": "ok",
        "subtitle": "Home",
        "detail": None,
        "usage": None,
        "link": "/settings",
    }
    assert (found["Koito"]["state"], found["Koito"]["group"]) == ("error", "yours")
    assert (
        found["JamBase"]["state"] == "warning"
        and found["JamBase"]["detail"] == "765 of 900 requests this month"
        and found["JamBase"]["usage"] == 0.85
    )
    assert (found["Ticketmaster"]["state"], found["Ticketmaster"]["detail"]) == (
        "off",
        "Not set up",
    )
    assert found["SearXNG (image search)"]["state"] == "ok"
    assert found["AcoustID"]["link"] == "/settings/services/files"
    names = list(found)
    assert names.index("Koito") < names.index("JamBase")  # the user's own first

    # Not an admin: whether each is set up, without numbers or links to admin pages.
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()
    theirs = {c["name"]: c for c in (await friend.get("/api/status/connections")).json()}
    assert "Koito" not in theirs
    jambase = theirs["JamBase"]
    assert (jambase["state"], jambase["detail"], jambase["link"], jambase["usage"]) == (
        "warning",
        None,
        None,
        None,
    )

    # SearXNG stops answering (after the minute it's remembered).
    plex.router.get("http://searxng:8080/healthz").mock(side_effect=httpx.ConnectError("down"))
    monkeypatch.setattr(statuses, "_searxng", None)
    down = {c["name"]: c for c in (await owner.get("/api/status/connections")).json()}
    assert (down["SearXNG (image search)"]["state"], down["SearXNG (image search)"]["detail"]) == (
        "error",
        "Not answering",
    )
