"""Cover songs from SecondHandSongs: looked up gently by the worker when an admin turns it
on, and shown on song rows."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender import cover_songs
from plekstender.db import get_sessionmaker
from plekstender.db.models import SongCover
from tests.fakes import CSRF, FakePlex, sign_in
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


# What SecondHandSongs knows: artists (two called Portishead), what they performed, and
# covers' pages.
ARTISTS = {
    "Radiohead": [(100, "Radiohead")],
    "Portishead": [(101, "Portishead"), (102, "Portishead"), (103, "Portishead Tribute")],
}
PERFORMANCES = {
    100: [
        performance(1, "Karma Police", "Radiohead", True),
        performance(3, "Lucky", "Radiohead", False),  # a re-recording of their own
    ],
    101: [performance(5, "Sour Times", "Portishead", True)],  # not the right Portishead
    102: [
        performance(2, "Glory Box", "Portishead", False),
        performance(6, "Roads", "Portishead", True),
    ],
    103: [performance(9, "Glory Box", "Portishead Tribute", False)],
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
        path = request.url.path
        if path == "/search/performance":  # checking a key
            return httpx.Response(200, json={"totalResults": 0, "resultPage": []})
        if path == "/search/artist":
            found = [
                {"entityType": "artist", "uri": f"{API}/artist/{n}", "commonName": name}
                for n, name in ARTISTS.get(request.url.params["commonName"], [])
            ]
            return httpx.Response(200, json={"totalResults": len(found), "resultPage": found})
        if path.endswith("/performances"):
            return httpx.Response(200, json=PERFORMANCES.get(int(path.split("/")[2]), []))
        n = int(path.rsplit("/", 1)[-1])
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
    status = (await owner.get("/api/admin/cover-songs")).json()
    assert status == {
        "enabled": False,
        "secondhandsongs": True,
        "musicbrainz": True,
        "song_versions": True,
        "api_key": False,
        "per_hour": 90,
        "per_day": 900,
        "songs": 5,
        "checked": 0,
        "covers": 0,
        "by_versions": 0,
        "requests_left": 6,  # three artists, two each (and about 5% covers)
    }

    resp = await owner.put("/api/admin/cover-songs", json={"enabled": True}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    # A tight budget: Radiohead's songs (Karma Police once, for both its releases), and the
    # page saying whose song Lucky is waits for the next run, as does Portishead.
    monkeypatch.setattr(
        cover_songs, "WITHOUT_KEY", cover_songs.Limits(per_minute=60_000, per_hour=12, per_day=900)
    )
    assert await run() == 3
    async with get_sessionmaker()() as db:
        lucky = await db.get(SongCover, ("radiohead", "lucky"))
        assert lucky is not None and (lucky.status, lucky.original_url) == ("cover", None)
    ok_computer = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert {t["cover_of"] for t in ok_computer["tracks"]} == {None}  # not yet
    monkeypatch.setattr(
        cover_songs, "WITHOUT_KEY", cover_songs.Limits(per_minute=60_000, per_hour=90, per_day=900)
    )
    assert await run() == 3
    async with get_sessionmaker()() as db:
        found = {(c.artist_key, c.title_norm): c for c in await db.scalars(select(SongCover))}
    assert {k: (c.status, c.tried) for k, c in found.items()} == {
        ("radiohead", "karma police"): ("original", "secondhandsongs"),
        ("radiohead", "paranoid android"): ("unknown", "secondhandsongs"),
        # Their own song, recorded again.
        ("radiohead", "lucky"): ("original", "secondhandsongs"),
        # The Portishead with the song (not their namesake, nor the tribute band).
        ("portishead", "glory box"): ("cover", "secondhandsongs"),
        ("secret band", "secret song"): ("unknown", "secondhandsongs"),
    }
    # An artist search and their list each (both Portisheads' lists), and a cover's page each:
    # not a request or two per song.
    paths = sorted(r.url.path for r in asked)
    assert paths == [
        "/artist/100/performances",
        "/artist/101/performances",
        "/artist/102/performances",
        "/performance/2",
        "/performance/3",
        "/search/artist",
        "/search/artist",
        "/search/artist",
    ]
    assert sum(c.requests for c in found.values()) == len(asked)
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
        "source": "secondhandsongs",
        # Isaac Hayes isn't in the library.
        "artist_rating_key": None,
        "original_rating_key": None,
        "original_album_rating_key": None,
        "original_versions": 0,
    }
    ok_computer = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert {t["cover_of"] for t in ok_computer["tracks"]} == {None}
    status = (await owner.get("/api/admin/cover-songs")).json()
    assert (status["songs"], status["checked"], status["covers"]) == (5, 5, 1)

    # Nothing due: nothing asked.
    status = (await owner.get("/api/admin/cover-songs")).json()
    assert status["requests_left"] == 0
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
    assert await run() == 0  # 8 requests made in the last hour already
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
        "/api/admin/cover-songs", json={"enabled": True, "api_key": "x" * 36}, headers=CSRF
    )
    assert bad.status_code == 400
    good = await owner.put(
        "/api/admin/cover-songs", json={"enabled": True, "api_key": KEY}, headers=CSRF
    )
    assert good.status_code == 200, good.text
    assert (good.json()["api_key"], good.json()["per_hour"]) == (True, 900)
    await due_again()
    asked.clear()
    assert await run() == 1
    assert [r.headers.get("X-API-Key") for r in asked] == [KEY, KEY]
    # Turned off (the key is kept until removed).
    off = await owner.put("/api/admin/cover-songs", json={"enabled": False}, headers=CSRF)
    assert (off.json()["enabled"], off.json()["api_key"]) == (False, True)
    removed = await owner.put(
        "/api/admin/cover-songs", json={"enabled": False, "remove_key": True}, headers=CSRF
    )
    assert removed.json()["api_key"] is False


@pytest.mark.parametrize("versions", [True, False])
async def test_a_songs_other_versions(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch, versions: bool
) -> None:
    """A cover's song page lists every version of it: the library's other versions are
    settled from it (one request instead of one or more each), when that's turned on."""
    from tests.fakes import _album, _artist, _track

    monkeypatch.setattr(
        cover_songs, "WITHOUT_KEY", cover_songs.Limits(per_minute=60_000, per_hour=90, per_day=900)
    )
    # Three versions of Hurt: Johnny Cash's (looked up first: the most popular), Nine Inch
    # Nails' original, and Sevendust's.
    lib = plex.library
    for item in (
        _artist(110, "Johnny Cash"),
        _artist(111, "Nine Inch Nails"),
        _artist(112, "Sevendust"),
        _album(210, "American IV", 110, 2002),
        _album(211, "The Downward Spiral", 111, 1994),
        _album(212, "Hurt Tribute", 112, 2004),
        _track(3100, "Hurt", 210, 110, 1, 5000),
        _track(3110, "Hurt", 211, 111, 1, 400),
        _track(3120, "Hurt", 212, 112, 1, 300),
    ):
        if item["type"] == "track":
            item["grandparentTitle"] = {
                110: "Johnny Cash",
                111: "Nine Inch Nails",
                112: "Sevendust",
            }[int(item["grandparentRatingKey"])]
        lib.items[int(item["ratingKey"])] = item
    cash, nin, sevendust = (performance(n, "Hurt", a, n == 61) for n, a in (
        (50, "Johnny Cash"), (61, "Nine Inch Nails"), (52, "Sevendust"),
    ))  # fmt: skip
    asked: list[str] = []

    def shs(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        ids = {"Johnny Cash": 110, "Nine Inch Nails": 111, "Sevendust": 112}
        if path == "/search/artist":
            name = request.url.params["commonName"]
            found = (
                [{"uri": f"{API}/artist/{ids[name]}", "commonName": name}] if name in ids else []
            )
            return httpx.Response(200, json={"totalResults": len(found), "resultPage": found})
        if path.endswith("/performances"):
            mine = {110: [cash], 111: [nin], 112: [sevendust]}
            return httpx.Response(200, json=mine.get(int(path.split("/")[2]), []))
        work = {"entityType": "work", "uri": f"{API}/work/60", "title": "Hurt"}
        if path in ("/performance/50", "/performance/52"):
            return httpx.Response(200, json={"originals": [{**work, "original": nin}]})
        if path == "/work/60":
            other = performance(53, "Hurt", "Someone Else", False)
            return httpx.Response(
                200, json={**work, "original": nin, "versions": [cash, nin, sevendust, other]}
            )
        return httpx.Response(404)

    plex.router.route(host="api.secondhandsongs.com").mock(side_effect=shs)
    owner, _sid = await owner_with_library(app, plex)
    body = {"enabled": True, "musicbrainz": False, "song_versions": versions}
    saved = (await owner.put("/api/admin/cover-songs", json=body, headers=CSRF)).json()
    assert saved["song_versions"] is versions
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        from plekstender.config import get_settings

        await cover_songs.run(db, http, get_settings())
    async with get_sessionmaker()() as db:
        hurt = {
            c.artist_key: c
            for c in await db.scalars(select(SongCover).where(SongCover.title_norm == "hurt"))
        }
    assert {k: (c.status, c.original_artist) for k, c in hurt.items()} == {
        "johnny cash": ("cover", "Nine Inch Nails"),
        "nine inch nails": ("original", None),
        "sevendust": ("cover", "Nine Inch Nails"),
    }
    if versions:
        # Cash's list and his cover's page, then the song's page settles the other two: their
        # artists aren't asked about at all.
        assert "/work/60" in asked
        assert not any("/artist/111" in p or "/artist/112" in p for p in asked)
        assert hurt["sevendust"].tried == "secondhandsongs,versions"
        assert hurt["sevendust"].performance_url == "https://secondhandsongs.com/performance/52"
        assert hurt["johnny cash"].requests == 4  # search, list, cover's page, song's page
        status = (await owner.get("/api/admin/cover-songs")).json()
        assert status["by_versions"] == 2
        # Three searches that find nothing (Radiohead, Portishead, Secret Band), and Cash's 4.
        assert len(asked) == 7
    else:
        assert "/work/60" not in asked
        assert "/performance/52" in asked  # Sevendust's own cover page
        assert hurt["sevendust"].tried == "secondhandsongs"
        # The same searches, and Cash's 3, Nine Inch Nails' 2, Sevendust's 3: 4 more.
        assert len(asked) == 11
    # Every request counted against a song (for the hour's and the day's budgets).
    async with get_sessionmaker()() as db:
        assert sum(c.requests for c in await db.scalars(select(SongCover))) == len(asked)


async def test_covers_link_to_the_library(app: FastAPI, plex: FakePlex) -> None:
    """A cover's original artist and song in the library (one release: its album; more: the
    versions), and both sides on artist pages: their covers, and their songs covered."""
    owner, sid = await owner_with_library(app, plex)
    now = dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as db:
        for artist_key, title_norm, artist, title in (
            # Say Glory Box were Portishead's cover of Karma Police (on two Radiohead albums),
            ("portishead", "glory box", "Radiohead", "Karma Police"),
            # and Lucky a cover of someone not in the library.
            ("radiohead", "lucky", "The Unknowns", "Lucky Day"),
        ):
            db.add(
                SongCover(
                    artist_key=artist_key, title_norm=title_norm, status="cover",
                    original_artist=artist, original_title=title, source="secondhandsongs",
                    original_url="https://secondhandsongs.com/performance/1",
                    performance_url=f"https://secondhandsongs.com/performance/{title_norm}",
                    tried="secondhandsongs", requests=0, checked_at=now,
                )
            )  # fmt: skip
        await db.commit()

    dummy = (await owner.get(f"/api/servers/{sid}/library/albums/202")).json()
    assert dummy["tracks"][0]["cover_of"] == {
        "artist": "Radiohead",
        "title": "Karma Police",
        "url": "https://secondhandsongs.com/performance/1",
        "cover_url": "https://secondhandsongs.com/performance/glory box",
        "source": "secondhandsongs",
        "artist_rating_key": 100,
        "original_rating_key": 301,  # OK Computer's, the oldest of the two
        "original_album_rating_key": 200,
        "original_versions": 2,
    }
    oknotok = (await owner.get(f"/api/servers/{sid}/library/albums/201")).json()
    lucky = next(t for t in oknotok["tracks"] if t["title"] == "Lucky")["cover_of"]
    in_library = ("artist_rating_key", "original_rating_key", "original_versions")
    assert [lucky[k] for k in in_library] == [None, None, 0]

    radiohead = (await owner.get(f"/api/servers/{sid}/library/artists/100/covers")).json()
    assert [(c["rating_key"], c["cover_of"]["artist"]) for c in radiohead["covers"]] == [
        (303, "The Unknowns")
    ]
    assert [
        (c["rating_key"], c["artist"], c["artist_rating_key"], c["original_title"],
         c["original_rating_key"], c["original_versions"])
        for c in radiohead["covered"]
    ] == [(304, "Portishead", 101, "Karma Police", 301, 2)]  # fmt: skip
    portishead = (await owner.get(f"/api/servers/{sid}/library/artists/101/covers")).json()
    assert [(c["title"], c["cover_of"]["artist_rating_key"]) for c in portishead["covers"]] == [
        ("Glory Box", 100)
    ]
    assert portishead["covered"] == []
    missing = await owner.get(f"/api/servers/{sid}/library/artists/999/covers")
    assert missing.status_code == 404


async def test_choosing_a_covers_original(app: FastAPI, plex: FakePlex) -> None:
    """A cover with no original artist (MusicBrainz's): the library's songs of its title by
    other artists, oldest first, one of which an admin can choose as the original."""
    from tests.fakes import _album, _artist, _track
    from tests.test_sync import connect, run_queued_syncs

    lib = plex.library
    for item in (
        _artist(120, "Isaac Hayes"),
        _album(220, "Hot Buttered Soul", 120, 1969),
        _track(3200, "Glory Box (Remastered)", 220, 120, 1, 10),
        _track(3201, "Glory Box (Live)", 200, 100, 14, 10),  # Radiohead's, say
    ):
        lib.items[int(item["ratingKey"])] = item
    lib.items[3200]["grandparentTitle"] = "Isaac Hayes"
    lib.items[3201]["grandparentTitle"] = "Radiohead"
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        db.add(
            SongCover(
                artist_key="portishead", title_norm="glory box", status="cover",
                original_title="Glory Box", source="musicbrainz",
                performance_url="https://musicbrainz.org/recording/1",
                original_url="https://musicbrainz.org/work/1",
                tried="musicbrainz", requests=0, checked_at=dt.datetime.now(dt.UTC),
            )
        )  # fmt: skip
        await db.commit()
    base = f"/api/servers/{sid}/library/tracks/304"
    listed = (await owner.get(f"{base}/originals")).json()
    assert (listed["title"], listed["can_choose"]) == ("Glory Box", True)
    assert [(s["rating_key"], s["artist"], s["year"], s["original"]) for s in listed["songs"]] == [
        (3200, "Isaac Hayes", 1969, False),
        (3201, "Radiohead", 1997, False),
    ]  # not Portishead's own
    not_a_cover = await owner.get(f"/api/servers/{sid}/library/tracks/301/originals")
    assert not_a_cover.status_code == 404

    chosen = await owner.put(f"{base}/original", json={"rating_key": 3200}, headers=CSRF)
    assert chosen.status_code == 200, chosen.text
    assert [s["original"] for s in chosen.json()["songs"]] == [True, False]
    dummy = (await owner.get(f"/api/servers/{sid}/library/albums/202")).json()
    cover_of = dummy["tracks"][0]["cover_of"]
    assert (cover_of["artist"], cover_of["title"], cover_of["artist_rating_key"]) == (
        "Isaac Hayes", "Glory Box", 120,
    )  # fmt: skip
    assert cover_of["original_rating_key"] == 3200
    wrong = await owner.put(f"{base}/original", json={"rating_key": 301}, headers=CSRF)
    assert wrong.status_code == 422  # Karma Police isn't Glory Box
    none = await owner.put(f"{base}/original", json={"rating_key": None}, headers=CSRF)
    assert [s["original"] for s in none.json()["songs"]] == [False, False]

    # Others see the list, but only admins choose.
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = await connect(friend)
    await run_queued_syncs()
    seen = (await friend.get(f"/api/servers/{friend_sid}/library/tracks/304/originals")).json()
    assert seen["can_choose"] is False
    refused = await friend.put(
        f"/api/servers/{friend_sid}/library/tracks/304/original",
        json={"rating_key": 3200},
        headers=CSRF,
    )
    assert refused.status_code == 403


async def test_artist_covers_whatever_the_songs_credit(app: FastAPI, plex: FakePlex) -> None:
    """An artist's covers include the songs on their albums that credit someone with them
    (Plex's track artist: "Radiohead & Friends"), as album pages show them."""
    from plekstender.sync.parse import normalise_artist
    from tests.fakes import _track

    guest = _track(3090, "Guest Song", 200, 100, 13, 10)
    guest |= {"grandparentTitle": "Radiohead", "originalTitle": "Radiohead & Friends"}
    plex.library.items[3090] = guest
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        db.add(
            SongCover(
                artist_key=normalise_artist("Radiohead & Friends"), title_norm="guest song",
                status="cover", original_artist="Someone", original_title="Guest Song",
                source="musicbrainz", original_url="https://musicbrainz.org/work/1",
                tried="musicbrainz", requests=0, checked_at=dt.datetime.now(dt.UTC),
            )
        )  # fmt: skip
        await db.commit()
    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert next(t for t in album["tracks"] if t["title"] == "Guest Song")["cover_of"]
    covers = (await owner.get(f"/api/servers/{sid}/library/artists/100/covers")).json()
    assert [(c["title"], c["cover_of"]["source"]) for c in covers["covers"]] == [
        ("Guest Song", "musicbrainz")
    ]


# --- MusicBrainz, the second source --------------------------------------------------------

MB = "https://musicbrainz.org/ws/2"
REC_GLORY = "10000000-0000-4000-8000-000000000304"
REC_LUCKY = "10000000-0000-4000-8000-000000000303"
REC_PARANOID = "10000000-0000-4000-8000-000000000300"
REC_ORIGINAL = "20000000-0000-4000-8000-000000000001"
WORK = "30000000-0000-4000-8000-000000000001"


def performs(work_title: str, *attributes: str) -> dict[str, Any]:
    return {
        "type": "performance",
        "target-type": "work",
        "attributes": list(attributes),
        "work": {"id": WORK, "title": work_title},
    }


async def test_cover_songs_from_musicbrainz(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plekstender import musicbrainz
    from plekstender.config import get_settings

    monkeypatch.setattr(musicbrainz, "INTERVAL", 0.0)
    musicbrainz.clear_cache()
    asked: list[str] = []

    def mb(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.path} {request.url.params.get('inc')}")
        path = request.url.path.removeprefix("/ws/2")
        recordings = {
            f"/recording/{REC_GLORY}": {"relations": [performs("Glory Box", "cover")]},
            f"/recording/{REC_LUCKY}": {"relations": [performs("Lucky")]},
            f"/recording/{REC_ORIGINAL}": {
                "artist-credit": [{"name": "Isaac Hayes", "joinphrase": ""}]
            },
        }
        if path in recordings:
            return httpx.Response(200, json=recordings[path])
        if path == f"/work/{WORK}":
            rel = {"type": "performance", "target-type": "recording"}
            return httpx.Response(
                200,
                json={
                    "title": "Glory Box",
                    "relations": [
                        {
                            **rel,
                            "attributes": ["live"],
                            "recording": {"id": "x1", "title": "Glory Box"},
                        },
                        {
                            **rel,
                            "attributes": ["medley"],
                            "recording": {"id": "x2", "title": "Box / Glory"},
                        },
                        {
                            **rel,
                            "attributes": ["cover"],
                            "recording": {"id": REC_GLORY, "title": "Glory Box"},
                        },
                        {
                            **rel,
                            "attributes": [],
                            "recording": {"id": REC_ORIGINAL, "title": "Glory Box"},
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"error": "Not Found"})

    plex.router.route(host="musicbrainz.org").mock(side_effect=mb)
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        from plekstender.db.models import MediaFile, User, UserServer

        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        us = await db.scalar(select(UserServer).where(UserServer.user_id == user_id))
        assert us is not None
        for key, mbid in (
            (304, REC_GLORY),
            (303, REC_LUCKY),
            (300, REC_PARANOID),
            (301, "not-an-id"),
        ):
            db.add(
                MediaFile(
                    server_id=us.server_id, path=f"{key}.flac", size=1, mtime=1.0,
                    tags={"musicbrainz_trackid": mbid}, rating_key=key,
                )
            )  # fmt: skip
        await db.commit()

    # MusicBrainz only: the songs with a recording MBID in their files.
    body = {"enabled": True, "secondhandsongs": False, "musicbrainz": True}
    assert (await owner.put("/api/admin/cover-songs", json=body, headers=CSRF)).status_code == 200
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        assert await cover_songs.run(db, http, get_settings()) == 3
    async with get_sessionmaker()() as db:
        found = {c.title_norm: c for c in await db.scalars(select(SongCover))}
    assert {k: (c.status, c.source, c.tried, c.requests) for k, c in found.items()} == {
        "glory box": ("cover", "musicbrainz", "musicbrainz", 0),
        "lucky": ("original", "musicbrainz", "musicbrainz", 0),
        "paranoid android": ("unknown", "musicbrainz", "musicbrainz", 0),
    }
    glory = found["glory box"]
    # The song's title, but no original artist: MusicBrainz doesn't say whose recording was
    # first (one request a song: the recording and its link to the song).
    assert (glory.original_artist, glory.original_title) == (None, "Glory Box")
    assert glory.original_url == f"https://musicbrainz.org/work/{WORK}"
    assert glory.performance_url == f"https://musicbrainz.org/recording/{REC_GLORY}"
    assert all(a.startswith("/ws/2/recording/") and a.endswith("work-rels") for a in asked)
    album = (await owner.get(f"/api/servers/{sid}/library/albums/202")).json()
    cover_of = album["tracks"][0]["cover_of"]
    assert (cover_of["artist"], cover_of["title"], cover_of["source"]) == (
        None, "Glory Box", "musicbrainz",
    )  # fmt: skip

    # Nothing more to ask; turned off, nothing at all.
    asked.clear()
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        assert await cover_songs.run(db, http, get_settings()) == 0
    assert asked == []


# --- the original artist tag in the files ---------------------------------------------------


def test_original_artist_tag_in_every_format(tmp_path: Path) -> None:
    from mutagen.id3 import ID3

    from plekstender.tags import keys
    from tests.test_files import make_audio

    for fmt, name in (("mp3", "a.mp3"), ("m4a", "a.m4a"), ("flac", "a.flac")):
        path = tmp_path / name
        make_audio(path, fmt if fmt != "m4a" else "aac")
        audio = keys.open_easy(path)
        if audio.tags is None:
            audio.add_tags()
        audio["originalartist"] = "Isaac Hayes"
        audio.save()
        assert keys.open_easy(path)["originalartist"] == ["Isaac Hayes"], fmt
    assert str(ID3(tmp_path / "a.mp3")["TOPE"]) == "Isaac Hayes"  # the "original artist" frame


async def test_writing_cover_tags(app: FastAPI, plex: FakePlex, tmp_path: Path) -> None:
    from mutagen.flac import FLAC

    from plekstender.worker import apply_tag_changes, scan_music_files
    from tests.test_files import make_audio, tag_flac

    root = tmp_path / "music"
    make_audio(root / "304.flac")
    tag_flac(root / "304.flac", title="Glory Box", artist="Portishead")
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(root)},
        headers=CSRF,
    )
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    # No covers known yet: nothing to write.
    preview = (await owner.post(f"{base}/cover-tags/preview", headers=CSRF)).json()
    assert (preview["files"], preview["changes"]) == (0, 0)
    assert (await owner.post(f"{base}/cover-tags", headers=CSRF)).status_code == 400

    async with get_sessionmaker()() as db:
        db.add(
            SongCover(
                artist_key="portishead", title_norm="glory box", status="cover",
                original_artist="Isaac Hayes", original_title="Ike's Rap II",
                source="secondhandsongs", tried="secondhandsongs", requests=2,
                checked_at=dt.datetime.now(dt.UTC),
            )
        )  # fmt: skip
        await db.commit()
    preview = (await owner.post(f"{base}/cover-tags/preview", headers=CSRF)).json()
    assert (preview["files"], preview["by_field"]) == (1, {"originalartist": 1})
    assert preview["samples"][0] == {
        "path": "304.flac",
        "field": "originalartist",
        "old_unknown": False,
        "old_value": None,
        "new_value": "Isaac Hayes",
    }
    resp = await owner.post(f"{base}/cover-tags", headers=CSRF)
    assert resp.status_code == 201, resp.text
    change_set = resp.json()
    assert change_set["description"] == "Original artists of covers to 1 file"
    await apply_tag_changes.func(set_id=change_set["id"])
    assert FLAC(root / "304.flac")["originalartist"] == ["Isaac Hayes"]
    # Done: nothing left to write. Undone: gone again.
    assert (await owner.post(f"{base}/cover-tags/preview", headers=CSRF)).json()["changes"] == 0
    undo = await owner.post(f"{base}/undo", json={"set_id": change_set["id"]}, headers=CSRF)
    assert undo.status_code == 201, undo.text
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert "originalartist" not in FLAC(root / "304.flac")
