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


def test_csv_dates_and_columns() -> None:
    from plekstender.gigs.imports import parse_csv, parse_date

    assert parse_date("2019-10-02") == dt.date(2019, 10, 2)
    assert parse_date("02/10/2019") == dt.date(2019, 10, 2)  # day first
    assert parse_date("10/25/2019") == dt.date(2019, 10, 25)  # unless it can't be
    assert parse_date("2 Oct 2019") == dt.date(2019, 10, 2)
    assert parse_date("October 2, 2019") == dt.date(2019, 10, 2)
    assert parse_date("soon") is None
    drafts, problems = parse_csv(
        "Event Date;Headliner;Support Acts;Venue Name;Town;Country;Festival;Notes\n"
        "02/10/2019;Portishead;Young Fathers, Kae Tempest;Royal Albert Hall;London;GB;;Brilliant\n"
        ";;;;;;;\n"
        "nonsense;Radiohead;;;;;;\n"
    )
    assert [(d.date, d.artists, d.venue, d.city, d.country, d.notes) for d in drafts] == [
        (
            dt.date(2019, 10, 2),
            ["Portishead", "Young Fathers", "Kae Tempest"],
            "Royal Albert Hall",
            "London",
            "GB",
            "Brilliant",
        ),
    ]
    assert problems == ["Row 4: no date or artist (nonsense)."]


SETLISTS = {
    1: {"type": "setlists", "itemsPerPage": 2, "page": 1, "total": 3, "setlist": [
        {"id": "a1", "eventDate": "02-10-2019", "url": "https://www.setlist.fm/setlist/portishead/a1.html",
         "artist": {"mbid": "8f6bd1e4", "name": "Portishead"},
         "venue": {"id": "v-rah", "name": "Royal Albert Hall", "city": {"name": "London", "country": {"code": "GB"}, "coords": {"lat": 51.5, "long": -0.12}}}},
        {"id": "a2", "eventDate": "02-10-2019", "artist": {"name": "Young Fathers"},
         "venue": {"id": "v-rah", "name": "Royal Albert Hall", "city": {"name": "London", "country": {"code": "GB"}}}},
    ]},
    2: {"type": "setlists", "itemsPerPage": 2, "page": 2, "total": 3, "setlist": [
        {"id": "b1", "eventDate": "24-06-2017", "artist": {"name": "Radiohead"}, "tour": {"name": "Glastonbury 2017"},
         "venue": {"id": "v-pyr", "name": "Pyramid Stage", "city": {"name": "Pilton", "country": {"code": "GB"}}}},
    ]},
}  # fmt: skip


@pytest.mark.db
async def test_gig_imports_and_exports(app: FastAPI, plex: FakePlex) -> None:
    import httpx

    calls: list[httpx.Request] = []

    def setlistfm(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-api-key") != "k" * 36:
            return httpx.Response(403, json={"code": 403, "message": "Forbidden"})
        calls.append(request)
        if "/search/artists" in request.url.path:
            return httpx.Response(200, json={"artist": []})
        if "/user/joakim/attended" in request.url.path:
            return httpx.Response(200, json=SETLISTS[int(request.url.params["p"])])
        return httpx.Response(404, json={"code": 404, "message": "not found"})

    plex.router.route(host="api.setlist.fm").mock(side_effect=setlistfm)
    owner, _ = await owner_with_library(app, plex)

    # setlist.fm: needs the admin's key; two artists' setlists of one night are one gig.
    missing = await owner.post(
        "/api/gigs/import/setlistfm", json={"username": "joakim"}, headers=CSRF
    )
    assert missing.status_code == 409
    assert (
        await owner.put("/api/admin/setlistfm", json={"api_key": "k" * 36}, headers=CSRF)
    ).status_code == 200
    first = await owner.post(
        "/api/gigs/import/setlistfm", json={"username": "joakim"}, headers=CSRF
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"added": 2, "updated": 0, "skipped": 0, "problems": []}
    assert calls[-1].headers["accept"] == "application/json"
    gigs = {g["date"]: g for g in (await owner.get("/api/gigs")).json()["gigs"]}
    rah = gigs["2019-10-02"]
    assert ([a["name"] for a in rah["artists"]], rah["venue"], rah["country"], rah["source"]) == (
        ["Portishead", "Young Fathers"], "Royal Albert Hall", "GB", "setlistfm",
    )  # fmt: skip
    assert rah["artists"][0]["rating_key"] == 101  # in the library
    assert gigs["2017-06-24"]["name"] == "Glastonbury 2017"
    again = await owner.post(
        "/api/gigs/import/setlistfm", json={"username": "joakim"}, headers=CSRF
    )
    assert again.json()["added"] == 0 and again.json()["updated"] == 2

    # CSV: the same night (added another way) is skipped; a new one is added.
    csv_body = (
        "date,artist,venue,city,country\n"
        "2019-10-02,Portishead,Royal Albert Hall,London,GB\n"
        "2024-05-01,Massive Attack,Brixton Academy,London,GB\n"
    )
    imported = await owner.post(
        "/api/gigs/import/csv",
        content=csv_body.encode(),
        headers={**CSRF, "content-type": "text/csv"},
    )
    assert imported.json() == {"added": 1, "updated": 0, "skipped": 1, "problems": []}
    bad = await owner.post(
        "/api/gigs/import/csv",
        content=b"just,some\n1,2\n",
        headers={**CSRF, "content-type": "text/csv"},
    )
    assert bad.status_code == 422 and "date and an artist" in bad.json()["detail"]

    # iCalendar: past events only.
    future = (TODAY + dt.timedelta(days=60)).strftime("%Y%m%d")
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "BEGIN:VEVENT", "UID:p1", "SUMMARY:Bonobo at Electric Brixton", "DTSTART;VALUE=DATE:20230311", "END:VEVENT",
        "BEGIN:VEVENT", "UID:f1", "SUMMARY:Future Islands at Roundhouse", f"DTSTART;VALUE=DATE:{future}", "END:VEVENT",
        "END:VCALENDAR", "",
    ])  # fmt: skip
    from_ics = await owner.post(
        "/api/gigs/import/ical",
        content=ics.encode(),
        headers={**CSRF, "content-type": "text/calendar"},
    )
    assert from_ics.json()["added"] == 1
    bonobo = [g for g in (await owner.get("/api/gigs")).json()["gigs"] if g["source"] == "ical"]
    assert [(g["date"], g["artists"][0]["name"], g["venue"]) for g in bonobo] == [
        ("2023-03-11", "Bonobo", "Electric Brixton"),
    ]

    # Exports.
    csv_out = await owner.get("/api/gigs/export.csv")
    assert csv_out.headers["content-disposition"] == 'attachment; filename="gigs.csv"'
    lines = csv_out.text.splitlines()
    assert lines[0].startswith("date,artists,festival,venue") and len(lines) == 5
    assert "2019-10-02,Portishead; Young Fathers,,Royal Albert Hall,London,GB" in csv_out.text
    assert len((await owner.get("/api/gigs/export.json")).json()["gigs"]) == 4
    ics_out = (await owner.get("/api/gigs/export.ics")).text
    assert ics_out.count("BEGIN:VEVENT") == 4 and "SUMMARY:Portishead + Young Fathers" in ics_out


def test_gig_stats() -> None:
    from plekstender.db.models import Gig
    from plekstender.gigs.stats import gig_stats, season

    assert [season(m, 51.5) for m in (12, 1, 3, 6, 9)] == [
        "winter",
        "winter",
        "spring",
        "summer",
        "autumn",
    ]
    assert season(12, -33.9) == "summer"  # Sydney
    gigs = [
        Gig(date=dt.date(2019, 10, 2), venue="Royal Albert Hall", city="London", latitude=51.5,
            artists=[{"name": "Portishead", "rating_key": 101, "server_id": 1}, {"name": "Young Fathers"}]),
        Gig(date=dt.date(2019, 12, 1), venue="Royal Albert Hall", city="London",
            artists=[{"name": "Portishead"}]),
        Gig(date=dt.date(2017, 6, 24), venue="Pyramid Stage", city="Pilton",
            artists=[{"name": "Radiohead"}]),
        Gig(date=dt.date(2020, 1, 10), venue="Opera House", city="Sydney", latitude=-33.86,
            artists=[{"name": "The Radiohead"}]),
    ]  # fmt: skip
    s = gig_stats(gigs)
    assert (s.total, s.artists, s.venues, s.first, s.last) == (
        4,
        3,
        3,
        dt.date(2017, 6, 24),
        dt.date(2020, 1, 10),
    )
    assert [(a["name"], a["gigs"], a["rating_key"]) for a in s.top_artists] == [
        ("Portishead", 2, 101), ("Radiohead", 2, None), ("Young Fathers", 1, None),
    ]  # fmt: skip
    assert s.top_venues[0] == {"name": "Royal Albert Hall", "city": "London", "gigs": 2}
    assert s.years == [
        {"year": y, "gigs": n} for y, n in ((2017, 1), (2018, 0), (2019, 2), (2020, 1))
    ]
    assert s.months[9] == 1 and s.months[11] == 1
    assert s.seasons == {
        "winter": 1,
        "spring": 0,
        "summer": 2,
        "autumn": 1,
    }  # Sydney's January is summer
