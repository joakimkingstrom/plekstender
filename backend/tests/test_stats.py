"""Listening stats: periods, totals, top lists linked to the library, streaks, activity."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from sqlalchemy import update

from plekstender.db import get_sessionmaker
from plekstender.db.models import Listen
from plekstender.history.lastfm import Scrobble
from plekstender.history.stats import StatsError, resolve_period, streaks
from plekstender.history.store import insert_listens, scrobble_row
from tests.fakes import FakePlex, sign_in
from tests.test_sync import connect, owner_with_library

LONDON = ZoneInfo("Europe/London")


def test_periods_follow_the_users_time_zone() -> None:
    now = dt.datetime(2026, 11, 18, 23, 30, tzinfo=dt.UTC)  # 23:30 in London too
    week = resolve_period("7d", LONDON, now)
    assert week.start == dt.datetime(2026, 11, 12, tzinfo=LONDON)
    assert week.previous_start == dt.datetime(2026, 11, 5, tzinfo=LONDON)
    assert week.bucket == "day"
    sydney = resolve_period("7d", ZoneInfo("Australia/Sydney"), now)
    assert sydney.start == dt.datetime(2026, 11, 13, tzinfo=ZoneInfo("Australia/Sydney"))
    year = resolve_period("y2024", LONDON, now)
    assert (year.start, year.end, year.bucket) == (
        dt.datetime(2024, 1, 1, tzinfo=LONDON),
        dt.datetime(2025, 1, 1, tzinfo=LONDON),
        "week",
    )
    assert resolve_period("y2026", LONDON, now).end == now
    everything = resolve_period("all", LONDON, now)
    assert (everything.start, everything.previous_start, everything.bucket) == (None, None, "month")
    with pytest.raises(StatsError):
        resolve_period("y2031", LONDON, now)


def test_streaks() -> None:
    d = dt.date(2026, 11, 18)
    days = [d - dt.timedelta(days=k) for k in (0, 1, 2, 10, 11, 12, 13, 14)]
    assert streaks(days, d) == (3, 5)
    assert streaks(days, d + dt.timedelta(days=1)) == (3, 5)  # nothing played yet today
    assert streaks(days, d + dt.timedelta(days=2)) == (0, 5)
    assert streaks([], d) == (0, 0)


async def add_listens(user_id: int, plays: list[tuple[int, str, str, str]]) -> None:
    """(days ago, artist, album, track) listens, each a minute before now minus those days."""
    now = dt.datetime.now(dt.UTC)
    rows = [
        scrobble_row(
            user_id,
            None,
            "lastfm",
            Scrobble(
                listened_at=now - dt.timedelta(days=days, minutes=1 + i),
                artist=artist,
                album=album,
                track=track,
                artist_mbid=None,
                release_mbid=None,
                recording_mbid=None,
            ),
        )
        for i, (days, artist, album, track) in enumerate(plays)
    ]
    async with get_sessionmaker()() as db:
        await insert_listens(db, rows)
        await db.commit()


@pytest.mark.db
async def test_overview_and_activity(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await add_listens(
        user_id,
        [
            (0, "Radiohead", "OK Computer", "Paranoid Android"),
            (1, "Radiohead", "OK Computer", "Paranoid Android"),
            (2, "radiohead", "OK Computer", "Paranoid Android (Remastered)"),
            (2, "Portishead", "Dummy", "Glory Box"),
            (3, "Not In Library", "Demo", "Some Song"),
            (3, "Not In Library", "Demo", "Counted Twice"),
            (10, "Portishead", "Dummy", "Glory Box"),
            (10, "Portishead", "Dummy", "Sour Times"),
            (11, "Portishead", "Dummy", "Glory Box"),
        ],
    )
    async with get_sessionmaker()() as db:  # a duplicate of another source's play
        await db.execute(
            update(Listen)
            .where(Listen.track_name == "Counted Twice")
            .values(duplicate_of=Listen.id)
        )
        await db.commit()

    params = {"range": "7d", "tz": "UTC", "server_id": sid}
    week = (await owner.get("/api/stats/overview", params=params)).json()
    assert week["totals"] == {"listens": 5, "listening_ms": 5 * 215_000, "artists": 3, "tracks": 3}
    assert week["previous"]["listens"] == 3
    assert week["new_artists"] == 2  # Radiohead and Not In Library
    assert [
        (a["name"], a["listens"], a["previous_rank"], a["rating_key"]) for a in week["top_artists"]
    ] == [
        ("Radiohead", 3, None, 100),
        ("Not In Library", 1, None, None),
        ("Portishead", 1, 1, 101),
    ]
    assert week["top_artists"][0]["thumb"].startswith("/library/metadata/100/thumb")
    album = week["top_albums"][0]
    assert (album["name"], album["artist"], album["rating_key"]) == (
        "OK Computer",
        "Radiohead",
        200,
    )
    top_track = week["top_tracks"][0]
    assert (top_track["name"], top_track["listens"], top_track["rating_key"]) == (
        "Paranoid Android",
        3,  # the remaster counts as the same song
        200,
    )
    assert (week["current_streak"], week["longest_streak"]) == (4, 4)

    everything = (await owner.get("/api/stats/overview", params={"range": "all"})).json()
    assert everything["totals"]["listens"] == 8
    assert everything["previous"] is None
    assert everything["top_artists"][0]["rating_key"] is None  # no server_id: no links

    act = (await owner.get("/api/stats/activity", params={"range": "7d", "tz": "UTC"})).json()
    assert act["bucket"] == "day"
    assert len(act["points"]) == 7
    assert sum(p["listens"] for p in act["points"]) == 5
    assert act["points"][-1]["start"] == dt.datetime.now(dt.UTC).date().isoformat()
    assert sum(map(sum, act["heatmap"])) == 5
    assert len(act["heatmap"]) == 7 and all(len(row) == 24 for row in act["heatmap"])
    months = (await owner.get("/api/stats/activity", params={"range": "all"})).json()
    assert months["bucket"] == "month"
    assert sum(p["listens"] for p in months["points"]) == 8

    bad_tz = await owner.get("/api/stats/overview", params={"range": "7d", "tz": "Mars/Base"})
    assert bad_tz.status_code == 422
    assert (await owner.get("/api/stats/overview", params={"range": "5y"})).status_code == 422


@pytest.mark.db
async def test_stats_are_private(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await add_listens(user_id, [(0, "Radiohead", "OK Computer", "Airbag")])
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    mine = (await friend.get("/api/stats/overview", params={"range": "all"})).json()
    assert mine["totals"]["listens"] == 0
    assert mine["top_artists"] == []
    # The owner's server id doesn't belong to the friend.
    assert (
        await friend.get("/api/stats/overview", params={"range": "all", "server_id": sid})
    ).status_code == 404


@pytest.mark.db
async def test_your_listens_on_artist_and_album_pages(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await add_listens(
        user_id,
        [
            (0, "Radiohead", "OK Computer", "Paranoid Android"),
            (1, "Radiohead", "OK Computer", "Paranoid Android"),
            (2, "Radiohead", "OK Computer", "Karma Police"),
            (3, "Radiohead", "OK Computer OKNOTOK 1997 2017", "Karma Police (Remastered)"),
            (800, "Radiohead", "OK Computer", "Airbag"),
            (0, "Portishead", "Dummy", "Glory Box"),
        ],
    )

    artist = (
        await owner.get(f"/api/servers/{sid}/library/artists/100", params={"tz": "UTC"})
    ).json()
    mine = artist["your_listens"]
    assert mine["listens"] == 5
    assert [(s["name"], s["listens"]) for s in mine["songs"]] == [
        ("Karma Police", 2),
        ("Paranoid Android", 2),
        ("Airbag", 1),
    ]
    this_year = dt.datetime.now(dt.UTC).year
    assert mine["years"][-1] == {"year": this_year, "listens": 4}
    assert sum(y["listens"] for y in mine["years"]) == 5
    first_year = mine["years"][0]["year"]
    assert [y["year"] for y in mine["years"]] == list(range(first_year, this_year + 1))  # no gaps
    # Remasters are one song: "Karma Police" counts both releases' listens.
    assert {t["title"]: t["listens"] for t in artist["top_tracks"]} == {
        "Karma Police": 2,
        "Paranoid Android": 2,
        "Lucky": 0,
    }

    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert album["your_listens"]["listens"] == 4  # not the OKNOTOK play
    assert {t["title"]: t["listens"] for t in album["tracks"]} == {
        "Paranoid Android": 2,
        "Karma Police": 1,
    }

    nothing = (await owner.get(f"/api/servers/{sid}/library/albums/203")).json()["your_listens"]
    assert nothing == {"listens": 0, "first_at": None, "last_at": None, "years": [], "songs": []}
