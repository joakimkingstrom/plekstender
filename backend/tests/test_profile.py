"""The profile page, and preferences that live on the account."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI

from plekstender.db import get_sessionmaker
from plekstender.db.models import Notification
from tests.fakes import CSRF, FakePlex
from tests.test_stats import add_listens
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_profile_and_preferences(app: FastAPI, plex: FakePlex) -> None:
    owner, _sid = await owner_with_library(app, plex)
    me = (await owner.get("/api/auth/me")).json()
    await add_listens(me["id"], [(0, "Radiohead", "OK Computer", "Lucky")] * 3)

    profile = (await owner.get("/api/auth/me/profile")).json()
    assert (profile["username"], profile["is_admin"]) == ("owner", True)
    assert profile["servers"] == [{"name": "Home", "is_owner": True}]
    # Radiohead and Paranoid Android were rated in Plex.
    assert (profile["scrobbles"], profile["ratings"], profile["gigs"]) == (3, 2, 0)
    assert profile["first_listen_at"] is not None and profile["joined_at"]

    # The theme and the other new preferences are saved on the account.
    prefs = {**me["preferences"], "theme": "light", "achievements": False}
    saved = (await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)).json()
    assert (saved["preferences"]["theme"], saved["preferences"]["achievements"]) == ("light", False)
    assert (await owner.get("/api/auth/me")).json()["preferences"]["theme"] == "light"

    # Admins can turn off the instance's notifications.
    now = dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as db:
        db.add(
            Notification(
                key="instance:Test",
                user_id=None,
                level="warning",
                title="Test needs a look",
                detail=None,
                link=None,
                first_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    assert len((await owner.get("/api/notifications")).json()["items"]) == 1
    prefs["instance_notifications"] = False
    await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    assert (await owner.get("/api/notifications")).json()["items"] == []
