"""Library sync, browsing, image proxy and exports against the fake Plex server."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from roadiefm.config import get_settings
from roadiefm.db import get_sessionmaker
from roadiefm.db.models import Artist, SyncRun
from roadiefm.sync import runner
from roadiefm.sync.runner import run_sync
from tests.fakes import CSRF, FakePlex, sign_in

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def tiny_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise pagination and detail batching with the small fake library.
    monkeypatch.setattr(runner, "PAGE_SIZE", 2)
    monkeypatch.setattr(runner, "DETAIL_BATCH", 2)


async def run_queued_syncs() -> list[SyncRun]:
    """What the worker does: run queued sync jobs. Returns the finished runs."""
    sessions = get_sessionmaker()
    async with sessions() as db:
        ids = list(
            await db.scalars(
                select(SyncRun.id).where(SyncRun.status == "queued").order_by(SyncRun.id)
            )
        )
    async with httpx.AsyncClient() as http:
        for run_id in ids:
            async with sessions() as db:
                await run_sync(db, http, get_settings(), run_id)
    async with sessions() as db:
        # The worker would have taken these jobs off the queue.
        await db.execute(text("DELETE FROM procrastinate_jobs"))
        await db.commit()
        return list(await db.scalars(select(SyncRun).where(SyncRun.id.in_(ids))))


async def connect(client: httpx.AsyncClient) -> int:
    resp = await client.post(
        "/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF
    )
    assert resp.status_code == 200, resp.text
    server_id: int = resp.json()["server"]["id"]
    return server_id


async def owner_with_library(app: FastAPI, plex: FakePlex) -> tuple[httpx.AsyncClient, int]:
    owner, _ = await sign_in(app, plex, "owner-token")
    server_id = await connect(owner)  # auto-selects the only music library, queues a sync
    runs = await run_queued_syncs()
    assert [(r.kind, r.trigger, r.status) for r in runs] == [("full", "setup", "succeeded")]
    return owner, server_id


async def test_full_sync_mirrors_library(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)

    status = (await owner.get(f"/api/servers/{sid}/sync")).json()
    assert status["active"] is None
    assert status["last"]["status"] == "succeeded"
    assert status["last"]["stats"]["sections"] == {"3": "full"}
    assert status["counts"] == {
        "artists": 3,
        "albums": 4,
        "tracks": 6,
        "rated_tracks": 1,
        "plays": 5,
    }

    by_popularity = (
        await owner.get(f"/api/servers/{sid}/library/artists", params={"sort": "popularity"})
    ).json()
    # Radiohead: Karma Police counts once (2000) + Paranoid Android 1000 + Lucky 500.
    assert [(a["title"], a["popularity"]) for a in by_popularity["items"]] == [
        ("Radiohead", 3500),
        ("Portishead", 800),
        ("Secret Band", 10),
    ]
    radiohead = by_popularity["items"][0]
    assert radiohead["user_rating"] == 8.0
    assert radiohead["album_count"] == 2

    search = (await owner.get(f"/api/servers/{sid}/library/artists", params={"q": "PORT"})).json()
    assert [a["title"] for a in search["items"]] == ["Portishead"]

    genres = (await owner.get(f"/api/servers/{sid}/library/tags", params={"kind": "genre"})).json()
    assert [(t["name"], t["count"]) for t in genres] == [
        ("Rock", 2),
        ("Alternative", 1),
        ("Trip Hop", 1),
    ]
    rock = genres[0]["id"]
    tagged = (await owner.get(f"/api/servers/{sid}/library/artists", params={"tag": rock})).json()
    assert {a["title"] for a in tagged["items"]} == {"Radiohead", "Secret Band"}

    artist = (await owner.get(f"/api/servers/{sid}/library/artists/100")).json()
    assert [(s["name"], s["rating_key"]) for s in artist["similar"]] == [
        ("Portishead", 101),
        ("Muse", None),
    ]
    assert {(t["kind"], t["name"]) for t in artist["tags"]} >= {
        ("genre", "Rock"),
        ("mood", "Melancholy"),
        ("style", "Art Rock"),
        ("country", "United Kingdom"),
    }
    assert [a["title"] for a in artist["albums"]] == ["OK Computer", "OK Computer OKNOTOK"]
    assert [a["popularity"] for a in artist["albums"]] == [3000, 2500]
    top = [t["title"] for t in artist["top_tracks"]]
    assert top[1:] == ["Paranoid Android", "Lucky"]
    assert top[0] in {"Karma Police", "Karma Police (Remastered)"}  # one of the two, not both

    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert album["artist_title"] == "Radiohead"
    assert album["critic_rating"] == 9.0
    assert album["released"] == "1997-05-21"
    assert [(t["number"], t["title"], t["user_rating"]) for t in album["tracks"]] == [
        (2, "Paranoid Android", 10.0),
        (6, "Karma Police", None),
    ]
    assert (await owner.get(f"/api/servers/{sid}/library/albums/999")).status_code == 404


async def test_image_proxy(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    url = f"/api/servers/{sid}/image"
    ok = await owner.get(url, params={"path": "/library/metadata/100/thumb/1700000000"})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/jpeg"
    assert ok.headers["cache-control"].startswith("private")

    for bad in ("https://evil.example/x.jpg", "/library/metadata/100/thumb/../../x", "/:/prefs"):
        assert (await owner.get(url, params={"path": bad})).status_code == 400


async def test_shared_user_sees_only_their_part_of_the_library(
    app: FastAPI, plex: FakePlex
) -> None:
    owner, owner_sid = await owner_with_library(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = await connect(friend)
    runs = await run_queued_syncs()
    assert [r.status for r in runs] == ["succeeded"]

    artists = (await friend.get(f"/api/servers/{friend_sid}/library/artists")).json()
    assert [a["title"] for a in artists["items"]] == ["Portishead", "Radiohead"]
    assert (await friend.get(f"/api/servers/{friend_sid}/library/artists/102")).status_code == 404
    portishead = next(a for a in artists["items"] if a["title"] == "Portishead")
    assert portishead["user_rating"] is None  # the owner's ratings aren't the friend's

    friend_status = (await friend.get(f"/api/servers/{friend_sid}/sync")).json()
    assert friend_status["counts"]["tracks"] == 5
    assert friend_status["counts"]["rated_tracks"] == 1  # the friend's own rating

    # The friend's full sync must not delete what only the owner can see.
    owner_artists = (await owner.get(f"/api/servers/{owner_sid}/library/artists")).json()
    assert "Secret Band" in [a["title"] for a in owner_artists["items"]]

    # Nobody can read someone else's connection.
    assert (await owner.get(f"/api/servers/{friend_sid}/library/artists")).status_code == 404
    assert (await owner.get(f"/api/servers/{friend_sid}/export/tracks.csv")).status_code == 404


async def test_incremental_then_full_sync(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    lib = plex.library

    # Changes in Plex: a rating (no updatedAt change), a renamed album, a new track,
    # and a deleted track.
    lib.rate("srv-owner", 304, 4.0)
    lib.touch(202, title="Dummy (Deluxe)")
    lib.add(dict(lib.items[304], ratingKey="306", title="Roads", index=7, ratingCount=900))
    del lib.items[303]

    resp = await owner.post(f"/api/servers/{sid}/sync", json={"kind": "incremental"}, headers=CSRF)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    # Asking again while one is pending returns the same run.
    again = await owner.post(f"/api/servers/{sid}/sync", json={"kind": "incremental"}, headers=CSRF)
    assert again.json()["id"] == resp.json()["id"]

    lib.requests.clear()
    runs = await run_queued_syncs()
    assert [(r.kind, r.status, r.stats["sections"]) for r in runs] == [
        ("incremental", "succeeded", {"3": "incremental"})
    ]
    assert lib.requests["metadata"] == 1  # only the changed album needed details

    portishead = (await owner.get(f"/api/servers/{sid}/library/artists/101")).json()
    assert [a["title"] for a in portishead["albums"]] == ["Dummy (Deluxe)"]
    assert [t["title"] for t in portishead["top_tracks"]] == ["Roads", "Glory Box"]
    glory_box = next(t for t in portishead["top_tracks"] if t["title"] == "Glory Box")
    assert glory_box["user_rating"] == 4.0
    assert portishead["popularity"] == 1700  # recomputed with the new track

    radiohead = (await owner.get(f"/api/servers/{sid}/library/albums/201")).json()
    assert "Lucky" in [t["title"] for t in radiohead["tracks"]]  # not removed yet

    await owner.post(f"/api/servers/{sid}/sync", json={"kind": "full"}, headers=CSRF)
    runs = await run_queued_syncs()
    assert runs[0].stats["tracks_removed"] == 1
    radiohead = (await owner.get(f"/api/servers/{sid}/library/albums/201")).json()
    assert [t["title"] for t in radiohead["tracks"]] == ["Karma Police (Remastered)"]


async def test_incremental_falls_back_without_activity_filters(
    app: FastAPI, plex: FakePlex
) -> None:
    owner, sid = await owner_with_library(app, plex)
    plex.library.activity_filters_supported = False
    plex.library.rate("srv-owner", 301, 7.0)

    await owner.post(f"/api/servers/{sid}/sync", json={"kind": "incremental"}, headers=CSRF)
    runs = await run_queued_syncs()
    assert runs[0].status == "succeeded"
    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert {t["title"]: t["user_rating"] for t in album["tracks"]}["Karma Police"] == 7.0


async def test_servers_answering_single_keys_still_get_details(
    app: FastAPI, plex: FakePlex
) -> None:
    plex.library.multi_key_metadata = False
    owner, sid = await owner_with_library(app, plex)
    artist = (await owner.get(f"/api/servers/{sid}/library/artists/101")).json()
    assert [t["name"] for t in artist["tags"]] == ["Trip Hop"]


async def test_failed_sync_is_reported(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        await db.execute(text("UPDATE user_servers SET access_token_enc = 'v1.garbage'"))
        await db.commit()
    await owner.post(f"/api/servers/{sid}/sync", json={"kind": "incremental"}, headers=CSRF)
    runs = await run_queued_syncs()
    assert runs[0].status == "failed"
    assert runs[0].error and "Reconnect" in runs[0].error
    status = (await owner.get(f"/api/servers/{sid}/sync")).json()
    assert status["last"]["status"] == "failed"
    # Data from the earlier sync is still there.
    assert status["counts"]["artists"] == 3


async def test_exports(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)

    resp = await owner.get(f"/api/servers/{sid}/export/tracks.csv")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 6
    paranoid = next(r for r in rows if r["title"] == "Paranoid Android")
    assert paranoid["stars"] == "5.0"
    assert paranoid["plays"] == "5"
    assert paranoid["album"] == "OK Computer"
    assert paranoid["genres"] == "Rock"
    assert paranoid["moods"] == "Paranoid"
    assert paranoid["artist_mbid"] == "a74b1b7f"
    assert paranoid["file"] == "/m/300.flac"

    resp = await owner.get(f"/api/servers/{sid}/export/library.json")
    data: dict[str, Any] = json.loads(resp.text)
    assert data["format"] == "roadiefm-library"
    assert [len(data[k]) for k in ("artists", "albums", "tracks")] == [3, 4, 6]
    radiohead = next(a for a in data["artists"] if a["title"] == "Radiohead")
    assert radiohead["similar"] == ["Portishead", "Muse"]
    assert radiohead["tags"]["genre"] == ["Alternative", "Rock"]
    assert radiohead["user"]["stars"] == 4.0


async def test_disconnect_removes_personal_state(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    assert (await owner.delete(f"/api/servers/{sid}", headers=CSRF)).status_code == 204
    async with get_sessionmaker()() as db:
        states = await db.scalar(text("SELECT count(*) FROM user_item_state"))
        artists = len(list(await db.scalars(select(Artist))))
    assert states == 0
    assert artists == 3  # shared metadata stays for other users of the server
