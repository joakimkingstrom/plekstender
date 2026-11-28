"""Song versions: every release of a song by the same artist (remasters, live, demos)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from plekstender.sync.parse import core_title
from tests.fakes import FakePlex
from tests.test_sync import owner_with_library


def test_core_title() -> None:
    same = [
        "Lucky",
        "Lucky (Live)",
        "Lucky - Live at Wembley 2007",
        "Lucky [Demo] (2011 Remaster)",
        "LUCKY \u2013 acoustic",  # an en dash
    ]
    assert {core_title(t) for t in same} == {"lucky"}
    assert core_title("Exit Music (For a Film)") == "exit music"
    assert core_title("(Nice Dream)") == "(nice dream)"  # nothing but brackets: kept


@pytest.mark.db
async def test_song_versions(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    found = (await owner.get(f"/api/servers/{sid}/library/tracks/302/versions")).json()
    assert (found["title"], found["artist"]) == ("Karma Police (Remastered)", "Radiohead")
    assert [
        (v["rating_key"], v["album_title"], v["year"], v["release_type"], v["current"])
        for v in found["versions"]
    ] == [
        (301, "OK Computer", 1997, "Album", False),
        (302, "OK Computer OKNOTOK", 2017, "Compilation", True),
    ]
    only = (await owner.get(f"/api/servers/{sid}/library/tracks/300/versions")).json()
    assert [v["rating_key"] for v in only["versions"]] == [300]
    missing = await owner.get(f"/api/servers/{sid}/library/tracks/999/versions")
    assert missing.status_code == 404


@pytest.mark.db
async def test_live_album_sources(app: FastAPI, plex: FakePlex) -> None:
    """A live album's songs, by the studio release each first came out on."""
    import datetime as dt

    from sqlalchemy import select

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import AlbumColour, User, UserServer
    from tests.fakes import _album, _tags, _track

    lib = plex.library
    for item in (
        _album(206, "I Might Be Wrong", 100, 2001),
        _album(207, "Lucky", 100, 1995),
        _track(3061, "Paranoid Android (Live)", 206, 100, 1, 50),
        _track(3062, "Karma Police - Live in Oslo", 206, 100, 2, 40),
        _track(3063, "Lucky (Live)", 206, 100, 3, 30),
        _track(3064, "Like Spinning Plates (Live)", 206, 100, 4, 20),
        _track(3071, "Lucky", 207, 100, 1, 10),
    ):
        if item["type"] == "track":
            item["grandparentTitle"] = "Radiohead"
        lib.items[int(item["ratingKey"])] = item
    lib.details[206] = {"Format": _tags("Album"), "Subformat": _tags("Live")}
    lib.details[207] = {"Format": _tags("Single")}
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        us = await db.scalar(select(UserServer).where(UserServer.user_id == user_id))
        assert us is not None
        db.add(
            AlbumColour(
                server_id=us.server_id, rating_key=200, thumb="t", hex="#aa3322",
                checked_at=dt.datetime.now(dt.UTC),
            )
        )  # fmt: skip
        await db.commit()

    live = (await owner.get(f"/api/servers/{sid}/library/albums/206")).json()
    assert live["release_type"] == "Live"
    # Lucky is on OKNOTOK too, but that's a compilation: the single is its studio release.
    assert [
        (s["rating_key"], s["title"], s["release_type"], s["colour"], s["songs"])
        for s in live["live_sources"]
    ] == [
        (207, "Lucky", "Single", None, 1),
        (200, "OK Computer", "Album", "#aa3322", 2),
    ]
    assert live["live_unmatched"] == 1  # Like Spinning Plates: no studio version here
    studio = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert (studio["live_sources"], studio["live_unmatched"]) == (None, 0)
