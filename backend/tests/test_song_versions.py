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
