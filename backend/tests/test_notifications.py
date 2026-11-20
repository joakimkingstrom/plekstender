"""Notifications: raised from connection statuses, once per problem, cleared when fixed."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender.api.routes import statuses
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import ScrobbleTarget, User
from plekstender.notifications import check
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs

pytestmark = pytest.mark.db


async def run_check() -> int:
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        return await check(db, http, get_settings())


async def test_notifications(app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "searxng_url", "http://searxng:8080")
    monkeypatch.setattr(statuses, "_searxng", None)
    searxng = plex.router.get("http://searxng:8080/healthz").mock(return_value=httpx.Response(500))
    owner, _ = await owner_with_library(app, plex)  # the first user: an admin
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()
    async with get_sessionmaker()() as db:
        owner_id = await db.scalar(select(User.id).where(User.username == "owner"))
        db.add(ScrobbleTarget(
            user_id=owner_id, kind="listenbrainz", service="koito", api_url="https://koito.example",
            token_enc="x", enabled_at=dt.datetime.now(dt.UTC), submitted_through_id=0, sent_count=0,
            paused=False, last_error="Koito answered 502.",
        ))  # fmt: skip
        await db.commit()

    assert await run_check() == 2  # Koito (the owner's) and SearXNG (the instance's)
    mine = (await owner.get("/api/notifications")).json()
    assert mine["unread"] == 2
    by_title = {n["title"]: n for n in mine["items"]}
    koito = by_title["Koito needs a look"]
    assert (koito["detail"], koito["link"], koito["instance"]) == (
        "Koito answered 502.",
        "/settings/scrobbling",
        False,
    )
    assert by_title["SearXNG (image search) isn't working"]["instance"] is True
    # Not an admin, not theirs: the friend sees none of these.
    assert (await friend.get("/api/notifications")).json() == {"items": [], "unread": 0}

    # Read; the same problem again doesn't pile up or come back unread...
    await owner.post("/api/notifications/read", json={}, headers=CSRF)
    assert await run_check() == 2
    again = (await owner.get("/api/notifications")).json()
    assert (len(again["items"]), again["unread"]) == (2, 0)
    # ...but getting worse does.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ScrobbleTarget).values(paused=True, last_error="Koito rejected the token.")
        )
        await db.commit()
    await run_check()
    worse = (await owner.get("/api/notifications")).json()
    assert worse["unread"] == 1
    assert {n["title"] for n in worse["items"]} >= {"Koito isn't working"}

    # Dismissed for one admin; hidden for every admin.
    koito_id = next(n["id"] for n in worse["items"] if n["title"].startswith("Koito"))
    searx_id = next(n["id"] for n in worse["items"] if n["title"].startswith("SearXNG"))
    assert (
        await owner.post(f"/api/notifications/{koito_id}/dismiss", headers=CSRF)
    ).status_code == 204
    assert (
        await friend.post(f"/api/notifications/{searx_id}/hide", headers=CSRF)
    ).status_code == 404
    assert (
        await owner.post(f"/api/notifications/{searx_id}/hide", headers=CSRF)
    ).status_code == 204
    assert (await owner.get("/api/notifications")).json()["items"] == []

    # Fixed: resolved by itself; it comes back (unread) if it happens again.
    searxng.mock(return_value=httpx.Response(200, text="OK"))
    monkeypatch.setattr(statuses, "_searxng", None)
    async with get_sessionmaker()() as db:
        await db.execute(update(ScrobbleTarget).values(paused=False, last_error=None))
        await db.commit()
    assert await run_check() == 0
    searxng.mock(return_value=httpx.Response(500))
    monkeypatch.setattr(statuses, "_searxng", None)
    assert await run_check() == 1
    back = (await owner.get("/api/notifications")).json()
    # SearXNG is back (hidden before, but this is a new occurrence); Koito stays dismissed.
    assert [(n["title"], n["read"], n["resolved_at"]) for n in back["items"]] == [
        ("SearXNG (image search) isn't working", False, None)
    ]
    assert back["unread"] == 1
