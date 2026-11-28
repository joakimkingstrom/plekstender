"""Smart playlists: choosing songs, the builder helpers, and writing playlists to Plex."""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text, update

from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import Album, MediaFile, Playlist, SongCover, User, UserServer
from plekstender.playlists.service import build
from plekstender.tags.read import LYRICS_READER
from plekstender.worker import schedule_due_playlists
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def preview(client: httpx.AsyncClient, sid: int, **definition: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/servers/{sid}/playlists/preview", json=definition, headers=CSRF)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def keys(body: dict[str, Any]) -> list[int]:
    return [s["rating_key"] for s in body["songs"]]


async def run_queued_builds() -> list[Playlist]:
    """What the worker does: build queued playlists."""
    sessions = get_sessionmaker()
    async with sessions() as db:
        ids = list(await db.scalars(select(Playlist.id).where(Playlist.status == "queued")))
    async with httpx.AsyncClient() as http:
        for playlist_id in ids:
            async with sessions() as db:
                await build(db, http, get_settings(), playlist_id, random.Random(1))  # noqa: S311
    async with sessions() as db:
        await db.execute(text("DELETE FROM procrastinate_jobs"))
        await db.commit()
        return list(await db.scalars(select(Playlist).where(Playlist.id.in_(ids))))


async def test_choosing_songs(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)

    # Radiohead: Karma Police (twice; once as the 1997 original), Paranoid Android (rated
    # 10), Lucky. Most popular first.
    body = await preview(owner, sid, artists=[100], order="popular")
    assert keys(body) == [301, 300, 303]
    assert (body["matched"], body["count"], body["artists"]) == (3, 3, 1)
    assert body["duration_ms"] == 3 * 240_000
    assert body["top_artists"] == ["Radiohead"]

    rated = {"min_rating": 8, "include_unrated": False}
    assert keys(await preview(owner, sid, artists=[100], rules=rated)) == [300]
    assert sorted(keys(await preview(owner, sid, artists=[100], rules={"min_rating": 8}))) == [
        300,
        301,
        303,
    ]
    # Top 50% per artist by popularity: of three songs, only the most popular.
    assert keys(await preview(owner, sid, artists=[100], rules={"top_percent": 50})) == [301]
    either = {**rated, "top_percent": 50, "op": "OR"}
    assert sorted(keys(await preview(owner, sid, artists=[100], rules=either))) == [300, 301]
    both = {**rated, "top_percent": 50, "op": "AND"}
    assert await preview(owner, sid, artists=[100], rules=both) == {
        "matched": 0,
        "count": 0,
        "duration_ms": 0,
        "artists": 0,
        "top_artists": [],
        "songs": [],
    }
    # Chosen songs are always in, whatever the rules.
    picked = await preview(owner, sid, artists=[100], tracks=[304], rules=rated)
    assert sorted(keys(picked)) == [300, 304]
    # The limit takes the most popular, or a random few.
    assert keys(await preview(owner, sid, artists=[100], order="popular", limit=2)) == [301, 300]
    assert len(keys(await preview(owner, sid, artists=[100, 101], limit=2))) == 2

    resp = await owner.post(f"/api/servers/{sid}/playlists/preview", json={}, headers=CSRF)
    assert resp.status_code == 422  # nothing chosen

    # A shared user only gets what their access covers (Secret Band is hidden from them).
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = (
        await friend.post("/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF)
    ).json()["server"]["id"]
    assert keys(await preview(friend, friend_sid, artists=[102])) == []


async def test_fresh_picks(app: FastAPI, plex: FakePlex) -> None:
    """The best songs of albums added lately, picked again at each refresh."""
    owner, sid = await owner_with_library(app, plex)
    now = dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as db:
        await db.execute(update(Album).values(added_at=now - dt.timedelta(days=200)))
        added = {201: 3, 202: 10}  # OK Computer OKNOTOK 3 days ago, Dummy 10 days ago
        for key, days in added.items():
            await db.execute(
                update(Album)
                .where(Album.rating_key == key)
                .values(added_at=now - dt.timedelta(days=days))
            )
        await db.commit()

    def new(days: int, per_album: int) -> dict[str, Any]:
        return {"new_albums": {"days": days, "per_album": per_album}, "order": "popular"}

    # Each new album's most popular song. OKNOTOK's Karma Police is its own (the remaster),
    # not the 1997 original, and new albums alone are enough for a playlist.
    assert keys(await preview(owner, sid, **new(30, 1))) == [302, 304]
    assert keys(await preview(owner, sid, **new(30, 2))) == [302, 304, 303]
    assert keys(await preview(owner, sid, **new(5, 3))) == [302, 303]
    assert keys(await preview(owner, sid, **new(1, 3))) == []
    # The rules still apply.
    only_loved = {"min_rating": 8, "include_unrated": False}
    assert keys(await preview(owner, sid, **new(30, 2), rules=only_loved)) == []
    # Nothing chosen at all: refused.
    resp = await owner.post(
        f"/api/servers/{sid}/playlists/preview", json={"order": "popular"}, headers=CSRF
    )
    assert resp.status_code == 422


async def test_songs_about_a_word(app: FastAPI, plex: FakePlex) -> None:
    """Songs with a word in their title, as the word cloud counts them."""
    owner, sid = await owner_with_library(app, plex)

    async def songs(*words: str, **more: Any) -> list[int]:
        return keys(await preview(owner, sid, title_words=list(words), order="popular", **more))

    # Karma Police once (its remaster is the same song), whatever the case.
    assert await songs("KARMA") == [301]
    assert await songs("police", "lucky") == [301, 303]
    assert await songs("Remastered") == []  # brackets don't count, as in the cloud
    assert await songs("Kar") == []  # whole words only
    cloud = (await owner.get(f"/api/servers/{sid}/library/stats/words")).json()
    assert next(w["count"] for w in cloud if w["word"].lower() == "karma") == len(
        await songs("karma")
    )
    # The rules still apply; nothing chosen at all is refused.
    assert await songs("karma", rules={"min_rating": 9, "include_unrated": False}) == []
    bad = await owner.post(
        f"/api/servers/{sid}/playlists/preview", json={"title_words": ["a%b"]}, headers=CSRF
    )
    assert bad.status_code == 422


async def test_cover_and_lyrics_rules(app: FastAPI, plex: FakePlex) -> None:
    """Only covers (or none), and only songs whose files have lyrics of a kind."""
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        us = await db.scalar(select(UserServer).where(UserServer.user_id == user_id))
        assert us is not None
        # Paranoid Android synced, Karma Police plain, Lucky word-synced (and a cover, say);
        # the remaster's file was read before lyrics were looked for.
        for key, kind, reader in (
            (300, "synced", LYRICS_READER),
            (301, "plain", LYRICS_READER),
            (303, "word", LYRICS_READER),
            (302, "word", LYRICS_READER - 1),
        ):
            db.add(
                MediaFile(
                    server_id=us.server_id, path=f"{key}.flac", size=1, mtime=1.0, tags={},
                    rating_key=key, lyrics=kind, reader=reader,
                )
            )  # fmt: skip
        db.add(
            SongCover(
                artist_key="radiohead", title_norm="lucky", status="cover", source="musicbrainz",
                tried="musicbrainz", requests=0, checked_at=dt.datetime.now(dt.UTC),
            )
        )  # fmt: skip
        await db.commit()

    async def songs(**rules: Any) -> list[int]:
        return keys(await preview(owner, sid, artists=[100], order="popular", rules=rules))

    assert await songs() == [301, 300, 303]
    assert await songs(covers="only") == [303]
    assert await songs(covers="none") == [301, 300]
    assert await songs(lyrics="with") == [301, 300, 303]
    assert await songs(lyrics="synced") == [300, 303]
    assert await songs(lyrics="word") == [303]
    assert await songs(lyrics="word", covers="none") == []
    # Chosen songs are always kept.
    kept = await preview(owner, sid, tracks=[301], order="popular", rules={"lyrics": "word"})
    assert keys(kept) == [301]
    # Every cover, no artist chosen (and the rules still apply).
    assert keys(await preview(owner, sid, all_covers=True)) == [303]
    assert keys(await preview(owner, sid, all_covers=True, rules={"lyrics": "synced"})) == [303]
    assert keys(await preview(owner, sid, all_covers=True, rules={"covers": "none"})) == []


async def test_builder_helpers(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/playlists"

    found = (await owner.get(f"{base}/search", params={"q": "head"})).json()
    assert [a["title"] for a in found["artists"]] == ["Radiohead", "Portishead"]
    found = (await owner.get(f"{base}/search", params={"q": "karma"})).json()
    assert [(t["rating_key"], t["artist"]) for t in found["tracks"]] == [(301, "Radiohead")]

    tags = {
        t["name"]: t["id"] for t in (await owner.get(f"/api/servers/{sid}/library/tags")).json()
    }
    trip_hop = (await owner.get(f"{base}/tag-artists", params={"tag_id": tags["Trip Hop"]})).json()
    assert [a["title"] for a in trip_hop] == ["Portishead"]
    rock = (await owner.get(f"{base}/tag-artists", params={"tag_id": tags["Rock"]})).json()
    assert [a["rating_key"] for a in rock] == [100, 102]  # most played first

    # Similar: Plex lists Portishead and Muse for Radiohead; only Portishead is in the
    # library. Sonic: Plex's analysis (Secret Band, Glory Box); Radiohead itself is left out.
    plex.library.nearest[100] = [100, 102, 304]
    resp = await owner.get(f"{base}/suggestions", params={"artists": [100], "tracks": [301]})
    assert resp.status_code == 200, resp.text
    found = resp.json()
    assert [a["title"] for a in found["similar"]] == ["Portishead"]
    assert [a["rating_key"] for a in found["sonic_artists"]] == [102]
    assert [t["rating_key"] for t in found["sonic_tracks"]] == [304]


async def test_playlists_in_plex(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/playlists"
    lib = plex.library

    body = {
        "name": "Radiohead essentials",
        "description": "Best of",
        "definition": {"artists": [100], "order": "popular"},
        "display": {"note": "kept as is"},
        "schedule": "daily",
    }
    resp = await owner.post(base, json=body, headers=CSRF)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["status"] == "queued"
    unbuilt = await owner.get(f"{base}/{created['id']}/export.m3u8")
    assert unbuilt.status_code == 409
    assert created["exportable"] is False

    [built] = await run_queued_builds()
    assert built.status == "ok", built.error
    key = built.plex_playlist_key
    assert key is not None
    assert lib.playlists[key] == {
        "token": "srv-owner",
        "title": "Radiohead essentials",
        "summary": "Best of",
        "items": [301, 300, 303],
    }
    assert built.last_result["count"] == 3
    assert built.next_run_at is not None and built.last_built_at is not None
    assert built.next_run_at - built.last_built_at == dt.timedelta(days=1)

    # The last build as M3U, with Plex's paths or relative to the library's root folder.
    resp = await owner.get(f"{base}/{created['id']}/export.m3u8")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/x-mpegurl")
    assert 'filename="Radiohead essentials.m3u8"' in resp.headers["content-disposition"]
    assert resp.text.splitlines() == [
        "#EXTM3U",
        "#PLAYLIST:Radiohead essentials",
        "#EXTINF:240,Radiohead - Karma Police",
        "/m/301.flac",
        "#EXTINF:240,Radiohead - Paranoid Android",
        "/m/300.flac",
        "#EXTINF:240,Radiohead - Lucky",
        "/m/303.flac",
    ]
    relative = await owner.get(f"{base}/{created['id']}/export.m3u8", params={"paths": "relative"})
    assert relative.text.splitlines()[3] == "301.flac"
    listed = (await owner.get(base)).json()
    assert listed[0]["exportable"] is True

    detail = (await owner.get(f"{base}/{created['id']}")).json()
    assert [a["title"] for a in detail["seed_artists"]] == ["Radiohead"]
    assert detail["display"] == {"note": "kept as is"}

    # Editing replaces the songs in place: same Plex playlist.
    body["definition"] = {
        "artists": [100],
        "tracks": [304],
        "rules": {"min_rating": 10, "include_unrated": False},
    }
    body["name"] = "Favourites"
    resp = await owner.put(f"{base}/{created['id']}", json=body, headers=CSRF)
    assert resp.status_code == 200, resp.text
    lib.bulk_clear_supported = False  # an older server: entries removed one at a time
    [built] = await run_queued_builds()
    assert built.plex_playlist_key == key
    assert lib.playlists[key]["title"] == "Favourites"
    assert sorted(lib.playlists[key]["items"]) == [300, 304]

    # Deleted in Plex: the next build makes a new one.
    del lib.playlists[key]
    await owner.post(f"{base}/{created['id']}/build", headers=CSRF)
    [built] = await run_queued_builds()
    assert built.status == "ok" and built.plex_playlist_key not in (None, key)
    key = built.plex_playlist_key

    # Nothing matching: Plex is left alone and the playlist shows the error.
    body["definition"] = {"artists": [101], "rules": {"min_rating": 10, "include_unrated": False}}
    await owner.put(f"{base}/{created['id']}", json=body, headers=CSRF)
    [built] = await run_queued_builds()
    assert built.status == "error" and built.error and "No songs" in built.error
    assert sorted(lib.playlists[key]["items"]) == [300, 304]

    # Daily schedule: due again a day later.
    assert await schedule_due_playlists(dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)) == 0
    assert await schedule_due_playlists(dt.datetime.now(dt.UTC) + dt.timedelta(days=2)) == 1
    [built] = await run_queued_builds()
    assert built.status == "error"  # still nothing matches

    # Other people can't see it.
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = (
        await friend.post("/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF)
    ).json()["server"]["id"]
    assert (
        await friend.get(f"/api/servers/{friend_sid}/playlists/{created['id']}")
    ).status_code == 404
    assert (await friend.get(f"/api/servers/{friend_sid}/playlists")).json() == []

    listed = (await owner.get(base)).json()
    assert [p["name"] for p in listed] == ["Favourites"]
    resp = await owner.delete(f"{base}/{created['id']}", headers=CSRF)
    assert resp.status_code == 204
    assert key not in lib.playlists
    assert (await owner.get(base)).json() == []
