"""Rewind: a look back at a period."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from plekstender.db import get_sessionmaker
from plekstender.db.models import AlbumColour, PlexServer, Tag
from tests.fakes import CSRF, FakePlex
from tests.test_stats import add_listens
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_rewind(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    # Listed newest first within a day (each a minute before the one above).
    await add_listens(
        user_id,
        [
            *[(0, "Radiohead", "OK Computer", "Paranoid Android")] * 10,
            (1, "Radiohead", "OK Computer", "Karma Police"),
            (1, "Radiohead", "OK Computer", "Karma Police"),
            (1, "Portishead", "Dummy", "Glory Box"),
            (2, "Radiohead", "OK Computer", "Lucky"),
            (20, "Portishead", "Dummy", "Glory Box"),  # before the period
        ],
    )
    async with get_sessionmaker()() as db:
        server = await db.scalar(select(PlexServer))
        assert server is not None
        db.add(
            AlbumColour(
                server_id=server.id,
                rating_key=200,
                thumb="/library/metadata/200/thumb/1700000000",
                hex="#2850c8",
                hue=225,
                saturation=0.6,
                lightness=0.47,
                checked_at=dt.datetime.now(dt.UTC),
            )
        )
        await db.commit()
    for _ in range(2):
        resp = await owner.post("/api/app-time", json={"seconds": 60}, headers=CSRF)
        assert resp.status_code == 204

    today = dt.datetime.now(dt.UTC).date()
    params = {
        "start": str(today - dt.timedelta(days=6)),
        "end": str(today),
        "tz": "UTC",
        "server_id": sid,
    }
    r = (await owner.get("/api/rewind", params=params)).json()
    assert (r["listens"], r["days"], r["period_days"], r["artists"]) == (14, 3, 7, 2)
    assert [(a["name"], a["listens"], a["rating_key"]) for a in r["top_artists"]] == [
        ("Radiohead", 13, 100),
        ("Portishead", 1, 101),
    ]
    ok = r["top_albums"][0]
    assert (ok["name"], ok["rating_key"], ok["colour"]) == ("OK Computer", 200, "#2850c8")
    assert r["top_tracks"][0]["name"] == "Paranoid Android"
    # Radiohead was first heard this week, 13 times; Portishead earlier.
    assert [a["name"] for a in r["new_artists"]] == ["Radiohead"]
    assert [a["name"] for a in r["new_albums"]] == ["OK Computer"]
    assert r["new_releases"] == []  # OK Computer is from 1997
    assert sum(r["weekdays"]) == 14 and sum(r["hours"]) == 14
    # Your sound is styles: Radiohead is Art Rock (Rock and Alternative as genres);
    # Portishead has no style, so isn't counted.
    assert [(g["genre"], g["listens"]) for g in r["genres"]] == [("Art Rock", 13)]
    assert r["genre_days"] and r["genre_parts"]
    assert [(s["name"], s["runs"], s["longest"], s["shortest"]) for s in r["repeats"]] == [
        ("Paranoid Android", 1, 10, 10),
        ("Karma Police", 1, 2, 2),
    ]
    assert r["longest_repeat"]["longest"] == 10
    assert r["game"] is None
    assert r["app_seconds"] == 120

    # A library without styles: genres instead (an artist's listens split over them).
    async with get_sessionmaker()() as db:
        await db.execute(delete(Tag).where(Tag.kind == "style"))
        await db.commit()
    r = (await owner.get("/api/rewind", params=params)).json()
    genres = {g["genre"]: g["listens"] for g in r["genres"]}
    assert genres["Rock"] == genres["Alternative"] == round(13 / 2) and genres["Trip Hop"] == 1

    bad = {**params, "start": str(today), "end": str(today - dt.timedelta(days=1))}
    assert (await owner.get("/api/rewind", params=bad)).status_code == 422
    assert (
        await owner.post("/api/app-time", json={"seconds": 500}, headers=CSRF)
    ).status_code == 422
