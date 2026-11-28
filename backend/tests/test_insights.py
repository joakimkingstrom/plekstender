"""Looking back: on this day, taste over time, rediscover, and the forgotten favourites
playlist source."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender.db import get_sessionmaker
from plekstender.db.models import Tag, UserItemState
from plekstender.history.lastfm import Scrobble
from plekstender.history.listen_counts import refresh_listen_counts
from plekstender.history.store import insert_listens, scrobble_row
from tests.fakes import CSRF, FakePlex
from tests.test_playlists import keys, preview
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


def years_ago(years: int, hour: int = 12) -> dt.datetime:
    """Noon UTC on today's date, `years` years ago."""
    today = dt.datetime.now(dt.UTC).date()
    return dt.datetime(today.year - years, today.month, today.day, hour, tzinfo=dt.UTC)


async def add(user_id: int, plays: list[tuple[dt.datetime, str, str, str]]) -> None:
    rows = [
        scrobble_row(
            user_id,
            None,
            "lastfm",
            Scrobble(
                listened_at=when + dt.timedelta(seconds=i),
                artist=artist,
                album=album,
                track=track,
                artist_mbid=None,
                release_mbid=None,
                recording_mbid=None,
            ),
        )
        for i, (when, artist, album, track) in enumerate(plays)
    ]
    async with get_sessionmaker()() as db:
        await insert_listens(db, rows)
        await refresh_listen_counts(db, user_id)
        await db.commit()


async def set_state(user_id: int, rating_key: int, **values: object) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            update(UserItemState)
            .where(UserItemState.user_id == user_id, UserItemState.rating_key == rating_key)
            .values(**values)
        )
        await db.commit()


@pytest.fixture(autouse=True)
def not_a_leap_day() -> None:
    today = dt.datetime.now(dt.UTC).date()
    if (today.month, today.day) == (2, 29):
        pytest.skip("29 February has no date in most earlier years")


async def test_on_this_day_and_taste(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    await add(
        user_id,
        [
            (years_ago(1), "Radiohead", "OK Computer", "Paranoid Android"),
            (years_ago(1), "radiohead", "OK Computer", "Paranoid Android"),
            (years_ago(1), "Radiohead", "OK Computer", "Karma Police"),
            (years_ago(1), "Not In Library", "Demo", "Some Song"),
            (years_ago(3), "Portishead", "Dummy", "Glory Box"),
            (years_ago(3) - dt.timedelta(days=1), "Portishead", "Dummy", "Glory Box"),
            # This year's plays aren't "earlier years".
            (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5), "Portishead", "Dummy", "Glory Box"),
        ],
    )

    day = (await owner.get("/api/stats/on-this-day", params={"tz": "UTC", "server_id": sid})).json()
    assert [(y["year"], y["listens"]) for y in day["years"]] == [
        (years_ago(1).year, 4),
        (years_ago(3).year, 1),  # the day before doesn't count
    ]
    last_year = day["years"][0]
    assert [(a["name"], a["listens"], a["rating_key"]) for a in last_year["artists"]] == [
        ("Radiohead", 3, 100),
        ("Not In Library", 1, None),
    ]
    assert [(t["name"], t["artist"], t["listens"]) for t in last_year["tracks"]] == [
        ("Paranoid Android", "Radiohead", 2),
        ("Some Song", "Not In Library", 1),
        ("Karma Police", "Radiohead", 1),
    ]
    assert (last_year["album"], last_year["album_rating_key"]) == ("OK Computer", 200)
    # Without a server: names only.
    plain = (await owner.get("/api/stats/on-this-day", params={"tz": "UTC"})).json()
    assert plain["years"][0]["artists"][0]["rating_key"] is None
    bad = await owner.get("/api/stats/on-this-day", params={"tz": "Nowhere/Special"})
    assert bad.status_code == 422

    taste = (await owner.get("/api/stats/taste", params={"server_id": sid, "tz": "UTC"})).json()
    this_year = dt.datetime.now(dt.UTC).year
    assert taste["years"] == [years_ago(3).year, years_ago(1).year, this_year]
    assert taste["listens"] == [2, 4, 1]
    genres = {g["name"]: g["shares"] for g in taste["genres"]}
    # Radiohead is Rock and Alternative: each of its listens counts half to both.
    assert genres == {
        "Trip Hop": [1.0, 0.0, 1.0],
        "Rock": [0.0, 0.5, 0.0],
        "Alternative": [0.0, 0.5, 0.0],
    }
    assert taste["genre_matched"] == [2, 3, 1]  # not the artist that isn't in the library
    # Styles or moods instead: only Radiohead has them (Art Rock, Melancholy).
    for kind, name in (("style", "Art Rock"), ("mood", "Melancholy")):
        tags = (
            await owner.get(
                "/api/stats/taste", params={"server_id": sid, "tz": "UTC", "kind": kind}
            )
        ).json()
        assert [(g["name"], g["shares"]) for g in tags["genres"]] == [(name, [0.0, 1.0, 0.0])]
        assert tags["genre_matched"] == [0, 3, 0]
        assert tags["decades"] == taste["decades"]  # the same either way
    bad = await owner.get("/api/stats/taste", params={"server_id": sid, "kind": "colour"})
    assert bad.status_code == 422
    # Karma Police is on OK Computer (1997) and its 2017 reissue: the earliest counts.
    assert [(d["name"], d["shares"]) for d in taste["decades"]] == [("1990s", [1.0, 1.0, 1.0])]
    assert taste["decade_matched"] == [2, 3, 1]
    assert taste["median_release"] == [1994, 1997, 1994]


async def test_rediscover_and_forgotten_playlist(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    # Glory Box: loved, last played two years ago (imported plays).
    await add(
        user_id,
        [
            (years_ago(2), "Portishead", "Dummy", "Glory Box"),
            (years_ago(2, hour=13), "Portishead", "Dummy", "Glory Box"),
        ],
    )
    await set_state(user_id, 304, user_rating=10.0)
    # Lucky: loved, never played.
    await set_state(user_id, 303, user_rating=9.0)
    # Karma Police: rated low, but played 6 times in Plex (the remaster shares the song).
    await set_state(user_id, 301, user_rating=2.0, view_count=6)
    # Paranoid Android: loved and played recently (in the fake library) - nothing to say.

    found = (await owner.get("/api/stats/rediscover", params={"server_id": sid})).json()
    forgotten = found["forgotten"]
    assert forgotten["total"] == 1
    [song] = forgotten["items"]
    assert (song["title"], song["artist"], song["plays"], song["rating"]) == (
        "Glory Box",
        "Portishead",
        2,
        10.0,
    )
    assert song["last_played_at"].startswith(years_ago(2).date().isoformat())
    assert [s["title"] for s in found["unplayed"]["items"]] == ["Lucky"]
    disliked = found["disliked"]["items"]
    assert [(s["title"], s["plays"], s["rating"]) for s in disliked] == [("Karma Police", 6, 2.0)]
    assert (found["loved"], found["forgotten_days"], found["played_a_lot"]) == (9, 365, 5)

    # The playlist source picks the same songs, and the rules still apply.
    assert keys(await preview(owner, sid, forgotten=True)) == [304]
    with_radiohead = await preview(owner, sid, forgotten=True, artists=[100], order="popular")
    assert keys(with_radiohead) == [301, 300, 304, 303]  # by popularity
    async with get_sessionmaker()() as db:
        rock = await db.scalar(select(Tag.id).where(Tag.kind == "genre", Tag.name == "Rock"))
    assert keys(await preview(owner, sid, forgotten=True, rules={"tags": [rock]})) == []

    # Hidden songs leave Rediscover and the playlist, and can be shown again.
    hidden = f"/api/stats/rediscover/hidden/{{}}?server_id={sid}"
    assert (await owner.put(hidden.format(304), headers=CSRF)).status_code == 204
    assert (await owner.put(hidden.format(304), headers=CSRF)).status_code == 204  # still hidden
    after = (await owner.get("/api/stats/rediscover", params={"server_id": sid})).json()
    assert after["forgotten"] == {"total": 0, "items": []}
    assert keys(await preview(owner, sid, forgotten=True)) == []
    listed = (
        await owner.get("/api/stats/rediscover", params={"server_id": sid, "include_hidden": True})
    ).json()
    assert [(s["title"], s["hidden"]) for s in listed["forgotten"]["items"]] == [
        ("Glory Box", True)
    ]
    # Hiding any release hides the song; showing any release shows it again.
    await owner.put(hidden.format(302), headers=CSRF)  # Karma Police (Remastered)
    after = (await owner.get("/api/stats/rediscover", params={"server_id": sid})).json()
    assert after["disliked"]["items"] == []
    assert (await owner.delete(hidden.format(301), headers=CSRF)).status_code == 204
    await owner.delete(hidden.format(304), headers=CSRF)
    back = (await owner.get("/api/stats/rediscover", params={"server_id": sid})).json()
    assert [s["title"] for s in back["disliked"]["items"]] == ["Karma Police"]
    assert [s["title"] for s in back["forgotten"]["items"]] == ["Glory Box"]
    other = await owner.put(f"/api/stats/rediscover/hidden/304?server_id={sid + 9}", headers=CSRF)
    assert other.status_code == 404


async def test_gigs_on_this_day(app: FastAPI, plex: FakePlex) -> None:
    """Gigs you went to on today's date in earlier years, with or without plays that day."""
    from plekstender.db import get_sessionmaker
    from plekstender.db.models import Gig, User
    from plekstender.history.insights import gigs_on_this_day

    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    today = dt.datetime.now(dt.UTC).date()

    def back(years: int, days: int = 0) -> dt.date:
        try:
            day = today.replace(year=today.year - years)
        except ValueError:  # 29 February
            day = today.replace(year=today.year - years, day=28)
        return day - dt.timedelta(days=days)

    setlist = {
        "artist": "Radiohead",
        "url": "https://www.setlist.fm/x.html",
        "songs": [{"name": "Lucky"}],
    }
    async with get_sessionmaker()() as db:
        on_the_day = Gig(
            user_id=user_id, date=back(5), venue="The O2", city="London", setlists=[setlist],
            artists=[{"name": "Radiohead"}, {"name": "Caribou"}],
        )  # fmt: skip
        another_day = Gig(user_id=user_id, date=back(5, days=3), artists=[{"name": "Muse"}])
        this_year = Gig(user_id=user_id, date=today, artists=[{"name": "Portishead"}])
        db.add_all([on_the_day, another_day, this_year])
        await db.commit()

    found = (await owner.get("/api/stats/on-this-day", params={"server_id": sid})).json()
    years = {y["year"]: y for y in found["years"]}
    assert today.year not in years  # this year's doesn't count
    five = years[today.year - 5]
    assert five["listens"] == 0  # a year with only a gig is listed too
    assert five["gigs"] == [
        {
            "id": five["gigs"][0]["id"],
            "artists": ["Radiohead", "Caribou"],
            "venue": "The O2",
            "city": "London",
            "setlist_url": "https://www.setlist.fm/x.html",
        }
    ]

    # 29 February is remembered on 28 February in years without one.
    async with get_sessionmaker()() as db:
        user = await db.get(User, user_id)
        assert user is not None
        db.add(Gig(user_id=user_id, date=dt.date(2024, 2, 29), venue="Leap", artists=[]))
        await db.commit()
        assert [
            g.venue for g in (await gigs_on_this_day(db, user_id, dt.date(2027, 2, 28)))[2024]
        ] == ["Leap"]
        assert 2024 not in await gigs_on_this_day(db, user_id, dt.date(2028, 2, 28))  # a leap year
