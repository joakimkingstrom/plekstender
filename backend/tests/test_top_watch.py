"""Notifications when the all-time top 10s change (artists, albums, songs): entering one,
a new #1."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import update

from plekstender.db import get_sessionmaker
from plekstender.db.models import Notification
from plekstender.history import top_watch
from plekstender.history.lastfm import Scrobble
from plekstender.history.store import insert_listens, scrobble_row
from tests.fakes import FakePlex, sign_in

pytestmark = pytest.mark.db

START = 1_700_000_000
_next = 0


async def plays(user_id: int, artist: str, n: int) -> None:
    """n listens of the artist's one song ("Hit", on "Debut"): the albums' and songs' top 10s
    follow the artists'."""
    global _next
    rows = [
        scrobble_row(
            user_id,
            None,
            "lastfm",
            Scrobble(
                listened_at=dt.datetime.fromtimestamp(START + (_next + i) * 600, dt.UTC),
                artist=artist,
                album="Debut",
                track="Hit",
                artist_mbid=None,
                release_mbid=None,
                recording_mbid=None,
            ),
        )
        for i in range(n)
    ]
    async with get_sessionmaker()() as db:
        await insert_listens(db, rows)
        await db.commit()
    _next += n


async def check() -> int:
    async with get_sessionmaker()() as db:
        return await top_watch.check(db, dt.datetime.now(dt.UTC))


async def test_top_10_news(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    user_id = (await owner.get("/api/auth/me")).json()["id"]

    async def news() -> list[tuple[str, str | None, bool]]:
        items = (await owner.get("/api/notifications")).json()["items"]
        return [(i["title"], i["detail"], i["read"]) for i in items if i["level"] == "info"]

    # Ten artists (30 listens down to 12, no ties), and one further down.
    for i in range(10):
        await plays(user_id, f"Artist {i + 1:02}", 30 - 2 * i)
    await plays(user_id, "Zola Jesus", 5)
    assert await check() == 0  # the first look only notes the top 10
    assert await news() == []

    # Zola Jesus climbs to 23 listens: #5.
    await plays(user_id, "Zola Jesus", 18)
    assert await check() == 3
    assert sorted(await news()) == [
        ("Debut by Zola Jesus is in your top 10 albums", "#5 of all time, with 23 listens.", False),
        ("Hit by Zola Jesus is in your top 10 songs", "#5 of all time, with 23 listens.", False),
        ("Zola Jesus is in your top 10 artists", "#5 of all time, with 23 listens.", False),
    ]
    assert await check() == 0  # no newer listens: nothing to do

    # Artist 02 overtakes Artist 01.
    await plays(user_id, "Artist 02", 5)
    assert await check() == 3
    assert (
        "Artist 02 is your new #1 artist",
        "Taking over from Artist 01, with 33 listens of all time.",
        False,
    ) in await news()
    assert (
        "Hit by Artist 02 is your new #1 song",
        "Taking over from Hit by Artist 01, with 33 listens of all time.",
        False,
    ) in await news()
    assert (
        "Debut by Artist 02 is your new #1 album",
        "Taking over from Debut by Artist 01, with 33 listens of all time.",
        False,
    ) in await news()

    # An import: five at once are one notification.
    for name in ("Beak", "Boris", "Brigid", "Buzzcocks", "Bwani"):
        await plays(user_id, name, 25)
    assert await check() == 3
    assert (
        "5 artists are new in your top 10",
        "Beak, Boris, Brigid, Buzzcocks and Bwani",
        False,
    ) in await news()
    assert (
        "5 songs are new in your top 10",
        "Hit by Beak, Hit by Boris, Hit by Brigid, Hit by Buzzcocks and Hit by Bwani",
        False,
    ) in await news()

    # Two weeks on, they go.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Notification).values(updated_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=15))
        )
        await db.commit()
    await check()
    assert await news() == []
