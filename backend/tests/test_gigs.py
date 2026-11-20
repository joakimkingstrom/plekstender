# ruff: noqa: E501 (hand-aligned test data)
"""Gigs the user went to: by hand, from Live, linked to the library."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from plekstender.db import get_sessionmaker
from plekstender.db.models import LiveShow, User
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs

TODAY = dt.datetime.now(dt.UTC).date()


@pytest.mark.db
async def test_gigs_by_hand(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await owner_with_library(app, plex)
    added = await owner.post(
        "/api/gigs",
        json={
            "date": "2017-07-01",
            "artists": ["Radiohead", "  Support   Act ", "radiohead"],
            "venue": "Glastonbury Festival",
            "city": "Pilton",
            "country": "gb",
            "name": "Glastonbury 2017",
        },
        headers=CSRF,
    )
    assert added.status_code == 201, added.text
    gig = added.json()
    # Linked to the library where the name matches; duplicates and spacing tidied.
    assert [(a["name"], a["rating_key"]) for a in gig["artists"]] == [
        ("Radiohead", 100),
        ("Support Act", None),
    ]
    assert gig["artists"][0]["server_id"] and gig["artists"][1]["server_id"] is None
    assert (gig["country"], gig["source"], gig["place_precision"]) == ("GB", "manual", None)

    future = TODAY + dt.timedelta(days=30)
    refused = await owner.post(
        "/api/gigs", json={"date": future.isoformat(), "artists": ["Radiohead"]}, headers=CSRF
    )
    assert refused.status_code == 422

    await owner.post(
        "/api/gigs",
        json={"date": "2019-10-02", "artists": ["Portishead"], "venue": "Royal Albert Hall"},
        headers=CSRF,
    )
    listed = (await owner.get("/api/gigs")).json()
    assert [g["date"] for g in listed["gigs"]] == ["2019-10-02", "2017-07-01"]  # newest first
    assert listed["years"] == [{"year": 2019, "gigs": 1}, {"year": 2017, "gigs": 1}]
    assert listed["total"] == 2
    assert [
        g["date"] for g in (await owner.get("/api/gigs", params={"year": 2017})).json()["gigs"]
    ] == ["2017-07-01"]
    by_artist = (await owner.get("/api/gigs", params={"artist": "Portishead"})).json()["gigs"]
    assert [g["venue"] for g in by_artist] == ["Royal Albert Hall"]
    assert (await owner.get("/api/gigs/artists")).json() == [
        "Portishead",
        "Radiohead",
        "Support Act",
    ]

    # Edit and delete; someone else can't see or touch them.
    edited = await owner.put(
        f"/api/gigs/{gig['id']}",
        json={
            "date": "2017-06-24",
            "artists": ["Radiohead"],
            "venue": "Pyramid Stage",
            "notes": "Paranoid Android!",
        },
        headers=CSRF,
    )
    assert edited.status_code == 200 and edited.json()["notes"] == "Paranoid Android!"
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()
    assert (await friend.get("/api/gigs")).json()["gigs"] == []
    assert (await friend.delete(f"/api/gigs/{gig['id']}", headers=CSRF)).status_code == 404
    assert (await owner.delete(f"/api/gigs/{gig['id']}", headers=CSRF)).status_code == 204
    assert (await owner.get("/api/gigs")).json()["total"] == 1


@pytest.mark.db
async def test_gigs_from_live(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        now = dt.datetime.now(dt.UTC)
        base = {
            "user_id": user_id, "name": "Radiohead", "festival": False, "status": "scheduled",
            "venue": "The O2", "city": "London", "country": "GB", "fetched_at": now,
            "artists": [{"name": "Radiohead", "headliner": True, "server_id": sid, "rating_key": 100}],
            "performers": [{"name": "Radiohead", "headliner": True}, {"name": "Support Act", "headliner": False}],
        }  # fmt: skip
        db.add_all([
            # Went (starred, now past), and found by two sources: one suggestion.
            LiveShow(**base, source="jambase", source_id="jambase:1", start_date=TODAY - dt.timedelta(days=3),
                     latitude=51.503, longitude=0.003, starred_at=now, ticket_url="https://t/1"),
            LiveShow(**base, source="ticketmaster", source_id="ticketmaster:A", start_date=TODAY - dt.timedelta(days=3),
                     starred_at=now),
            # Not starred, or still to come: not suggested.
            LiveShow(**base, source="jambase", source_id="jambase:2", start_date=TODAY - dt.timedelta(days=5)),
            LiveShow(**base, source="jambase", source_id="jambase:3", start_date=TODAY + dt.timedelta(days=5),
                     starred_at=now),
        ])  # fmt: skip
        await db.commit()

    suggestions = (await owner.get("/api/gigs/suggestions")).json()
    assert [(s["venue"], s["sources"]) for s in suggestions] == [
        ("The O2", ["jambase", "ticketmaster"])
    ]
    recorded = await owner.post(
        "/api/gigs/from-live", json={"ids": suggestions[0]["ids"]}, headers=CSRF
    )
    assert recorded.status_code == 201, recorded.text
    gig = recorded.json()
    assert (gig["source"], gig["venue"], gig["place_precision"], gig["url"]) == (
        "live", "The O2", "venue", "https://t/1",
    )  # fmt: skip
    assert [a["name"] for a in gig["artists"]] == ["Radiohead", "Support Act"]
    assert gig["artists"][0]["rating_key"] == 100
    assert (await owner.get("/api/gigs/suggestions")).json() == []
    again = await owner.post(
        "/api/gigs/from-live", json={"ids": suggestions[0]["ids"]}, headers=CSRF
    )
    assert again.status_code == 404


@pytest.mark.db
async def test_gig_places(app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> None:
    """Venues are looked up once on Nominatim (the town if the venue isn't found)."""
    import httpx

    from plekstender.config import get_settings
    from plekstender.gigs import places

    monkeypatch.setattr(places, "INTERVAL", 0.0)
    asked: list[dict[str, str]] = []

    def nominatim(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        asked.append(params)
        assert request.headers["user-agent"].startswith("Plekstender/")
        if params.get("q") == "Royal Albert Hall, London":
            return httpx.Response(
                200, json=[{"lat": "51.5009", "lon": "-0.1774", "display_name": "RAH"}]
            )
        if params.get("city") == "Pilton":
            return httpx.Response(200, json=[{"lat": "51.16", "lon": "-2.59"}])
        return httpx.Response(200, json=[])

    plex.router.get("https://nominatim.openstreetmap.org/search").mock(side_effect=nominatim)
    owner, _ = await owner_with_library(app, plex)
    for body in (
        {
            "date": "2019-10-02",
            "artists": ["Portishead"],
            "venue": "Royal Albert Hall",
            "city": "London",
            "country": "GB",
        },
        {
            "date": "2021-10-02",
            "artists": ["Radiohead"],
            "venue": "Royal Albert Hall",
            "city": "London",
            "country": "GB",
        },
        {
            "date": "2017-06-24",
            "artists": ["Radiohead"],
            "venue": "Pyramid Stage",
            "city": "Pilton",
            "country": "GB",
        },
        {
            "date": "2010-01-01",
            "artists": ["Radiohead"],
            "venue": "Nowhere Hall",
            "city": "Atlantis",
        },
    ):
        assert (await owner.post("/api/gigs", json=body, headers=CSRF)).status_code == 201
    async with get_sessionmaker()() as db:
        jobs = (await db.execute(text("SELECT args FROM procrastinate_jobs"))).scalars().all()
        assert jobs and all(j.get("user_id") for j in jobs)  # adding a gig queues the lookup
        async with httpx.AsyncClient() as http:
            placed = await places.place_gigs(db, http, get_settings().nominatim_url)
    assert placed == 3
    gigs = {(g["venue"], g["date"]): g for g in (await owner.get("/api/gigs")).json()["gigs"]}
    rah = gigs[("Royal Albert Hall", "2019-10-02")]
    assert (rah["latitude"], rah["longitude"], rah["place_precision"]) == (
        51.5009,
        -0.1774,
        "venue",
    )
    assert gigs[("Pyramid Stage", "2017-06-24")]["place_precision"] == "city"
    assert gigs[("Nowhere Hall", "2010-01-01")]["place_precision"] is None
    # The Royal Albert Hall was looked up once for both gigs; the country narrows the search.
    assert [a.get("q") or a.get("city") for a in asked].count("Royal Albert Hall, London") == 1
    assert asked[0]["countrycodes"] == "gb"
