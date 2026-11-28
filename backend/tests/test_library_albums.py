"""Library → Albums: every album with its artist, searched, filtered, and sorted."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from tests.fakes import FakePlex
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_library_albums(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/library/albums"

    async def albums(**params: Any) -> dict[str, Any]:
        resp = await owner.get(base, params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    every = await albums()
    # By artist, then year: Portishead, Radiohead (1997, 2017), Secret Band.
    assert [(a["title"], a["artist_title"]) for a in every["items"]] == [
        ("Dummy", "Portishead"),
        ("OK Computer", "Radiohead"),
        ("OK Computer OKNOTOK", "Radiohead"),
        ("Secret Album", "Secret Band"),
    ]
    assert every["total"] == 4
    # Release types (Plex's Subformat, else its Format, else Album), for the filter.
    assert every["types"] == {"Album": 2, "Compilation": 1, "EP": 1}
    assert [a["release_type"] for a in every["items"]] == ["EP", "Album", "Compilation", "Album"]

    only = await albums(release_type="Compilation")
    assert [a["title"] for a in only["items"]] == ["OK Computer OKNOTOK"]
    assert (only["total"], only["types"]["Album"]) == (1, 2)  # the counts ignore the type
    # By album or artist name.
    assert [a["title"] for a in (await albums(q="head"))["items"]] == [
        "Dummy",
        "OK Computer",
        "OK Computer OKNOTOK",
    ]
    assert [a["title"] for a in (await albums(q="dumm"))["items"]] == ["Dummy"]
    newest = await albums(sort="newest")
    assert [a["year"] for a in newest["items"]] == [2020, 2017, 1997, 1994]
    # A genre: the album's own, or its artist's.
    listed = (await owner.get(f"/api/servers/{sid}/library/tags")).json()
    tags = {t["name"]: t["id"] for t in listed}
    rock = await albums(tag=tags["Rock"])
    # OK Computer's own, OKNOTOK by Radiohead's, Secret Album by Secret Band's.
    assert [a["title"] for a in rock["items"]] == [
        "OK Computer",
        "OK Computer OKNOTOK",
        "Secret Album",
    ]
    trip_hop = await albums(tag=tags["Trip Hop"])  # Portishead's genre
    assert [a["title"] for a in trip_hop["items"]] == ["Dummy"]
    # Paged.
    page = await albums(offset=2, limit=1)
    assert ([a["title"] for a in page["items"]], page["total"]) == (["OK Computer OKNOTOK"], 4)
