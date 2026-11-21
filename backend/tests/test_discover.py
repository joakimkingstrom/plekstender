"""Library → Discover: artists Plex calls similar to many of the library's, not in it yet."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender.db import get_sessionmaker
from plekstender.db.models import PlexServer, SimilarArtist, User
from tests.fakes import CSRF, FakePlex
from tests.test_stats import add_listens
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_artists_to_add(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    url = f"/api/servers/{sid}/library/discover"
    # Plex: Radiohead is like Portishead (in the library) and Muse; Portishead like Massive Attack.
    first = (await owner.get(url)).json()
    assert [(s["name"], s["similar_to"]) for s in first["items"]] == [
        ("Massive Attack", 1),
        ("Muse", 1),
    ]
    async with get_sessionmaker()() as db:
        server_id = await db.scalar(select(PlexServer.id))
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        assert server_id is not None and user_id is not None
        db.add(SimilarArtist(server_id=server_id, artist_rating_key=101, position=5, name="muse"))
        await db.commit()
    await add_listens(user_id, [(1, "Massive Attack", "Mezzanine", "Teardrop")] * 3)

    found = (await owner.get(url)).json()
    # Muse is similar to two library artists now (however Plex spells it); listens next.
    assert [(s["name"], s["similar_to"], s["listens"]) for s in found["items"]] == [
        ("Muse", 2, 0),
        ("Massive Attack", 1, 3),
    ]
    assert [x["name"] for x in found["items"][0]["sources"]] == ["Portishead", "Radiohead"]
    assert (found["total"], found["hidden"]) == (2, 0)

    # Hidden: gone from the list, listed with the hidden ones, and back when unhidden.
    muse = found["items"][0]
    hide = await owner.post(
        f"{url}/hidden", json={"key": muse["key"], "name": muse["name"]}, headers=CSRF
    )
    assert hide.status_code == 204
    after = (await owner.get(url)).json()
    assert [s["name"] for s in after["items"]] == ["Massive Attack"] and after["hidden"] == 1
    hidden = (await owner.get(url, params={"hidden": True})).json()
    assert [s["name"] for s in hidden["items"]] == ["Muse"]
    assert (await owner.delete(f"{url}/hidden/{muse['key']}", headers=CSRF)).status_code == 204
    assert (await owner.get(url)).json()["total"] == 2
