"""Album covers behind the pages: light ones in light mode, dark ones in dark mode, or one
colour family's."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender.db import get_sessionmaker
from plekstender.db.models import Album, AlbumColour, User, UserServer
from tests.fakes import CSRF, FakePlex
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_backgrounds(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        us = await db.scalar(select(UserServer).where(UserServer.user_id == user_id))
        assert us is not None
        thumbs = {a.rating_key: a.thumb for a in await db.scalars(select(Album))}
        # (album, brightness, hue, saturation, lightness)
        for key, brightness, hue, sat, light in (
            (200, 0.8, 50, 0.1, 0.9),  # a light, near-white cover
            (201, 0.1, 0, 0.0, 0.05),  # a dark one
            (202, 0.4, 220, 0.8, 0.45),  # blue, in between
        ):
            db.add(
                AlbumColour(
                    server_id=us.server_id, rating_key=key, thumb=thumbs[key], hex="#123456",
                    hue=hue, saturation=sat, lightness=light, brightness=brightness,
                    checked_at=dt.datetime.now(dt.UTC),
                )
            )  # fmt: skip
        # A colour worked out for an older cover doesn't count.
        db.add(
            AlbumColour(
                server_id=us.server_id, rating_key=203, thumb="/old", hex="#000000", hue=0,
                saturation=0, lightness=0.02, brightness=0.02, checked_at=dt.datetime.now(dt.UTC),
            )
        )  # fmt: skip
        await db.commit()
    base = f"/api/servers/{sid}/library/stats/backgrounds"

    async def keys(**params: str) -> list[int]:
        resp = await owner.get(base, params=params)
        assert resp.status_code == 200, resp.text
        return sorted(c["rating_key"] for c in resp.json())

    assert await keys(mode="covers", theme="light") == [200]
    assert await keys(mode="covers", theme="dark") == [201]
    assert await keys(mode="colour", family="blue") == [202]
    assert await keys(mode="colour", family="white") == [200]
    assert await keys(mode="colour", family="green") == []

    # The preference, saved with the others.
    prefs = (await owner.get("/api/auth/me")).json()["preferences"]
    assert prefs["background"] == {"mode": "off", "family": "blue", "speed": "normal"}
    prefs["background"] = {"mode": "colour", "family": "red", "speed": "slow"}
    saved = await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    assert saved.json()["preferences"]["background"]["family"] == "red"
