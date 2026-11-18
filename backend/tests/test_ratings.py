"""Rating queues, writing ratings to Plex, undo, and listen counts per item."""

from __future__ import annotations

import random
from collections import Counter

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from plekstender.db import get_sessionmaker
from plekstender.history.listen_counts import refresh_listen_counts
from plekstender.ratings.queue import ARTIST_GAP, QueueItem, interleave
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_stats import add_listens
from tests.test_sync import connect, owner_with_library, run_queued_syncs


def item(key: int, artist: int, metric: int) -> QueueItem:
    return QueueItem(
        rating_key=key,
        kind="track",
        title=f"t{key}",
        artist_key=artist,
        subtitle=None,
        album_title=None,
        album_rating_key=None,
        thumb=None,
        year=None,
        duration_ms=None,
        popularity=metric,
        summary=None,
        critic_rating=None,
        user_rating=None,
        plays=0,
    )


def test_interleave_mixes_artists_but_keeps_each_artists_best_first() -> None:
    # Artist 1 is far more popular and has 50 songs; 9 other artists have 5 each.
    items = [item(1000 + i, 1, 10_000 - i) for i in range(50)]
    items += [item(a * 100 + i, a, 1_000 - i) for a in range(2, 11) for i in range(5)]
    items.sort(key=lambda i: -(i.popularity or 0))
    queue = interleave(items, lambda i: float(i.popularity or 0), 30, random.Random(4))  # noqa: S311

    assert len(queue) == 30
    artists = [q.artist_key for q in queue]
    for i, artist in enumerate(artists):  # never the same artist twice within the gap
        assert artist not in artists[max(0, i - ARTIST_GAP) : i]
    by_artist: dict[int | None, list[int]] = {}
    for q in queue:
        by_artist.setdefault(q.artist_key, []).append(q.popularity or 0)
    assert all(v == sorted(v, reverse=True) for v in by_artist.values())
    # The popular artist still gets the biggest share.
    assert Counter(artists).most_common(1)[0][0] == 1


def test_interleave_with_one_artist_keeps_order() -> None:
    items = [item(i, 1, 100 - i) for i in range(5)]
    queue = interleave(items, lambda i: float(i.popularity or 0), 3, random.Random(1))  # noqa: S311
    assert [q.rating_key for q in queue] == [0, 1, 2]


async def queue(client: httpx.AsyncClient, sid: int, **body: object) -> list[int]:
    resp = await client.post(f"/api/servers/{sid}/rate/queue", json=body, headers=CSRF)
    assert resp.status_code == 200, resp.text
    return [i["rating_key"] for i in resp.json()]


async def refresh(client: httpx.AsyncClient) -> None:
    user_id = (await client.get("/api/auth/me")).json()["id"]
    async with get_sessionmaker()() as db:
        await refresh_listen_counts(db, user_id)
        await db.commit()


@pytest.mark.db
async def test_queues(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await add_listens(
        user_id,
        [
            (1, "Radiohead", "OK Computer OKNOTOK 1997 2017", "Lucky"),
            (2, "Radiohead", "OK Computer OKNOTOK 1997 2017", "Lucky"),
            (3, "Radiohead", "OK Computer", "Karma Police (Remastered)"),
        ],
    )
    await refresh(owner)

    # Songs: Paranoid Android (300) is rated; Karma Police exists twice (301, 302 remaster)
    # and is offered once, as the 1997 original.
    unrated = await queue(owner, sid, kind="track", pool="unrated", order="weighted", limit=50)
    assert sorted(unrated) == [301, 303, 304, 305]
    everything = await queue(owner, sid, kind="track", pool="all", order="popular", limit=50)
    assert sorted(everything) == [300, 301, 303, 304, 305]
    # Listened: Plex plays (300) or listens in the history (Lucky, Karma Police).
    listened = await queue(owner, sid, kind="track", pool="listened", order="plays", limit=50)
    assert sorted(listened) == [300, 301, 303]
    assert await queue(
        owner, sid, kind="track", pool="unrated", exclude=[301, 303, 304], limit=50
    ) == [305]

    resp = await owner.post(
        f"/api/servers/{sid}/rate/queue",
        json={"kind": "track", "pool": "all", "order": "plays", "limit": 50},
        headers=CSRF,
    )
    songs = {i["rating_key"]: i for i in resp.json()}
    assert (songs[303]["plays"], songs[303]["subtitle"], songs[303]["album_title"]) == (
        2,
        "Radiohead",
        "OK Computer OKNOTOK",
    )
    assert songs[301]["plays"] == 1  # the remaster's listen counts for the song
    assert songs[300]["user_rating"] == 10

    assert sorted(await queue(owner, sid, kind="album", pool="unrated", limit=50)) == [
        200,
        201,
        202,
        203,
    ]
    assert sorted(await queue(owner, sid, kind="artist", pool="unrated", limit=50)) == [101, 102]
    assert await queue(owner, sid, kind="artist", pool="listened", limit=50) == [100]


@pytest.mark.db
async def test_listen_counts_per_item(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await add_listens(
        user_id,
        [
            (1, "Radiohead", "OK Computer", "Karma Police"),
            (2, "radiohead", "OK Computer (Collector's Edition)", "Karma Police"),
            (3, "Radiohead", "OKNOTOK", "Karma Police (Remastered)"),
            (4, "Portishead", "Dummy", "Glory Box"),
            (5, "Someone Else", "Elsewhere", "Karma Police"),  # a different artist's song
        ],
    )
    await refresh(owner)
    async with get_sessionmaker()() as db:
        rows = dict(
            (
                await db.execute(
                    text("SELECT rating_key, listens FROM user_item_state WHERE user_id = :u"),
                    {"u": user_id},
                )
            ).all()
        )
    assert (rows[301], rows[302]) == (3, 3)  # both releases of Karma Police
    # OK Computer: only listens under that album name ("Collector's Edition" is another).
    assert rows[200] == 1
    assert rows[201] == 0
    assert (rows[100], rows[101], rows[102]) == (3, 1, 0)
    assert rows[304] == 1

    # Re-running reflects deletions too.
    await owner.delete("/api/listens", headers=CSRF)
    await refresh(owner)
    async with get_sessionmaker()() as db:
        total = await db.scalar(
            text("SELECT sum(listens) FROM user_item_state WHERE user_id = :u"), {"u": user_id}
        )
    assert total == 0


@pytest.mark.db
async def test_rating_writes_to_plex_and_undo(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)

    rated = await owner.post(
        f"/api/servers/{sid}/ratings", json={"rating_key": 303, "rating": 7}, headers=CSRF
    )
    assert rated.status_code == 200, rated.text
    assert rated.json() == {"rating_key": 303, "item_type": "track", "user_rating": 7.0}
    assert plex.library.rates == [("srv-owner", 303, 7.0)]
    album = (await owner.get(f"/api/servers/{sid}/library/albums/201")).json()
    assert {t["title"]: t["user_rating"] for t in album["tracks"]}["Lucky"] == 7.0
    assert 303 not in await queue(owner, sid, kind="track", pool="unrated", limit=50)

    cleared = await owner.post(
        f"/api/servers/{sid}/ratings", json={"rating_key": 303, "rating": None}, headers=CSRF
    )
    assert cleared.json()["user_rating"] is None
    assert plex.library.rates[-1] == ("srv-owner", 303, -1.0)

    await owner.post(
        f"/api/servers/{sid}/ratings", json={"rating_key": 202, "rating": 9}, headers=CSRF
    )
    undo = await owner.post(f"/api/servers/{sid}/ratings/undo", headers=CSRF)
    assert undo.json() == {"rating_key": 202, "item_type": "album", "user_rating": None}
    assert plex.library.rates[-1] == ("srv-owner", 202, -1.0)
    undo = await owner.post(f"/api/servers/{sid}/ratings/undo", headers=CSRF)
    assert undo.json()["user_rating"] == 7.0  # the clear is undone: back to 7
    undo = await owner.post(f"/api/servers/{sid}/ratings/undo", headers=CSRF)
    assert undo.json()["user_rating"] is None
    assert (await owner.post(f"/api/servers/{sid}/ratings/undo", headers=CSRF)).status_code == 404

    assert (
        await owner.post(
            f"/api/servers/{sid}/ratings", json={"rating_key": 303, "rating": 11}, headers=CSRF
        )
    ).status_code == 422
    plex.library.rate_fails = True
    failed = await owner.post(
        f"/api/servers/{sid}/ratings", json={"rating_key": 303, "rating": 4}, headers=CSRF
    )
    assert failed.status_code == 502
    status = (await owner.get(f"/api/servers/{sid}/library/albums/201")).json()
    assert {t["title"]: t["user_rating"] for t in status["tracks"]}["Lucky"] is None  # unchanged


@pytest.mark.db
async def test_friends_rate_with_their_own_account(app: FastAPI, plex: FakePlex) -> None:
    await owner_with_library(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")
    fsid = await connect(friend)
    await run_queued_syncs()

    ok = await friend.post(
        f"/api/servers/{fsid}/ratings", json={"rating_key": 304, "rating": 8}, headers=CSRF
    )
    assert ok.status_code == 200
    assert plex.library.rates == [("srv-friend", 304, 8.0)]
    hidden = await friend.post(
        f"/api/servers/{fsid}/ratings", json={"rating_key": 305, "rating": 8}, headers=CSRF
    )
    assert hidden.status_code == 404  # not visible to the friend's Plex account
    assert 305 not in await queue(friend, fsid, kind="track", pool="all", limit=50)


@pytest.mark.db
async def test_library_plays_include_the_listening_history(app: FastAPI, plex: FakePlex) -> None:
    """Plex only knows plays from when it was in use; imported history counts too."""
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await add_listens(
        user_id,
        [(10 + i, "Portishead", "Dummy", "Glory Box") for i in range(8)]
        + [(30, "Radiohead", "OK Computer", "Paranoid Android")],
    )
    # Before the per-item counts are refreshed, detail pages already include the history.
    portishead = (await owner.get(f"/api/servers/{sid}/library/artists/101")).json()
    assert portishead["plays"] == 8
    dummy = (await owner.get(f"/api/servers/{sid}/library/albums/202")).json()
    assert dummy["plays"] == 8

    await refresh(owner)
    by_plays = (
        await owner.get(f"/api/servers/{sid}/library/artists", params={"sort": "plays"})
    ).json()["items"]
    assert [(a["title"], a["plays"]) for a in by_plays][:2] == [("Portishead", 8), ("Radiohead", 1)]
    # Plex's own count wins where it's higher (Paranoid Android: 5 Plex plays, 1 listen).
    radiohead = (await owner.get(f"/api/servers/{sid}/library/artists/100")).json()
    assert {t["title"]: t["plays"] for t in radiohead["top_tracks"]}["Paranoid Android"] == 5
    status = (await owner.get(f"/api/servers/{sid}/sync")).json()
    assert status["counts"]["plays"] == 5 + 8  # per song: the higher of the two
