"""Achievements: unlocked from your numbers, once; celebrated once; can be turned off."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from plekstender.db import get_sessionmaker
from plekstender.db.models import GameRound
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs

pytestmark = pytest.mark.db


async def test_achievements(app: FastAPI, plex: FakePlex) -> None:
    owner, _sid = await owner_with_library(app, plex)
    me = (await owner.get("/api/auth/me")).json()

    found = (await owner.get("/api/achievements")).json()
    assert found["enabled"] is True
    by_id = {a["id"]: a for a in found["items"]}
    # The owner rated Radiohead and Paranoid Android in Plex: a first rating.
    assert by_id["one-hit-wonder"]["unlocked_at"] is not None
    assert by_id["one-hit-wonder"]["seen"] is False
    # Not yet: 3 of 100 artists, shown as progress.
    roadie = by_id["roadie"]
    assert (roadie["unlocked_at"], roadie["progress"], roadie["goal"]) == (None, 3, 100)
    assert roadie["hint"] == "100 artists in your library"
    # One of six songs rated: 16 %, so Air Guitar (10 %) too.
    assert by_id["air-guitar"]["unlocked_at"] and not by_id["stage-diver"]["unlocked_at"]

    # Ten intros right in a row.
    async with get_sessionmaker()() as db:
        db.add_all(
            [GameRound(user_id=me["id"], mode="song", correct=True, points=10, streak=i)
             for i in range(1, 11)]
        )  # fmt: skip
        await db.commit()
    new = (await owner.get("/api/achievements", params={"new": True})).json()["items"]
    assert {a["id"] for a in new} == {"one-hit-wonder", "air-guitar", "sound-check", "brown-mms"}
    await owner.post("/api/achievements/seen", json={"ids": [a["id"] for a in new]}, headers=CSRF)
    assert (await owner.get("/api/achievements", params={"new": True})).json()["items"] == []

    # Off for you; off for everyone (admins).
    prefs = {**me["preferences"], "achievements": False}
    await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    assert (await owner.get("/api/achievements")).json() == {"enabled": False, "items": []}
    prefs["achievements"] = True
    await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()
    assert (
        await friend.put("/api/admin/achievements", json={"enabled": False}, headers=CSRF)
    ).status_code == 403
    assert (
        await owner.put("/api/admin/achievements", json={"enabled": False}, headers=CSRF)
    ).json() == {"enabled": False}
    assert (await friend.get("/api/achievements")).json()["enabled"] is False
    assert (await owner.get("/api/admin/achievements")).json() == {"enabled": False}
