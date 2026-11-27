"""Cover songs from SecondHandSongs: looked up gently by the worker when an admin turns it
on, and shown on song rows."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender import cover_songs
from plekstender.db import get_sessionmaker
from plekstender.db.models import SongCover
from tests.fakes import CSRF, FakePlex
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db

API = "https://api.secondhandsongs.com"
KEY = "ed0a8c61-4520-4317-9b95-7a260260b4c0"


def performance(n: int, title: str, artist: str, original: bool) -> dict[str, Any]:
    return {
        "entityType": "performance",
        "uri": f"{API}/performance/{n}",
        "title": title,
        "performer": {"uri": f"{API}/artist/{n}", "name": artist},
        "isOriginal": original,
    }


# What SecondHandSongs knows: search results by title, and covers' pages.
SEARCH = {
    "Karma Police": [performance(1, "Karma Police", "Radiohead", True)],
    "Glory Box": [
        performance(9, "Glory Box", "Some Tribute Band", False),
        performance(2, "Glory Box", "Portishead", False),
    ],
    "Lucky": [performance(3, "Lucky", "Radiohead", False)],  # a re-recording of their own
}
DETAILS = {
    2: performance(20, "Ike's Rap II", "Isaac Hayes", True),
    3: performance(30, "Lucky", "Radiohead", True),
}


async def test_cover_songs(app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cover_songs, "WITHOUT_KEY", cover_songs.Limits(per_minute=60_000, per_hour=90, per_day=900)
    )
    monkeypatch.setattr(
        cover_songs, "WITH_KEY", cover_songs.Limits(per_minute=60_000, per_hour=900, per_day=9000)
    )
    asked: list[httpx.Request] = []
    busy = False

    def shs(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if request.headers.get("X-API-Key") not in (None, KEY):
            return httpx.Response(403, json={"error": "bad key"})
        if busy:
            return httpx.Response(429, json={"error": "slow down"})
        if request.url.path == "/search/performance":
            title = request.url.params["title"]
            assert request.url.params["performer"]  # always by performer too
            found = SEARCH.get(title, [])
            return httpx.Response(
                200, json={"totalResults": len(found), "resultPage": found, "skippedResults": 0}
            )
        n = int(request.url.path.rsplit("/", 1)[-1])
        if n not in DETAILS:
            return httpx.Response(404)
        original = DETAILS[n]
        work = {"entityType": "work", "uri": f"{API}/work/{n}", "title": original["title"]}
        return httpx.Response(
            200,
            json={"uri": f"{API}/performance/{n}", "originals": [{**work, "original": original}]},
        )

    plex.router.route(host="api.secondhandsongs.com").mock(side_effect=shs)
    owner, sid = await owner_with_library(app, plex)

    async def run() -> int:
        async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
            from plekstender.config import get_settings

            return await cover_songs.run(db, http, get_settings())

    # Off (the default): nothing asked.
    assert await run() == 0
    assert asked == []
    status = (await owner.get("/api/admin/secondhandsongs")).json()
    assert status == {
        "enabled": False,
        "api_key": False,
        "per_hour": 90,
        "songs": 5,
        "checked": 0,
        "covers": 0,
    }

    resp = await owner.put("/api/admin/secondhandsongs", json={"enabled": True}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    assert await run() == 5  # Karma Police once, for both its releases
    async with get_sessionmaker()() as db:
        found = {(c.artist_key, c.title_norm): c for c in await db.scalars(select(SongCover))}
    assert {k: (c.status, c.requests) for k, c in found.items()} == {
        ("radiohead", "karma police"): ("original", 1),
        ("radiohead", "paranoid android"): ("unknown", 1),
        ("radiohead", "lucky"): ("original", 2),  # their own song, recorded again
        ("portishead", "glory box"): ("cover", 2),  # not the tribute band's version
        ("secret band", "secret song"): ("unknown", 1),
    }
    glory = found[("portishead", "glory box")]
    assert (glory.original_artist, glory.original_title) == ("Isaac Hayes", "Ike's Rap II")
    assert glory.performance_url == "https://secondhandsongs.com/performance/2"
    assert glory.original_url == "https://secondhandsongs.com/performance/20"

    # On the song's row.
    album = (await owner.get(f"/api/servers/{sid}/library/albums/202")).json()
    assert album["tracks"][0]["cover_of"] == {
        "artist": "Isaac Hayes",
        "title": "Ike's Rap II",
        "url": "https://secondhandsongs.com/performance/20",
        "cover_url": "https://secondhandsongs.com/performance/2",
    }
    ok_computer = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert {t["cover_of"] for t in ok_computer["tracks"]} == {None}
    status = (await owner.get("/api/admin/secondhandsongs")).json()
    assert (status["songs"], status["checked"], status["covers"]) == (5, 5, 1)

    # Nothing due: nothing asked.
    asked.clear()
    assert await run() == 0
    assert asked == []

    # Not on SecondHandSongs half a year ago: looked up again, within the hour's budget.
    async def due_again() -> None:
        async with get_sessionmaker()() as db:
            await db.execute(
                update(SongCover)
                .where(SongCover.title_norm == "paranoid android")
                .values(checked_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=200))
            )
            await db.commit()

    await due_again()
    monkeypatch.setattr(
        cover_songs, "WITHOUT_KEY", cover_songs.Limits(per_minute=60_000, per_hour=6, per_day=900)
    )
    assert await run() == 0  # 6 requests made in the last hour already
    monkeypatch.setattr(
        cover_songs, "WITHOUT_KEY", cover_songs.Limits(per_minute=60_000, per_hour=90, per_day=900)
    )

    # Asked to slow down: the run stops, and the song stays due.
    busy = True
    assert await run() == 0
    async with get_sessionmaker()() as db:
        again = await db.get(SongCover, ("radiohead", "paranoid android"))
        assert again is not None and again.checked_at < dt.datetime.now(dt.UTC) - dt.timedelta(
            days=100
        )
    busy = False
    assert await run() == 1

    # An API key: checked first, then sent with every request.
    bad = await owner.put(
        "/api/admin/secondhandsongs", json={"enabled": True, "api_key": "x" * 36}, headers=CSRF
    )
    assert bad.status_code == 400
    good = await owner.put(
        "/api/admin/secondhandsongs", json={"enabled": True, "api_key": KEY}, headers=CSRF
    )
    assert good.status_code == 200, good.text
    assert (good.json()["api_key"], good.json()["per_hour"]) == (True, 900)
    await due_again()
    asked.clear()
    assert await run() == 1
    assert [r.headers.get("X-API-Key") for r in asked] == [KEY]
    # Turned off (the key is kept until removed).
    off = await owner.put("/api/admin/secondhandsongs", json={"enabled": False}, headers=CSRF)
    assert (off.json()["enabled"], off.json()["api_key"]) == (False, True)
    removed = await owner.put(
        "/api/admin/secondhandsongs", json={"enabled": False, "remove_key": True}, headers=CSRF
    )
    assert removed.json()["api_key"] is False
