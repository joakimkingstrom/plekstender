"""Library → Stats: growth, decades, words."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender.db import get_sessionmaker
from plekstender.db.models import Album
from plekstender.library_stats import words
from tests.fakes import FakePlex
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_growth_and_decades(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/library/stats"
    # Dummy was added two months before the rest.
    async with get_sessionmaker()() as db:
        ok_computer = await db.scalar(select(Album).where(Album.rating_key == 200))
        assert ok_computer is not None and ok_computer.added_at is not None
        early = ok_computer.added_at - dt.timedelta(days=62)
        await db.execute(update(Album).where(Album.rating_key == 202).values(added_at=early))
        await db.commit()

    stats = (await owner.get(base)).json()
    assert (stats["artists"], stats["albums"], stats["tracks"]) == (3, 4, 6)
    growth = stats["growth"]
    assert len(growth) == 3  # every month from the first to the last, gaps included
    assert growth[0]["albums"] == 1 and growth[1]["albums"] == 1
    assert growth[-1] == growth[-1] | {"artists": 3, "albums": 4, "tracks": 6}
    assert [(d["decade"], d["albums"]) for d in stats["decades"]] == [
        (1990, 2),
        (2010, 1),
        (2020, 1),
    ]

    nineties = (await owner.get(f"{base}/released", params={"decade": 1990})).json()
    assert [(b["decade"], b["albums"]) for b in nineties["buckets"] if b["albums"]] == [
        (1994, 1),
        (1997, 1),
    ]
    # OK Computer has a full date (May); Dummy only a year in the fake (month unknown).
    year = (await owner.get(f"{base}/released", params={"year": 1997})).json()
    assert [(b["decade"], b["albums"]) for b in year["buckets"] if b["albums"]] == [(5, 1)]
    assert [(a["title"], a["artist_title"]) for a in year["albums"]] == [
        ("OK Computer", "Radiohead")
    ]
    dummy = (await owner.get(f"{base}/released", params={"year": 1994})).json()
    assert [(b["decade"], b["albums"]) for b in dummy["buckets"] if b["albums"]] == [(0, 1)]


async def test_words_in_names(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    found = (
        await owner.get(f"/api/servers/{sid}/library/stats/words", params={"kind": "track"})
    ).json()
    # Karma Police and its remaster count once; "(Remastered)" is left out.
    counts = {w["word"]: w["count"] for w in found}
    assert counts["Karma"] == 1 and counts["Police"] == 1
    assert "Remastered" not in counts
    albums = (
        await owner.get(f"/api/servers/{sid}/library/stats/words", params={"kind": "album"})
    ).json()
    assert {"word": "Computer", "count": 2} in albums


def test_word_counting() -> None:
    names = [
        "The Wind Cries Mary",
        "Mary Had a Little Lamb (2011 Remaster)",
        "Don\u2019t Stop Me Now",
        "don't stop believin'",
        "Mary, Mary [feat. Someone]",
        "Det är vi som är här",
    ]
    counted = dict(words(names))
    assert counted["Mary"] == 3  # once per name
    assert counted["Stop"] == 2 and "Don't" not in counted  # a stop word, whichever apostrophe
    assert "Remaster" not in counted and "Someone" not in counted and "feat" not in counted
    assert "är" not in counted and "the" not in {k.lower() for k in counted}
