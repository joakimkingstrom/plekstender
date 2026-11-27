# ruff: noqa: E501 - setlists and expected names, one per line
"""Playlists for gigs: before a starred one (what the artists have been playing lately) and
after one the user went to (its setlist), for people who turn them on."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender.db import get_sessionmaker
from plekstender.db.models import Gig, GigPlaylist, LiveShow, Playlist, User, UserServer
from plekstender.gigs import playlists as gig_playlists
from plekstender.gigs.playlists import SetSong, delete_after, deleted_on, usual_setlist
from plekstender.playlists.definition import Definition
from tests.fakes import CSRF, FakePlex
from tests.test_playlists import run_queued_builds
from tests.test_sync import owner_with_library

TODAY = dt.datetime.now(dt.UTC).date()


def day(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).strftime("%d-%m-%Y")


def setlist(artist: str, date: str, *songs: str | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{artist}-{date}",
        "eventDate": date,
        "artist": {"name": artist},
        "venue": {
            "id": "v",
            "name": "Somewhere",
            "city": {"name": "London", "country": {"code": "GB"}},
        },
        "sets": {"set": [{"song": [s if isinstance(s, dict) else {"name": s} for s in songs]}]},
    }


def test_the_usual_setlist() -> None:
    shows = [
        [SetSong("Lucky"), SetSong("Paranoid Android"), SetSong("Karma Police")],
        [SetSong("Paranoid Android"), SetSong("Karma Police"), SetSong("Creep")],
    ]
    # Played at half the shows or more: all of them (fewer than enough: the most played),
    # in the order they usually come.
    assert [s.name for s in usual_setlist(shows)] == [
        "Lucky",
        "Paranoid Android",
        "Karma Police",
        "Creep",
    ]
    many = [[SetSong(f"Song {i}") for i in range(10)] for _ in range(3)]
    many[0].append(SetSong("Once"))
    many[1].append(SetSong("Twice"))
    many[2].append(SetSong("Twice"))
    names = [s.name for s in usual_setlist(many)]
    assert "Twice" in names and "Once" in names  # a third of the shows is enough
    assert usual_setlist([]) == []


@pytest.mark.db
async def test_gig_playlists(app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> None:
    from plekstender.worker import make_gig_playlists_now

    monkeypatch.setattr(gig_playlists, "SETLISTFM_INTERVAL", 0.0)
    asked: list[dict[str, str]] = []

    def search(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search/artists"):  # checking the key
            return httpx.Response(200, json={"artist": []})
        params = dict(request.url.params)
        asked.append(params)
        if params.get("artistName") == "Radiohead" or params.get("artistMbid"):
            return httpx.Response(200, json={"total": 5, "setlist": [
                setlist("Radiohead", day(20)),  # announced, no songs yet
                setlist("Radiohead", day(-3), "Lucky", "Paranoid Android", "Karma Police"),
                setlist("Radiohead", day(-5), "Paranoid Android", "Karma Police", "Creep"),
                setlist("Radiohead", day(-7), "Lucky", "Paranoid Android", "Karma Police", "Exit Music"),
                setlist("Radiohead", day(-9), "Paranoid Android", "Karma Police"),
            ]})  # fmt: skip
        if params.get("artistName") == "Portishead" and params.get("date") == day(-2):
            return httpx.Response(200, json={"total": 1, "setlist": [
                setlist("Portishead", day(-2), {"name": "Intro", "tape": True}, "Glory Box", "Roads"),
            ]})  # fmt: skip
        return httpx.Response(404, json={"code": 404})

    plex.router.route(host="api.setlist.fm").mock(side_effect=search)
    owner, sid = await owner_with_library(app, plex)
    await owner.put("/api/admin/setlistfm", json={"api_key": "k" * 36}, headers=CSRF)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        assert user_id is not None
        now = dt.datetime.now(dt.UTC)
        db.add(
            LiveShow(
                user_id=user_id, source="jambase", source_id="jambase:9", name="Radiohead",
                festival=False, status="scheduled", venue="The O2", city="London", country="GB",
                fetched_at=now, start_date=TODAY + dt.timedelta(days=10), starred_at=now,
                artists=[{"name": "Radiohead", "headliner": True, "server_id": sid, "rating_key": 100}],
                performers=[{"name": "Radiohead", "headliner": True}],
            )
        )  # fmt: skip
        db.add(
            Gig(
                user_id=user_id, date=TODAY - dt.timedelta(days=2), venue="Royal Albert Hall",
                city="London", artists=[{"name": "Portishead", "server_id": sid, "rating_key": 101}],
            )
        )  # fmt: skip
        await db.commit()

    # Off (the default): nothing is made.
    await make_gig_playlists_now.func(user_id=user_id)
    assert asked == []
    assert (await owner.get("/api/gigs/playlists")).json() == {"setlistfm": True, "items": []}

    prefs = {"gig_playlists": {"before": True, "after": True}}
    assert (
        await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    ).status_code == 200
    assert (await owner.post("/api/gigs/playlists/check", headers=CSRF)).status_code == 204
    await make_gig_playlists_now.func(user_id=user_id)

    made = (await owner.get("/api/gigs/playlists")).json()["items"]
    assert [(p["kind"], p["name"], p["songs"]) for p in made] == [
        ("before", f"Before the gig: Radiohead, {gig_playlists.nice_date(TODAY + dt.timedelta(days=10))}", 3),
        ("after", f"Portishead live, Royal Albert Hall, {gig_playlists.nice_date(TODAY - dt.timedelta(days=2))}", 1),
    ]  # fmt: skip
    async with get_sessionmaker()() as db:
        before = await db.get(Playlist, made[0]["playlist_id"])
        after = await db.get(Playlist, made[1]["playlist_id"])
        gig = await db.scalar(select(Gig))
    assert before is not None and after is not None and gig is not None
    # In the usual order, the library's songs only (Creep and Exit Music aren't in it).
    assert (before.definition["tracks"], before.definition["order"]) == ([303, 300, 301], "listed")
    assert before.status == "queued" and "refreshed weekly" in (before.description or "")
    # The setlist songs it couldn't find, for the playlist's card.
    assert before.display["setlist"] == {
        "songs": 5,
        "missing": [
            {"artist": "Radiohead", "title": "Creep"},
            {"artist": "Radiohead", "title": "Exit Music"},
        ],
        "missing_count": 2,
    }
    # What was played, without the tape; the setlist is kept on the gig.
    assert after.definition["tracks"] == [304]
    assert after.display["setlist"]["missing"] == [{"artist": "Portishead", "title": "Roads"}]
    assert [s["artist"] for s in gig.setlists] == ["Portishead"]
    # The preview keeps the setlist's order.
    preview = await owner.post(
        f"/api/servers/{sid}/playlists/preview",
        json={"tracks": [303, 300, 301], "order": "listed"},
        headers=CSRF,
    )
    assert [s["rating_key"] for s in preview.json()["songs"]] == [303, 300, 301]

    # Again: nothing new to ask setlist.fm for (refreshed weekly until the gig).
    asked.clear()
    await make_gig_playlists_now.func(user_id=user_id)
    assert asked == []
    async with get_sessionmaker()() as db:
        await db.execute(
            update(GigPlaylist)
            .where(GigPlaylist.kind == "before")
            .values(refreshed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=8))
        )
        await db.commit()
    await make_gig_playlists_now.func(user_id=user_id)
    assert len(asked) == 1  # Radiohead's recent setlists, once
    async with get_sessionmaker()() as db:
        assert len(list(await db.scalars(select(Playlist)))) == 2  # updated, not a new one

    # Deleted: not made again.
    deleted = await owner.delete(
        f"/api/servers/{sid}/playlists/{made[0]['playlist_id']}", headers=CSRF
    )
    assert deleted.status_code in (200, 204), deleted.text
    async with get_sessionmaker()() as db:
        await db.execute(
            update(GigPlaylist).values(refreshed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=8))
        )
        await db.commit()
    asked.clear()
    await make_gig_playlists_now.func(user_id=user_id)
    assert asked == []
    items = (await owner.get("/api/gigs/playlists")).json()["items"]
    assert (items[0]["kind"], items[0]["playlist_id"]) == ("before", None)

    # Asked for from the gig's row: made again (deleted or not), straight away.
    [show] = (await owner.get("/api/live/shows")).json()["shows"]
    assert show["playlist_id"] is None
    again = await owner.post(f"/api/live/shows/{show['id']}/playlist", headers=CSRF)
    assert again.status_code == 200, again.text
    made_again = again.json()
    assert (made_again["songs"], made_again["missing"]) == (3, 2)
    assert made_again["playlist_id"] is not None
    [show] = (await owner.get("/api/live/shows")).json()["shows"]
    assert show["playlist_id"] == made_again["playlist_id"]
    # Rebuilt: the same playlist.
    rebuilt = (await owner.post(f"/api/live/shows/{show['id']}/playlist", headers=CSRF)).json()
    assert rebuilt["playlist_id"] == made_again["playlist_id"]
    assert (await owner.post("/api/live/shows/999999/playlist", headers=CSRF)).status_code == 404


def test_when_old_gig_playlists_go() -> None:
    assert deleted_on(dt.date(2026, 11, 15), 10, "days") == dt.date(2026, 11, 25)
    assert deleted_on(dt.date(2026, 11, 15), 2, "weeks") == dt.date(2026, 11, 29)
    assert deleted_on(dt.date(2026, 11, 15), 3, "months") == dt.date(2027, 2, 15)
    assert deleted_on(dt.date(2026, 1, 31), 1, "months") == dt.date(2026, 2, 28)
    assert deleted_on(dt.date(2027, 12, 31), 2, "months") == dt.date(2028, 2, 29)
    on = User(preferences={"gig_playlists": {"delete_after": 2, "delete_unit": "months"}})
    assert delete_after(on) == (2, "months")
    assert delete_after(User(preferences={})) == (0, "weeks")
    odd = User(preferences={"gig_playlists": {"delete_after": True, "delete_unit": "years"}})
    assert delete_after(odd) == (0, "weeks")


@pytest.mark.db
async def test_deleting_old_gig_playlists(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plekstender.worker import delete_old_gig_playlists_periodically, make_gig_playlists_now

    monkeypatch.setattr(gig_playlists, "SETLISTFM_INTERVAL", 0.0)
    asked: list[str] = []

    def search(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/search/artists"):  # not just checking the key
            asked.append(str(request.url))
        return httpx.Response(200, json={"artist": [], "total": 0, "setlist": []})

    plex.router.route(host="api.setlist.fm").mock(side_effect=search)
    owner, sid = await owner_with_library(app, plex)
    await owner.put("/api/admin/setlistfm", json={"api_key": "k" * 36}, headers=CSRF)
    now = dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        us = await db.scalar(select(UserServer).where(UserServer.user_id == user_id))
        assert user_id is not None and us is not None
        ids = {}
        for name, days_ago in (("old", 20), ("recent", 5), ("mine", None)):
            playlist = Playlist(
                user_server_id=us.id,
                schedule="manual",
                name=name,
                definition=Definition(tracks=[300], order="listed", limit=None).model_dump(),
                status="queued",
            )
            db.add(playlist)
            await db.flush()
            ids[name] = playlist.id
            if days_ago is not None:
                db.add(
                    GigPlaylist(
                        user_id=user_id, kind="after", ref=name, playlist_id=playlist.id,
                        gig_date=TODAY - dt.timedelta(days=days_ago), songs=1,
                        made_at=now, refreshed_at=now,
                    )
                )  # fmt: skip
        # A gig a few days ago, its playlist not made yet.
        db.add(
            Gig(
                user_id=user_id, date=TODAY - dt.timedelta(days=4), venue="Royal Albert Hall",
                city="London", artists=[{"name": "Portishead", "server_id": sid, "rating_key": 101}],
            )
        )  # fmt: skip
        await db.commit()
    built = await run_queued_builds()
    assert all(p.plex_playlist_key for p in built)
    in_plex = {p.name: p.plex_playlist_key for p in built}
    assert all(k in plex.library.playlists for k in in_plex.values())

    # Kept (the default): nothing goes.
    await delete_old_gig_playlists_periodically.func(timestamp=0)
    async with get_sessionmaker()() as db:
        assert len(list(await db.scalars(select(Playlist)))) == 3

    prefs = {
        "gig_playlists": {"before": False, "after": True, "delete_after": 2, "delete_unit": "weeks"}
    }
    resp = await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    assert resp.status_code == 200, resp.text
    assert resp.json()["preferences"]["gig_playlists"] == prefs["gig_playlists"]
    await delete_old_gig_playlists_periodically.func(timestamp=0)

    # The one from 20 days ago went, in Plex and here; its record stays, so it isn't made
    # again. The recent one and the user's own playlist stay.
    async with get_sessionmaker()() as db:
        left = {p.name for p in await db.scalars(select(Playlist))}
        records = {r.ref: r.playlist_id for r in await db.scalars(select(GigPlaylist))}
    assert left == {"recent", "mine"}
    assert records == {"old": None, "recent": ids["recent"]}
    assert in_plex["old"] not in plex.library.playlists
    assert in_plex["recent"] in plex.library.playlists and in_plex["mine"] in plex.library.playlists
    items = (await owner.get("/api/gigs/playlists")).json()["items"]
    assert [(p["gig_date"], p["playlist_id"]) for p in items] == [
        ((TODAY - dt.timedelta(days=5)).isoformat(), ids["recent"]),
        ((TODAY - dt.timedelta(days=20)).isoformat(), None),
    ]

    # A gig older than the setting isn't looked up for a playlist only to delete it.
    prefs["gig_playlists"].update(delete_after=3, delete_unit="days")
    await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    await make_gig_playlists_now.func(user_id=user_id)
    assert asked == []
    prefs["gig_playlists"].update(delete_after=1, delete_unit="weeks")
    await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    await make_gig_playlists_now.func(user_id=user_id)
    assert len(asked) == 1  # Portishead, that day
