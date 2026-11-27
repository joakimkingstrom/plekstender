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
        if params.get("city") == "Pilton" and "amenity" not in params:
            return httpx.Response(200, json=[{"lat": "51.16", "lon": "-2.59"}])
        if params.get("q") == "Worthy Farm, Pilton":
            return httpx.Response(200, json=[{
                "lat": "51.1537", "lon": "-2.5872", "name": "Worthy Farm", "type": "farm",
                "display_name": "Worthy Farm, Pilton, Somerset, England, United Kingdom",
                "address": {"village": "Pilton", "country": "United Kingdom"},
            }])  # fmt: skip
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
    # A venue the free text misses is searched as a named place in the town, before the town.
    pyramid = [a for a in asked if a.get("amenity") == "Pyramid Stage"]
    assert pyramid and pyramid[0]["city"] == "Pilton"

    # Picking the place by hand (searched on OpenStreetMap): kept, and used for the other
    # gig at the same venue when asked to.
    found = await owner.get(
        "/api/gigs/places", params={"q": "Worthy Farm, Pilton", "country": "GB"}
    )
    assert found.json() == [
        {"name": "Worthy Farm", "address": "Pilton, United Kingdom", "kind": "farm",
         "latitude": 51.1537, "longitude": -2.5872},
    ]  # fmt: skip
    pyramid_gig = gigs[("Pyramid Stage", "2017-06-24")]
    second = await owner.post(
        "/api/gigs",
        json={"date": "2019-06-29", "artists": ["The Killers"], "venue": "Pyramid Stage",
              "city": "Pilton", "country": "GB"},
        headers=CSRF,
    )  # fmt: skip
    body = {k: pyramid_gig[k] for k in ("date", "venue", "city", "country", "setlists")}
    same = await owner.get(
        "/api/gigs/same-venue",
        params={"venue": "pyramid stage", "city": "Pilton", "country": "gb", "but": pyramid_gig["id"]},
    )  # fmt: skip
    assert same.json() == {"gigs": 1}
    pick = {"latitude": 51.1537, "longitude": -2.5872}
    picked = await owner.put(
        f"/api/gigs/{pyramid_gig['id']}",
        json={**body, "artists": ["Radiohead"], "place": pick},
        headers=CSRF,
    )
    assert (picked.json()["place_precision"], picked.json()["latitude"]) == ("manual", 51.1537)
    by_id = {g["id"]: g for g in (await owner.get("/api/gigs")).json()["gigs"]}
    assert by_id[second.json()["id"]]["place_precision"] != "manual"  # just this one
    await owner.put(
        f"/api/gigs/{pyramid_gig['id']}",
        json={**body, "artists": ["Radiohead"], "place": pick, "place_others": True},
        headers=CSRF,
    )
    by_id = {g["id"]: g for g in (await owner.get("/api/gigs")).json()["gigs"]}
    assert by_id[second.json()["id"]]["place_precision"] == "manual"
    assert by_id[second.json()["id"]]["latitude"] == 51.1537
    # Editing other things keeps it; the lookups leave it alone.
    kept = await owner.put(
        f"/api/gigs/{pyramid_gig['id']}",
        json={**body, "artists": ["Radiohead"], "notes": "Mud"},
        headers=CSRF,
    )
    assert kept.json()["place_precision"] == "manual"
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        await places.place_gigs(db, http, get_settings().nominatim_url)
    by_id = {g["id"]: g for g in (await owner.get("/api/gigs")).json()["gigs"]}
    assert by_id[pyramid_gig["id"]]["place_precision"] == "manual"
    # Renaming the venue on the other gigs there too (asked first in the app): they move to
    # the new name and are looked up again; without asking, only this one changes.
    third = await owner.post(
        "/api/gigs",
        json={"date": "2022-06-25", "artists": ["Billie Eilish"], "venue": "pyramid stage",
              "city": "Pilton", "country": "GB"},
        headers=CSRF,
    )  # fmt: skip
    renamed = {**body, "artists": ["Radiohead"], "venue": "The Pyramid Stage"}
    alone = await owner.put(f"/api/gigs/{pyramid_gig['id']}", json=renamed, headers=CSRF)
    assert alone.json()["venue"] == "The Pyramid Stage"
    by_id = {g["id"]: g for g in (await owner.get("/api/gigs")).json()["gigs"]}
    assert by_id[third.json()["id"]]["venue"] == "pyramid stage"
    old = await owner.get(
        "/api/gigs/same-venue",
        params={"venue": "Pyramid Stage", "city": "Pilton", "country": "GB", "but": pyramid_gig["id"]},
    )  # fmt: skip
    assert old.json() == {"gigs": 2}
    back = {**renamed, "venue": "Pyramid Stage"}
    await owner.put(f"/api/gigs/{pyramid_gig['id']}", json=back, headers=CSRF)
    together = await owner.put(
        f"/api/gigs/{pyramid_gig['id']}",
        json={**renamed, "rename_others": True},
        headers=CSRF,
    )
    assert together.status_code == 200
    by_id = {g["id"]: g for g in (await owner.get("/api/gigs")).json()["gigs"]}
    assert {by_id[i]["venue"] for i in (second.json()["id"], third.json()["id"])} == {
        "The Pyramid Stage"
    }
    assert by_id[second.json()["id"]]["place_precision"] is None  # looked up again
    await owner.put(
        f"/api/gigs/{pyramid_gig['id']}", json={**back, "rename_others": True}, headers=CSRF
    )

    # Asking for the lookup again forgets it.
    again = await owner.put(
        f"/api/gigs/{pyramid_gig['id']}",
        json={**body, "artists": ["Radiohead"], "find_place": True},
        headers=CSRF,
    )
    assert again.json()["place_precision"] is None


@pytest.mark.db
async def test_town_only_places_are_looked_up_again(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Places found only as their town before venues were searched as named places get one
    more lookup."""
    import httpx

    from plekstender.config import get_settings
    from plekstender.db.models import GigPlace
    from plekstender.gigs import places
    from plekstender.gigs.service import place_key

    monkeypatch.setattr(places, "INTERVAL", 0.0)

    def nominatim(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("amenity") == "Debaser Slussen":
            return httpx.Response(200, json=[{"lat": "59.3195", "lon": "18.0717"}])
        return httpx.Response(200, json=[])

    plex.router.get("https://nominatim.openstreetmap.org/search").mock(side_effect=nominatim)
    owner, _ = await owner_with_library(app, plex)
    gig = await owner.post(
        "/api/gigs",
        json={"date": "2003-04-20", "artists": ["Randy"], "venue": "Debaser Slussen",
              "city": "Stockholm", "country": "SE"},
        headers=CSRF,
    )  # fmt: skip
    key = place_key("Debaser Slussen", "Stockholm", "SE")
    async with get_sessionmaker()() as db:
        # As an earlier lookup left it: only the town.
        db.add(GigPlace(key=key, latitude=59.3293, longitude=18.0686, precision="city",
                        looked_up_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))  # fmt: skip
        await db.commit()
        async with httpx.AsyncClient() as http:
            await places.place_gigs(db, http, get_settings().nominatim_url)
    placed = (await owner.get("/api/gigs")).json()["gigs"][0]
    assert placed["id"] == gig.json()["id"]
    assert (placed["latitude"], placed["place_precision"]) == (59.3195, "venue")


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
         "venue": {"id": "v-rah", "name": "Royal Albert Hall", "city": {"name": "London", "country": {"code": "GB"}, "coords": {"lat": 51.5, "long": -0.12}}},
         "sets": {"set": [{"song": [{"name": "Glory Box"}, {"name": "Roads"}]}]}},
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
    assert rah["setlists"] == [
        {"artist": "Portishead", "url": "https://www.setlist.fm/setlist/portishead/a1.html",
         "songs": [{"name": "Glory Box", "cover": None, "tape": False}, {"name": "Roads", "cover": None, "tape": False}]},
    ]  # fmt: skip
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
    from plekstender.gigs.stats import gig_stats

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


SETLIST_PAGE = (
    "https://www.setlist.fm/setlist/radiohead/2017/worthy-farm-pilton-england-63de4613.html"
)
RADIOHEAD_SET = {
    "id": "63de4613",
    "eventDate": "24-06-2017",
    "url": SETLIST_PAGE,
    "artist": {"name": "Radiohead"},
    "tour": {"name": "Glastonbury 2017"},
    "venue": {
        "id": "v-wf",
        "name": "Worthy Farm",
        "city": {"name": "Pilton", "country": {"code": "GB"}},
    },
    "sets": {
        "set": [
            {
                "song": [
                    {"name": "Daydreaming"},
                    {"name": "Paranoid Android"},
                    {"name": "Intro", "tape": True},
                ]
            },
            {"encore": 1, "song": [{"name": "Karma Police"}, {"name": "Creep", "cover": None}]},
        ]
    },
}
SUPPORT_SET = {
    "id": "aa11", "eventDate": "24-06-2017", "artist": {"name": "Support Act"},
    "venue": {"id": "v-wf", "name": "Worthy Farm", "city": {"name": "Pilton", "country": {"code": "GB"}}},
    "sets": {"set": [{"song": [{"name": "Opener"}, {"name": "Lithium", "cover": {"name": "Nirvana"}}]}]},
}  # fmt: skip


@pytest.mark.db
async def test_gig_from_a_setlist_link(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    asked: list[httpx.Request] = []

    def setlistfm(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if request.url.path.endswith("/setlist/63de4613"):
            return httpx.Response(200, json=RADIOHEAD_SET)
        if request.url.path.endswith("/search/setlists"):
            return httpx.Response(200, json={"setlist": [RADIOHEAD_SET, SUPPORT_SET]})
        return httpx.Response(404, json={"code": 404})

    plex.router.route(host="api.setlist.fm").mock(side_effect=setlistfm)
    owner, sid = await owner_with_library(app, plex)
    await owner.put("/api/admin/setlistfm", json={"api_key": "k" * 36}, headers=CSRF)

    bad = await owner.post(
        "/api/gigs/setlistfm/lookup", json={"url": "https://example.com/x"}, headers=CSRF
    )
    assert bad.status_code == 422
    found = await owner.post("/api/gigs/setlistfm/lookup", json={"url": SETLIST_PAGE}, headers=CSRF)
    assert found.status_code == 200, found.text
    gig = found.json()
    # The night's details and the whole bill (from that date and venue), headliner first.
    assert (gig["date"], gig["venue"], gig["city"], gig["country"], gig["name"], gig["url"]) == (
        "2017-06-24", "Worthy Farm", "Pilton", "GB", "Glastonbury 2017", SETLIST_PAGE,
    )  # fmt: skip
    assert gig["artists"] == ["Radiohead", "Support Act"]
    assert [s["name"] for s in gig["setlists"][0]["songs"]] == [
        "Daydreaming", "Paranoid Android", "Intro", "Karma Police", "Creep",
    ]  # fmt: skip
    assert gig["setlists"][0]["songs"][2]["tape"] is True
    assert gig["setlists"][1]["songs"][1]["cover"] == "Nirvana"
    search = asked[-1]
    assert search.url.params["date"] == "24-06-2017" and search.url.params["venueId"] == "v-wf"

    # Searching setlist.fm: by artist, year and town; each setlist with its link.
    assert (await owner.get("/api/gigs/setlistfm/search")).status_code == 422
    results = (
        await owner.get(
            "/api/gigs/setlistfm/search",
            params={"artist": " Radiohead ", "year": 2017, "city": "Pilton"},
        )
    ).json()
    assert asked[-1].url.params["artistName"] == "Radiohead"
    assert (asked[-1].url.params["year"], asked[-1].url.params["cityName"]) == ("2017", "Pilton")
    assert results["items"] == [  # (the support act's setlist has no link: left out)
        {"url": SETLIST_PAGE, "date": "2017-06-24", "artist": "Radiohead", "venue": "Worthy Farm",
         "city": "Pilton", "country": "GB", "tour": "Glastonbury 2017", "songs": 5},
    ]  # fmt: skip

    saved = await owner.post(
        "/api/gigs",
        json={
            k: gig[k]
            for k in ("date", "artists", "name", "venue", "city", "country", "url", "setlists")
        },
        headers=CSRF,
    )
    assert saved.status_code == 201, saved.text
    assert len(saved.json()["setlists"]) == 2

    # On the artist's page: the gigs they played at, and songs seen live marked.
    gigs = (await owner.get(f"/api/gigs/artist/{sid}/100")).json()
    assert [g["venue"] for g in gigs] == ["Worthy Farm"]
    assert (await owner.get(f"/api/gigs/artist/{sid}/101")).json() == []  # Portishead: no
    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert {t["title"]: t["seen_live"] for t in album["tracks"]} == {
        "Paranoid Android": 1, "Karma Police": 1,
    }  # fmt: skip
    remaster = (await owner.get(f"/api/servers/{sid}/library/albums/201")).json()
    seen = {t["title"]: t["seen_live"] for t in remaster["tracks"]}
    assert seen["Karma Police (Remastered)"] == 1  # the same song
    assert sum(seen.values()) == 1
    artist = (await owner.get(f"/api/servers/{sid}/library/artists/100")).json()
    top = {t["title"]: t["seen_live"] for t in artist["top_tracks"]}
    assert top["Paranoid Android"] == 1 and sum(top.values()) == len(
        [t for t in top if t.startswith(("Paranoid Android", "Karma Police"))]
    )

    # Library → Stats: the share of the library's songs seen live, and by artist.
    share = (await owner.get(f"/api/servers/{sid}/library/stats/seen-live")).json()
    assert (share["tracks"], share["seen"], share["setlists"]) == (6, 3, 1)
    # Artists: anyone on a gig's bill (Radiohead here; the support act isn't in the library).
    assert (share["gigs"], share["artists_seen"], share["artists_total"]) == (1, 1, 3)
    assert share["artists"] == []  # Radiohead has too few songs here to rank
    from plekstender.api.routes import library_stats

    monkeypatch.setattr(library_stats, "SEEN_LIVE_MIN_TRACKS", 1)
    share = (await owner.get(f"/api/servers/{sid}/library/stats/seen-live")).json()
    assert [(a["title"], a["seen"]) for a in share["artists"]] == [("Radiohead", 3)]
    assert share["artists"][0]["tracks"] >= 3


@pytest.mark.db
async def test_gig_filters_and_biggest_artists_first(app: FastAPI, plex: FakePlex) -> None:
    from sqlalchemy import update

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import Artist, Gig

    owner, _ = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        await db.execute(update(Artist).where(Artist.rating_key == 100).values(popularity=5000))
        await db.execute(update(Artist).where(Artist.rating_key == 101).values(popularity=3000))
        await db.commit()
    gigs = [
        ("2019-10-02", ["Support Act", "Portishead"], "Royal Albert Hall", "London", "GB"),
        ("2019-10-02", ["Radiohead"], "O2 Academy", "Brixton", "GB"),  # same day, bigger
        ("2018-05-01", ["Portishead"], "O2 Academy", "Glasgow", "GB"),
        ("2017-06-24", ["Support Act", "Radiohead"], "Annexet", "Stockholm", "SE"),
    ]
    for date, artists, venue, city, country in gigs:
        body = {"date": date, "artists": artists, "venue": venue, "city": city, "country": country}
        assert (await owner.post("/api/gigs", json=body, headers=CSRF)).status_code == 201
    async with get_sessionmaker()() as db:  # one found at its venue, one placed by hand
        await db.execute(update(Gig).where(Gig.city == "London").values(place_precision="venue"))
        await db.execute(update(Gig).where(Gig.city == "Glasgow").values(place_precision="manual"))
        await db.execute(update(Gig).where(Gig.city == "Stockholm").values(place_precision="city"))
        await db.commit()

    listed = (await owner.get("/api/gigs")).json()
    # On the same day the biggest artist's gig comes first, and each bill biggest first
    # (artists not in the library last, in the order given).
    assert [[a["name"] for a in g["artists"]] for g in listed["gigs"]] == [
        ["Radiohead"],
        ["Portishead", "Support Act"],
        ["Portishead"],
        ["Radiohead", "Support Act"],
    ]
    assert {(p["country"], p["city"], p["venue"], p["gigs"]) for p in listed["places"]} == {
        ("GB", "London", "Royal Albert Hall", 1),
        ("GB", "Brixton", "O2 Academy", 1),
        ("GB", "Glasgow", "O2 Academy", 1),
        ("SE", "Stockholm", "Annexet", 1),
    }
    assert listed["unplaced"] == 2  # Brixton (not looked up yet) and Stockholm (town only)

    async def cities(**params: object) -> list[str]:
        found = (await owner.get("/api/gigs", params=params)).json()["gigs"]
        return [g["city"] for g in found]

    assert await cities(country="gb") == ["Brixton", "London", "Glasgow"]
    assert await cities(city="stockholm") == ["Stockholm"]
    assert await cities(venue="O2 Academy") == ["Brixton", "Glasgow"]
    assert await cities(venue="O2 Academy", city="Glasgow") == ["Glasgow"]
    assert await cities(unplaced=True) == ["Brixton", "Stockholm"]
    assert await cities(country="SE", year=2019) == []
    assert (await owner.get("/api/gigs", params={"country": "Sweden"})).status_code == 422


def test_setlist_links() -> None:
    """The setlist's ID from a setlist.fm link, wherever it is in what was pasted; quickly,
    however long and repetitive the text."""
    import time

    from plekstender.gigs.imports import setlist_id

    page = (
        "https://www.setlist.fm/setlist/radiohead/2017/pyramid-stage-pilton-england-63de4613.html"
    )
    assert setlist_id(page) == "63de4613"
    assert setlist_id("setlist.fm/setlist/63DE4613.html") == "63de4613"
    assert setlist_id(f"see {page}?ref=x thanks") == "63de4613"
    assert setlist_id("https://www.setlist.fm/venue/the-o2-63de4613.html") is None
    assert setlist_id("https://example.com/setlist/63de4613.html") is None
    assert setlist_id("") is None
    started = time.perf_counter()
    assert setlist_id("setlist.fm/setlist/" * 100) is None
    assert setlist_id("setlist.fm/setlist/" + "-" * 100_000) is None  # too long to be a link
    assert time.perf_counter() - started < 0.2
