"""Library -> Releases: artists' release groups from MusicBrainz, new releases from the
artists you follow, and what's missing from the library."""

from __future__ import annotations

import datetime as dt
import io

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select, update

from plekstender import musicbrainz, releases
from plekstender.artwork import fetch as fetch_mod
from plekstender.db import get_sessionmaker
from plekstender.db.models import Album, Artist, ArtistReleaseCheck
from tests.fakes import CSRF, FakePlex
from tests.test_sync import owner_with_library

RADIOHEAD = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
PORTISHEAD = "8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"
GONE = "00000000-0000-4000-8000-000000000000"


def rg(n: int, title: str, primary: str | None, date: str, *secondary: str) -> dict[str, object]:
    return {
        "id": f"{n:08d}-0000-4000-8000-000000000000",
        "title": title,
        "primary-type": primary,
        "secondary-types": list(secondary),
        "first-release-date": date,
    }


def days(n: int) -> str:
    return (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=n)).isoformat()


def test_kinds_and_dates() -> None:
    assert releases.classify("Album", []) == "album"
    assert releases.classify("EP", []) == "ep"
    assert releases.classify("Single", []) == "single"
    assert releases.classify("Album", ["Live"]) == "live"
    assert releases.classify("Album", ["Compilation", "Live"]) == "live"
    assert releases.classify("Album", ["Compilation"]) == "compilation"
    assert releases.classify("Album", ["Soundtrack"]) == "other"
    assert releases.classify("Broadcast", []) == "other"
    assert releases.classify(None, []) == "other"
    assert releases.release_date("2019-05-21") == dt.date(2019, 5, 21)
    assert releases.release_date("2019-05") == dt.date(2019, 5, 1)
    assert releases.release_date("2019") == dt.date(2019, 1, 1)
    assert releases.release_date("") is None
    assert releases.release_date("2019-02-30") is None
    rows = releases.rows_for(
        RADIOHEAD,
        [
            rg(1, "Kid A", "Album", "2000-10-02"),
            rg(1, "Kid A", "Album", "2000-10-02"),  # listed twice: kept once
            {"id": "not-an-mbid", "title": "Junk"},
            rg(2, "", "Album", "2001"),
        ],
    )
    assert [(r["title"], r["title_norm"], r["kind"]) for r in rows] == [("Kid A", "kid a", "album")]


@pytest.fixture
def web(plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """MusicBrainz (two pages for Radiohead) and the Cover Art Archive."""
    monkeypatch.setattr(musicbrainz, "INTERVAL", 0.0)
    monkeypatch.setattr(releases, "PAUSE", 0.0)

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(fetch_mod, "resolver", resolve)
    asked = {"musicbrainz": 0}
    groups = {
        RADIOHEAD: [
            rg(1, "OK Computer", "Album", "1997-05-21"),  # in the library, by name
            rg(2, "Kid A", "Album", "2000-10-02"),
            rg(3, "Amnesiac", "Album", "2001-06-05"),
            rg(4, "I Might Be Wrong", "Album", "2001-11-12", "Live"),
            rg(5, "Creep", "Single", "1992-09-21"),
            rg(6, "A Fresh One", "Album", days(-10)),
            rg(7, "Still to Come", "EP", days(30)),
            rg(8, "Long Ago", "Album", days(-400)),
        ],
        PORTISHEAD: [
            rg(10, "Third", "Album", "2008-04-28"),
            rg(11, "Named Differently", "Album", "1994-08-22"),  # in the library, by MBID
            rg(12, "Brand New Portishead", "Album", days(-5)),
        ],
    }

    def browse(request: httpx.Request) -> httpx.Response:
        asked["musicbrainz"] += 1
        artist = request.url.params["artist"]
        if artist not in groups:
            return httpx.Response(404, json={"error": "Not Found"})
        offset = int(request.url.params["offset"])
        page = groups[artist][offset : offset + 5]  # pretend pages of 5
        return httpx.Response(
            200, json={"release-group-count": len(groups[artist]), "release-groups": page}
        )

    plex.router.get("https://musicbrainz.org/ws/2/release-group").mock(side_effect=browse)
    cover = io.BytesIO()
    Image.new("RGB", (500, 500), "navy").save(cover, "JPEG")
    plex.router.get(
        "https://coverartarchive.org/release-group/00000002-0000-4000-8000-000000000000/front-250"
    ).mock(return_value=httpx.Response(200, content=cover.getvalue()))
    plex.router.get(
        "https://coverartarchive.org/release-group/00000003-0000-4000-8000-000000000000/front-250"
    ).mock(return_value=httpx.Response(404))
    return asked


@pytest.mark.db
async def test_releases(app: FastAPI, plex: FakePlex, web: dict[str, int]) -> None:
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        await db.execute(update(Artist).where(Artist.rating_key == 100).values(mbid=RADIOHEAD))
        await db.execute(update(Artist).where(Artist.rating_key == 101).values(mbid=PORTISHEAD))
        await db.execute(update(Artist).where(Artist.rating_key == 102).values(mbid=GONE))
        await db.execute(
            update(Album)
            .where(Album.rating_key == 202)
            .values(mbid="00000011-0000-4000-8000-000000000000")
        )
        await db.commit()

    # Nothing read yet: the coverage says so.
    first = (await owner.get(f"/api/servers/{sid}/releases/missing")).json()
    assert first["artists"] == []
    assert first["coverage"] == {"artists": 3, "with_mbid": 3, "checked": 0}

    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        # Radiohead (rated 4 stars, so followed) comes first of those never read.
        assert (await releases.to_check(db, dt.datetime.now(dt.UTC)))[0] == RADIOHEAD
        assert await releases.look_up(db, http) == 3
        rows = await db.execute(select(ArtistReleaseCheck.artist_mbid, ArtistReleaseCheck.found))
        checks = {mbid: found for mbid, found in rows.all()}
    assert checks == {RADIOHEAD: True, PORTISHEAD: True, GONE: False}
    assert web["musicbrainz"] == 4  # Radiohead in two pages
    async with get_sessionmaker()() as db:
        now = dt.datetime.now(dt.UTC)
        assert await releases.to_check(db, now) == []
        # Followed artists are read again daily, the rest every two weeks.
        assert await releases.to_check(db, now + dt.timedelta(days=2)) == [RADIOHEAD]
        assert len(await releases.to_check(db, now + dt.timedelta(days=15))) == 3

    missing = (await owner.get(f"/api/servers/{sid}/releases/missing")).json()
    assert missing["coverage"] == {"artists": 3, "with_mbid": 3, "checked": 3}
    assert missing["total_artists"] == 2
    assert [
        (a["name"], [(r["title"], r["kind"]) for r in a["releases"]]) for a in missing["artists"]
    ] == [
        ("Portishead", [("Third", "album"), ("Brand New Portishead", "album")]),
        (
            "Radiohead",
            [("Kid A", "album"), ("Amnesiac", "album"), ("Long Ago", "album"),
             ("A Fresh One", "album")],
        ),
    ]  # fmt: skip
    # Other kinds on request; nothing that isn't out yet.
    everything = (
        await owner.get(
            f"/api/servers/{sid}/releases/missing",
            params=[("kind", k) for k in releases.KINDS] + [("q", "radio")],
        )
    ).json()
    [radiohead] = everything["artists"]
    assert [r["title"] for r in radiohead["releases"]] == [
        "Creep",
        "Kid A",
        "Amnesiac",
        "I Might Be Wrong",
        "Long Ago",
        "A Fresh One",
    ]
    paged = (await owner.get(f"/api/servers/{sid}/releases/missing", params={"limit": 1})).json()
    assert ([a["name"] for a in paged["artists"]], paged["total_artists"]) == (["Portishead"], 2)

    # New: the followed artist's last 90 days and what's coming, not Portishead (not followed).
    new = (await owner.get(f"/api/servers/{sid}/releases/new")).json()
    items = new["items"]
    assert [(r["title"], r["upcoming"], r["in_library"], r["artist"]["name"]) for r in items] == [
        ("Still to Come", True, False, "Radiohead"),
        ("A Fresh One", False, False, "Radiohead"),
    ]  # fmt: skip
    assert (new["following"], new["new_days"]) == (1, 90)
    albums_only = await owner.get(f"/api/servers/{sid}/releases/new", params={"kind": "album"})
    assert [r["title"] for r in albums_only.json()["items"]] == ["A Fresh One"]
    assert new["settings"] == {"kinds": ["album", "ep"], "follow_rating": 8, "follow_top": 50}

    # Your own settings: which kinds, and whom you follow (here: nobody).
    async def settings(**releases: object) -> None:
        resp = await owner.put(
            "/api/auth/me/preferences", json={"releases": releases}, headers=CSRF
        )
        assert resp.status_code == 200, resp.text

    await settings(kinds=["single", "album"], follow_rating=0, follow_top=0)
    nobody = (await owner.get(f"/api/servers/{sid}/releases/new")).json()
    assert (nobody["following"], nobody["items"]) == (0, [])
    async with get_sessionmaker()() as db:
        later = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
        assert await releases.to_check(db, later) == []  # nobody followed: not read daily
    singles = (
        await owner.get(f"/api/servers/{sid}/releases/missing", params={"q": "radio"})
    ).json()
    assert singles["artists"][0]["releases"][0]["title"] == "Creep"
    await settings(kinds=["album"], follow_rating=6, follow_top=50)
    fresh = (await owner.get(f"/api/servers/{sid}/releases/new")).json()
    assert [r["title"] for r in fresh["items"]] == ["A Fresh One"]
    assert (await owner.put(
        "/api/auth/me/preferences", json={"releases": {"follow_top": -1}}, headers=CSRF
    )).status_code == 422  # fmt: skip

    # Hiding one: gone from both lists, back with include_hidden, and shown again.
    kid_a = "00000002-0000-4000-8000-000000000000"
    hid = await owner.put(f"/api/servers/{sid}/releases/hidden/{kid_a}", headers=CSRF)
    assert hid.status_code == 204
    again = await owner.put(f"/api/servers/{sid}/releases/hidden/{kid_a}", headers=CSRF)
    assert again.status_code == 204
    after = (await owner.get(f"/api/servers/{sid}/releases/missing", params={"q": "radio"})).json()
    assert "Kid A" not in [r["title"] for r in after["artists"][0]["releases"]]
    shown = (
        await owner.get(
            f"/api/servers/{sid}/releases/missing", params={"q": "radio", "include_hidden": True}
        )
    ).json()
    assert {r["title"]: r["hidden"] for r in shown["artists"][0]["releases"]}["Kid A"] is True
    await owner.delete(f"/api/servers/{sid}/releases/hidden/{kid_a}", headers=CSRF)
    back = (await owner.get(f"/api/servers/{sid}/releases/missing", params={"q": "radio"})).json()
    assert "Kid A" in [r["title"] for r in back["artists"][0]["releases"]]
    bad = await owner.put(f"/api/servers/{sid}/releases/hidden/not-an-id", headers=CSRF)
    assert bad.status_code == 422

    # Covers come through the server; none is a cacheable 404.
    cover = await owner.get(f"/api/releases/{kid_a}/cover")
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(cover.content)).size == (250, 250)
    none = await owner.get("/api/releases/00000003-0000-4000-8000-000000000000/cover")
    assert none.status_code == 404
    assert "max-age" in none.headers["cache-control"]
    assert (await owner.get("/api/releases/nope/cover")).status_code == 422

    # Another server's id isn't yours.
    assert (await owner.get(f"/api/servers/{sid + 99}/releases/new")).status_code == 404


@pytest.mark.db
async def test_a_huge_discography_and_a_failing_artist_dont_stop_the_rest(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """7,500 release groups (more than one statement takes values for), and an artist
    MusicBrainz fails on: the others are still read, and the run doesn't get stuck."""
    from sqlalchemy import func

    from plekstender.db.models import ReleaseGroup

    monkeypatch.setattr(musicbrainz, "INTERVAL", 0.0)
    monkeypatch.setattr(releases, "PAUSE", 0.0)
    huge = [rg(n, f"Live {n}", "Album", "1975", "Live") for n in range(1, 7501)]

    def browse(request: httpx.Request) -> httpx.Response:
        artist = request.url.params["artist"]
        offset = int(request.url.params["offset"])
        if artist == GONE:
            return httpx.Response(500)
        groups = huge if artist == RADIOHEAD else [rg(9001, "Third", "Album", "2008")]
        page = groups[offset : offset + 100]
        return httpx.Response(
            200, json={"release-group-count": len(groups), "release-groups": page}
        )

    plex.router.get("https://musicbrainz.org/ws/2/release-group").mock(side_effect=browse)
    await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        await db.execute(update(Artist).where(Artist.rating_key == 100).values(mbid=RADIOHEAD))
        await db.execute(update(Artist).where(Artist.rating_key == 101).values(mbid=PORTISHEAD))
        await db.execute(update(Artist).where(Artist.rating_key == 102).values(mbid=GONE))
        await db.commit()
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        # Radiohead (followed) first, then GONE (failing), then Portishead.
        assert await releases.to_check(db, dt.datetime.now(dt.UTC)) == [RADIOHEAD, GONE, PORTISHEAD]
        assert await releases.look_up(db, http) == 2
        stored = dict(
            (
                await db.execute(
                    select(ReleaseGroup.artist_mbid, func.count()).group_by(
                        ReleaseGroup.artist_mbid
                    )
                )
            ).all()
        )
        assert stored == {RADIOHEAD: 7500, PORTISHEAD: 1}
        # The failing artist is tried again next time.
        assert await releases.to_check(db, dt.datetime.now(dt.UTC)) == [GONE]
