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

    live = (await owner.get(f"/api/servers/{sid}/library/albums/206")).json()
    assert live["release_type"] == "Live"
    # Lucky is on OKNOTOK too, but that's a compilation: the single is its studio release.
    assert [
        (s["rating_key"], s["title"], s["release_type"], s["songs"]) for s in live["song_sources"]
    ] == [
        (207, "Lucky", "Single", 1),
        (200, "OK Computer", "Album", 2),
    ]
    assert live["sources_unmatched"] == 1  # Like Spinning Plates: no studio version here
    studio = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert (studio["song_sources"], studio["sources_unmatched"]) == (None, 0)


@pytest.mark.db
async def test_compilation_sources(app: FastAPI, plex: FakePlex) -> None:
    """A compilation's songs, by the release each first came out on: the artist's own for
    their best-of, each song's own artist's for a various artists one."""
    from tests.fakes import _album, _artist, _tags, _track

    lib = plex.library
    for item in (
        _artist(104, "Various Artists"),
        _album(208, "Trip-Hop Classics", 104, 2005),
        _track(3081, "Glory Box", 208, 104, 1, 50),
        _track(3082, "Paranoid Android (Radio Edit)", 208, 104, 2, 40),
        _track(3083, "Teardrop", 208, 104, 3, 30),
        _track(3084, "Karma Police", 208, 104, 4, 20),  # someone else's song of that name
    ):
        lib.items[int(item["ratingKey"])] = item
    for key, artist in (
        (3081, "Portishead"),
        (3082, "Radiohead"),
        (3083, "Massive Attack"),
        (3084, "Some Tribute Band"),
    ):
        lib.items[key] |= {"grandparentTitle": "Various Artists", "originalTitle": artist}
    for key in (300, 301, 302, 303):
        lib.items[key]["grandparentTitle"] = "Radiohead"
    lib.items[304]["grandparentTitle"] = "Portishead"
    lib.details[208] = {"Format": _tags("Album")}  # Plex didn't say compilation: its artist does
    owner, sid = await owner_with_library(app, plex)

    best_of = (await owner.get(f"/api/servers/{sid}/library/albums/201")).json()
    assert best_of["release_type"] == "Compilation"
    assert [(s["rating_key"], s["artist"], s["songs"]) for s in best_of["song_sources"]] == [
        (200, "Radiohead", 1)  # Karma Police; not the remaster itself
    ]
    assert best_of["sources_unmatched"] == 1  # Lucky: only on the compilation

    various = (await owner.get(f"/api/servers/{sid}/library/albums/208")).json()
    assert [
        (s["rating_key"], s["title"], s["artist"], s["release_type"], s["songs"])
        for s in various["song_sources"]
    ] == [
        (202, "Dummy", "Portishead", "EP", 1),
        (200, "OK Computer", "Radiohead", "Album", 1),
    ]
    assert various["sources_unmatched"] == 2  # Teardrop, and the other Karma Police
