"""Live: upcoming shows from JamBase near the user, by the artists they play most."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text, update

from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.instance import get_jambase_usage
from plekstender.db.models import Artist, LiveSettings, User
from plekstender.live import jambase
from plekstender.live.refresh import (
    Followed,
    batches,
    due_users,
    follow_count,
    match,
    refresh_user,
)
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs

API = "https://api.data.jambase.com/v3"
KEY = "jbd_test_0123456789abcdef"
RADIOHEAD_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
STOCKHOLM = {"place_name": "Stockholm, Sweden", "latitude": 59.3293, "longitude": 18.0686}

# Shaped like the events in JamBase's documentation (country and region as objects or
# plain strings, dates with or without a time and an offset).
CONCERT = {
    "@type": "Concert",
    "identifier": "jambase:111",
    "name": "Radiohead at Avicii Arena",
    "url": "https://www.jambase.com/show/radiohead-avicii-arena",
    "startDate": "2027-03-04T19:30:00+01:00",
    "eventStatus": "scheduled",
    "location": {
        "@type": "Venue",
        "name": "Avicii Arena",
        "address": {
            "addressLocality": "Stockholm",
            "addressRegion": {
                "@type": "State",
                "name": "Stockholm County",
                "alternateName": "SE-AB",
            },
            "addressCountry": {"@type": "Country", "name": "Sweden", "alternateName": "SE"},
        },
        "geo": {"@type": "GeoCoordinates", "latitude": 59.2936, "longitude": 18.0832},
    },
    "offers": [
        {"@type": "Offer", "name": "Resale", "url": "https://resale.example/rh"},
        {
            "@type": "Offer",
            "category": "ticketingLinkPrimary",
            "url": "https://www.ticketmaster.se/event/radiohead",
            "seller": {"@type": "Organization", "name": "Ticketmaster"},
        },
    ],
    "performer": [
        {"@type": "MusicGroup", "name": "Radiohead", "x-isHeadliner": True},
        {"@type": "MusicGroup", "name": "Support Act", "x-isHeadliner": False},
    ],
}
FESTIVAL = {
    "@type": "Festival",
    "identifier": "jambase:222",
    "name": "Way Out West 2027",
    "startDate": "2027-08-12",
    "endDate": "2027-08-14",
    "location": {
        "name": "Slottsskogen",
        "address": {"addressLocality": "Gothenburg", "addressCountry": "SE"},
    },
    "performer": [
        {"name": "Portishead", "x-isHeadliner": True},
        {"name": "Radiohead", "x-isHeadliner": False},
    ],
}
TRIBUTE = {
    "@type": "Concert",
    "identifier": "jambase:333",
    "name": "Portishead Tribute Night",
    "startDate": "2027-01-10T20:00:00",
    "location": {"name": "Pub", "address": {"addressLocality": "Uppsala"}},
    "performer": [{"name": "Portishead Tribute Band"}],
}


def test_parse_event_shapes() -> None:
    concert = jambase.parse_event(CONCERT)
    assert concert is not None
    assert (concert.start_date, concert.start_time, concert.end_date) == (
        dt.date(2027, 3, 4), "19:30", None,
    )  # fmt: skip
    assert (concert.venue, concert.city, concert.region, concert.country) == (
        "Avicii Arena", "Stockholm", "Stockholm County", "SE",
    )  # fmt: skip
    # The primary ticket link, exactly as given.
    assert (concert.ticket_url, concert.seller) == (
        "https://www.ticketmaster.se/event/radiohead", "Ticketmaster",
    )  # fmt: skip
    assert [(p.name, p.headliner) for p in concert.performers] == [
        ("Radiohead", True), ("Support Act", False),
    ]  # fmt: skip

    festival = jambase.parse_event(FESTIVAL)
    assert festival is not None and festival.festival
    assert (festival.start_time, festival.end_date, festival.country) == (
        None, dt.date(2027, 8, 14), "SE",
    )  # fmt: skip
    assert festival.latitude is None and festival.ticket_url is None

    assert jambase.parse_event({"identifier": "x", "startDate": "soon"}) is None
    assert jambase.parse_event("nonsense") is None


def test_matching_and_batches() -> None:
    batch = [Followed("The Beatles", None, 5), Followed("Sigur Rós", None, 3)]
    assert match("Beatles", batch) is batch[0]
    assert match("Sigur Ros", batch) is batch[1]
    assert match("Beatles Tribute Band", batch) is None

    assert [follow_count(n, 10) for n in (0, 1, 9, 10, 25, 5000)] == [0, 1, 1, 1, 3, 500]
    artists = [Followed(f"A{i}", f"{i:08d}-0000-0000-0000-000000000000", 1) for i in range(25)]
    artists += [Followed(f"B{i}", None, 1) for i in range(3)]
    assert [len(b) for b in batches(artists)] == [20, 5, 3]
    assert all(a.mbid for a in batches(artists)[1]) and not any(a.mbid for a in batches(artists)[2])


class FakeJamBase:
    def __init__(self, plex: FakePlex) -> None:
        self.requests: list[httpx.Request] = []
        self.remaining = 1000
        self.refuse_ids = False  # answer every artistId search with an undocumented 400
        plex.router.get(f"{API}/quota").mock(side_effect=self._quota)
        plex.router.get(f"{API}/events").mock(side_effect=self._events)

    def _auth(self, request: httpx.Request) -> httpx.Response | None:
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"type": "unauthorized", "title": "Unauthorized"})
        return None

    def _quota(self, request: httpx.Request) -> httpx.Response:
        if refused := self._auth(request):
            return refused
        return httpx.Response(200, json={
            "plan": "developer", "quota": 1000, "usedCalls": 1000 - self.remaining,
            "remainingCalls": self.remaining, "overageCalls": 0, "noMonthlyCap": False,
            "periodStart": "2026-11-01T00:00:00Z", "periodEnd": "2026-12-01T00:00:00Z",
            "periodSource": "calendar", "limitType": "soft", "blocksAtQuota": False,
            "notes": {"rateLimitHeaders": "", "overage": ""},
        })  # fmt: skip

    def _events(self, request: httpx.Request) -> httpx.Response:
        if refused := self._auth(request):
            return refused
        self.requests.append(request)
        params = request.url.params
        found: list[dict[str, Any]] = []
        if params.get("artistId") and self.refuse_ids:
            return httpx.Response(400, json={"success": False, "errors": [{"field": "artistId"}]})
        if ids := params.get("artistId"):
            # As JamBase answers: every ID it doesn't know named, and the request fails.
            unknown = [i for i in ids.split("|") if i != f"musicbrainz:{RADIOHEAD_MBID}"]
            if unknown:
                errors = [
                    {
                        "code": "identifier_invalid",
                        "message": "No artist found for source `musicbrainz` artist id "
                        f"`{i.removeprefix('musicbrainz:')}`",
                    }
                    for i in unknown
                ]
                return httpx.Response(400, json={"success": False, "errors": errors})
            found = [CONCERT, FESTIVAL]
        elif "Portishead" in (params.get("artistName") or "").split("|"):
            found = [FESTIVAL, TRIBUTE]
        return httpx.Response(200, json={
            "success": True,
            "pagination": {"page": 1, "perPage": 100, "totalItems": len(found), "totalPages": 1},
            "events": found,
        })  # fmt: skip


async def owner_listening(app: FastAPI, plex: FakePlex) -> tuple[httpx.AsyncClient, int]:
    """The owner, who has played Radiohead (with a MusicBrainz ID) and Portishead."""
    owner, _ = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        assert user_id
        await db.execute(update(Artist).where(Artist.rating_key == 100).values(mbid=RADIOHEAD_MBID))
        for key, plays in ((100, 50), (101, 10)):
            await db.execute(
                text(
                    "UPDATE user_item_state SET view_count = :plays"
                    " WHERE user_id = :user AND rating_key = :key"
                ),
                {"plays": plays, "user": user_id, "key": key},
            )
        await db.commit()
    return owner, user_id


async def refresh(user_id: int, **overrides: Any) -> Any:
    settings = get_settings().model_copy(update=overrides)
    async with httpx.AsyncClient() as http:
        return await refresh_user(get_sessionmaker(), http, settings, user_id)


@pytest.mark.db
async def test_live_shows_flow(app: FastAPI, plex: FakePlex) -> None:
    jb = FakeJamBase(plex)
    owner, user_id = await owner_listening(app, plex)

    # The admin's key is checked with the free quota request.
    bad = await owner.put(
        "/api/admin/jambase", json={"api_key": "jbd_wrong_0123456789"}, headers=CSRF
    )
    assert bad.status_code == 400
    assert "didn't accept" in bad.json()["detail"]
    saved = await owner.put("/api/admin/jambase", json={"api_key": KEY}, headers=CSRF)
    assert saved.status_code == 200, saved.text
    assert saved.json()["configured"] and saved.json()["quota"]["remainingCalls"] == 1000
    assert jb.requests == []

    before = (await owner.get("/api/live/settings")).json()
    assert (before["place_name"], before["listened_artists"], before["followed_artists"]) == (
        None, 2, 1,
    )  # fmt: skip
    assert before["source_ready"]

    resp = await owner.put(
        "/api/live/settings", json={**STOCKHOLM, "top_percent": 100}, headers=CSRF
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["followed_artists"] == 2
    assert resp.json()["refresh_status"] == "queued"

    result = await refresh(user_id)
    assert result.complete, result.error
    # Radiohead by MusicBrainz ID, Portishead (no ID) by name: two requests.
    assert result.requests == 2
    assert {r.url.params.get("geoRadiusUnits") for r in jb.requests} == {"km"}
    assert jb.requests[0].url.params["geoLatitude"] == "59.3293"

    shows = (await owner.get("/api/live/shows")).json()
    # The tribute act isn't Portishead; the festival is listed once with both artists.
    assert [s["name"] for s in shows["shows"]] == ["Radiohead at Avicii Arena", "Way Out West 2027"]
    concert, festival = shows["shows"]
    assert concert["distance_km"] == pytest.approx(4.1, abs=0.2)
    assert concert["ticket_url"] == "https://www.ticketmaster.se/event/radiohead"
    assert [(a["name"], a["headliner"]) for a in festival["artists"]] == [
        ("Portishead", True), ("Radiohead", False),
    ]  # fmt: skip
    assert festival["artists"][0]["rating_key"] == 101
    assert shows["artists"] == [
        {"name": "Radiohead", "shows": 2},
        {"name": "Portishead", "shows": 1},
    ]
    only = (await owner.get("/api/live/shows", params={"artist": "Portishead"})).json()
    assert [s["name"] for s in only["shows"]] == ["Way Out West 2027"]
    until = (await owner.get("/api/live/shows", params={"to": "2027-04-01"})).json()
    assert [s["name"] for s in until["shows"]] == ["Radiohead at Avicii Arena"]

    async with get_sessionmaker()() as db:
        assert (await get_jambase_usage(db)).calls == 2
        live = await db.get(LiveSettings, user_id)
        assert live and (live.refresh_status, live.request_count, live.artist_count) == (
            "done",
            2,
            2,
        )

    # Saving the place queued a refresh, so one by hand waits an hour (the budget is shared).
    async with get_sessionmaker()() as db:
        await db.execute(text("DELETE FROM procrastinate_jobs"))  # the worker took it
        await db.commit()
    soon = await owner.post("/api/live/refresh", headers=CSRF)
    assert soon.status_code == 429
    async with get_sessionmaker()() as db:
        live = await db.get(LiveSettings, user_id)
        assert live and live.refresh_requested_at
        live.refresh_requested_at -= dt.timedelta(hours=2)
        await db.commit()
    again = await owner.post("/api/live/refresh", headers=CSRF)
    assert again.status_code == 200 and again.json()["refresh_status"] == "queued"
    assert again.json()["refresh_available_at"] is not None

    # A lookup the worker never finished doesn't block the page forever.
    async with get_sessionmaker()() as db:
        await db.execute(text("DELETE FROM procrastinate_jobs"))
        live = await db.get(LiveSettings, user_id)
        assert live
        live.refresh_status = "running"
        live.refresh_requested_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        await db.commit()
    stale = (await owner.get("/api/live/settings")).json()
    assert stale["refresh_status"] == "failed" and "didn't finish" in stale["refresh_error"]
    retried = await owner.post("/api/live/refresh", headers=CSRF)
    assert retried.status_code == 200 and retried.json()["refresh_status"] == "queued"

    # Someone else sees only their own (no place yet, nothing listed).
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()
    assert (await friend.get("/api/live/shows")).json()["shows"] == []
    assert (await friend.get("/api/admin/jambase")).status_code == 403

    # Forgetting the place removes the shows too.
    assert (await owner.delete("/api/live/settings", headers=CSRF)).status_code == 204
    assert (await owner.get("/api/live/shows")).json()["shows"] == []


@pytest.mark.db
async def test_live_budget(app: FastAPI, plex: FakePlex) -> None:
    jb = FakeJamBase(plex)
    owner, user_id = await owner_listening(app, plex)
    await owner.put("/api/admin/jambase", json={"api_key": KEY}, headers=CSRF)
    await owner.put("/api/live/settings", json={**STOCKHOLM, "top_percent": 100}, headers=CSRF)
    assert (await refresh(user_id)).complete

    # The cap stops a run before it sends anything more; the last list is kept.
    stopped = await refresh(user_id, jambase_monthly_cap=3)
    assert not stopped.complete and "used up" in (stopped.error or "")
    assert stopped.requests == 1 and len(jb.requests) == 3
    assert len((await owner.get("/api/live/shows")).json()["shows"]) == 2
    status = (await owner.get("/api/live/settings")).json()
    assert status["refresh_status"] == "failed" and "used up" in status["refresh_error"]

    # JamBase saying its quota is nearly gone stops a run before it starts.
    jb.remaining = 1
    early = await refresh(user_id)
    assert not early.complete and early.requests == 0 and "renews 2026-12-01" in (early.error or "")

    # The daily schedule: due once the last refresh is old, within the month's budget.
    settings = get_settings()
    now = dt.datetime(2026, 11, 20, 5, 20, tzinfo=dt.UTC)
    async with get_sessionmaker()() as db:
        live = await db.get(LiveSettings, user_id)
        assert live
        live.refreshed_at = now - dt.timedelta(hours=2)
        await db.commit()
        assert await due_users(db, settings, now, dt.timedelta(hours=20)) == []
        live.refreshed_at = now - dt.timedelta(days=1)
        await db.commit()
        assert await due_users(db, settings, now, dt.timedelta(hours=20)) == [user_id]
        tight = settings.model_copy(update={"jambase_monthly_cap": 3})
        assert await due_users(db, tight, now, dt.timedelta(hours=20)) == []


@pytest.mark.db
async def test_live_unknown_artist_ids(app: FastAPI, plex: FakePlex) -> None:
    """An ID JamBase doesn't know is left out (and remembered); that artist is searched by name."""
    jb = FakeJamBase(plex)
    owner, user_id = await owner_listening(app, plex)
    unknown = "0103c1cc-4a09-4a5d-a344-56ad99a77193"
    also_unknown = "ffe02aed-ef7e-4736-a186-c2f1dd55ce8d"
    async with get_sessionmaker()() as db:
        await db.execute(update(Artist).where(Artist.rating_key == 101).values(mbid=unknown))
        await db.execute(update(Artist).where(Artist.rating_key == 102).values(mbid=also_unknown))
        await db.execute(
            text(
                "UPDATE user_item_state SET view_count = 5 WHERE user_id = :u AND rating_key = 102"
            ),
            {"u": user_id},
        )
        await db.commit()
    await owner.put("/api/admin/jambase", json={"api_key": KEY}, headers=CSRF)
    await owner.put("/api/live/settings", json={**STOCKHOLM, "top_percent": 100}, headers=CSRF)

    first = await refresh(user_id)
    assert first.complete, first.error
    # All three IDs (two unknown, both named at once), Radiohead's again, the others by name.
    sent = [(r.url.params.get("artistId"), r.url.params.get("artistName")) for r in jb.requests]
    assert sent == [
        (f"musicbrainz:{RADIOHEAD_MBID}|musicbrainz:{unknown}|musicbrainz:{also_unknown}", None),
        (f"musicbrainz:{RADIOHEAD_MBID}", None),
        (None, "Portishead|Secret Band"),
    ]
    names = [s["name"] for s in (await owner.get("/api/live/shows")).json()["shows"]]
    assert names == ["Radiohead at Avicii Arena", "Way Out West 2027"]

    # Next time the unknown ID isn't sent at all.
    jb.requests.clear()
    second = await refresh(user_id)
    assert second.complete and second.requests == 2
    assert [r.url.params.get("artistName") for r in jb.requests] == [None, "Portishead|Secret Band"]


def test_error_messages() -> None:
    def problem(status: int, body: Any) -> str:
        resp = (
            httpx.Response(status, json=body)
            if not isinstance(body, str)
            else httpx.Response(status, text=body)
        )
        return jambase._problem(resp)

    documented = {"errors": [{"errorCode": "parameter_invalid", "errorMessage": "Bad date."}]}
    assert problem(400, documented) == "Bad date."
    assert problem(400, {"errors": [{"code": "x", "message": "Too many IDs."}]}) == "Too many IDs."
    assert problem(403, {"type": "plan-feature-required", "title": "Forbidden"}) == "Forbidden"
    assert problem(400, {"errors": [{"field": "artistId"}]}) == '{"errors":[{"field":"artistId"}]}'
    assert problem(400, "Bad Request") == "Bad Request"


@pytest.mark.db
async def test_live_ids_refused(app: FastAPI, plex: FakePlex) -> None:
    """A 400 for the ID search that names no ID: everyone is searched by name instead."""
    jb = FakeJamBase(plex)
    jb.refuse_ids = True
    owner, user_id = await owner_listening(app, plex)
    await owner.put("/api/admin/jambase", json={"api_key": KEY}, headers=CSRF)
    await owner.put("/api/live/settings", json={**STOCKHOLM, "top_percent": 100}, headers=CSRF)
    result = await refresh(user_id)
    assert result.complete, result.error
    assert [r.url.params.get("artistName") for r in jb.requests] == [
        None,
        "Portishead",
        "Radiohead",
    ]
    names = [s["name"] for s in (await owner.get("/api/live/shows")).json()["shows"]]
    assert names == ["Way Out West 2027"]  # the fake only knows Portishead by name


def test_unknown_id_answers() -> None:
    """JamBase's real answer (one error per ID) and the documented one."""
    found = "No artist found for source `musicbrainz` artist id `{}`"
    real = httpx.Response(400, json={"success": False, "errors": [
        {"code": "identifier_invalid", "message": found.format(
            "ffe02aed-ef7e-4736-a186-c2f1dd55ce8d")},
        {"code": "identifier_invalid", "message": found.format(
            "8F92558C-2baa-4758-8c38-615519e9deda")},
    ]})  # fmt: skip
    assert jambase._unknown_ids(real) == (True, [
        "musicbrainz:ffe02aed-ef7e-4736-a186-c2f1dd55ce8d",
        "musicbrainz:8f92558c-2baa-4758-8c38-615519e9deda",
    ])  # fmt: skip
    invalid = "The identifier `jambase:87121` is invalid."
    documented = httpx.Response(400, json={"errors": [
        {"errorCode": "identifier_invalid", "errorMessage": invalid},
    ]})  # fmt: skip
    assert jambase._unknown_ids(documented) == (True, ["jambase:87121"])
    other = httpx.Response(400, json={"errors": [{"code": "parameter_invalid", "message": "x"}]})
    assert jambase._unknown_ids(other) == (False, [])


# --- Ticketmaster, merging, the period, stars and the calendar ------------------------------

TM_API = "https://app.ticketmaster.com/discovery/v2"
TM_KEY = "TmKey0123456789abcdef"


def tm_event(event_id: str, day: str, attractions: list[str], venue: str, **extra: Any) -> dict:
    return {
        "id": event_id,
        "name": f"{attractions[0]} live",
        "url": f"https://www.ticketmaster.co.uk/event/{event_id}",
        "dates": {
            "start": {"localDate": day, "localTime": "19:00:00"},
            "status": {"code": extra.get("status", "onsale")},
        },
        "_embedded": {
            "venues": [{
                "name": venue,
                "city": {"name": "Stockholm"},
                "country": {"countryCode": "SE"},
                "location": {"latitude": extra.get("lat", "59.2937"), "longitude": "18.0833"},
            }],
            "attractions": [{"name": n} for n in attractions],
        },
    }  # fmt: skip


@pytest.fixture(autouse=True)
def no_ticketmaster_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    from plekstender.live import ticketmaster

    monkeypatch.setattr(ticketmaster, "INTERVAL", 0.0)


class FakeTicketmaster:
    def __init__(self, plex: FakePlex) -> None:
        self.requests: list[httpx.Request] = []
        self.events = [
            # The same show JamBase has (Radiohead at Avicii Arena on 4 March).
            tm_event("A1", "2027-03-04", ["Radiohead", "Support Act"], "Avicii Arena Stockholm"),
            # Only here: Portishead, and someone the user doesn't follow.
            tm_event("B2", "2027-02-01", ["Portishead"], "Annexet", status="postponed"),
            tm_event("C3", "2027-02-02", ["Somebody Else"], "Debaser"),
        ]
        self.crowded: set[str] = set()  # window starts that claim >1,000 events
        plex.router.get(f"{TM_API}/events.json").mock(side_effect=self._events)

    def _events(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("apikey") != TM_KEY:
            return httpx.Response(401, json={"fault": {"faultstring": "Invalid ApiKey"}})
        self.requests.append(request)
        params = request.url.params
        start = params.get("startDateTime", "")[:10]
        end = params.get("endDateTime", "9999-12-31")[:10]
        found = [e for e in self.events if start <= e["dates"]["start"]["localDate"] <= end]
        long = bool(start) and (
            dt.date.fromisoformat(end) - dt.date.fromisoformat(start) > dt.timedelta(days=20)
        )
        total = 1500 if start in self.crowded and long else len(found)
        return httpx.Response(200, json={
            "_embedded": {"events": found} if found else {},
            "page": {"size": 200, "totalElements": total, "totalPages": max(1, -(-total // 200)),
                     "number": int(params.get("page", 0))},
        })  # fmt: skip


def test_geohash_and_calendar_lines() -> None:
    from plekstender.live import ical, ticketmaster

    assert ticketmaster.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"
    show = ical.CalendarShow(
        uid="x@plekstender",
        title="Radiohead, with friends; live",
        start_date=dt.date(2027, 3, 4),
        start_time="19:30",
        end_date=None,
        status="scheduled",
        location="Avicii Arena",
        url="https://example.com/t",
        description="A" * 200,
        updated=dt.datetime(2026, 11, 19, tzinfo=dt.UTC),
    )
    text_ = ical.calendar([show], "Shows")
    assert "SUMMARY:Radiohead\\, with friends\\; live\r\n" in text_
    assert "DTSTART:20270304T193000\r\n" in text_ and "DTEND:20270304T223000\r\n" in text_
    assert all(len(line.encode()) <= 75 for line in text_.split("\r\n"))
    festival = replace(show, start_time=None, end_date=dt.date(2027, 3, 6))
    all_day = ical.calendar([festival], "Shows")
    assert "DTSTART;VALUE=DATE:20270304" in all_day and "DTEND;VALUE=DATE:20270307" in all_day


@pytest.mark.db
async def test_ticketmaster_merging_stars_and_calendar(app: FastAPI, plex: FakePlex) -> None:
    FakeJamBase(plex)
    tm = FakeTicketmaster(plex)
    owner, user_id = await owner_listening(app, plex)
    await owner.put("/api/admin/jambase", json={"api_key": KEY}, headers=CSRF)
    bad = await owner.put(
        "/api/admin/ticketmaster", json={"api_key": "WrongKey0123456789"}, headers=CSRF
    )
    assert bad.status_code == 400 and "Invalid ApiKey" in bad.json()["detail"]
    saved = await owner.put("/api/admin/ticketmaster", json={"api_key": TM_KEY}, headers=CSRF)
    assert saved.status_code == 200 and saved.json()["calls"] == 0  # the check isn't counted
    await owner.put("/api/live/settings", json={**STOCKHOLM, "top_percent": 100}, headers=CSRF)
    assert (await owner.get("/api/live/settings")).json()["sources"] == ["jambase", "ticketmaster"]

    tm.requests.clear()  # the key check
    tm.crowded = {dt.datetime.now(dt.UTC).date().isoformat()}  # the first month is split
    result = await refresh(user_id)
    assert result.complete, result.error
    params = [r.url.params for r in tm.requests]
    assert {p["unit"] for p in params} == {"km"} and {p["classificationName"] for p in params} == {
        "music"
    }
    assert params[0]["geoPoint"].startswith("u6sce")  # Stockholm's geohash
    starts = [p["startDateTime"][:10] for p in params]
    assert starts[0] == starts[1]  # the crowded window, then its first half
    assert len(starts) == 8  # 6 windows of 30 days, one split in two (+1 for the split)

    shows = (await owner.get("/api/live/shows")).json()["shows"]
    assert [(s["name"], s["sources"]) for s in shows] == [
        ("Portishead live", ["ticketmaster"]),
        ("Radiohead at Avicii Arena", ["jambase", "ticketmaster"]),
        ("Way Out West 2027", ["jambase"]),
    ]
    merged = shows[1]
    assert [t["seller"] for t in merged["tickets"]] == ["Ticketmaster", "Ticketmaster"]
    assert merged["tickets"][1]["url"] == "https://www.ticketmaster.co.uk/event/A1"
    assert shows[0]["status"] == "postponed" and shows[0]["start_time"] == "19:00"

    # Star the merged show (both rows), then lose it from both sources: it stays.
    star = await owner.put(
        "/api/live/stars", json={"ids": merged["ids"], "starred": True}, headers=CSRF
    )
    assert star.status_code == 204
    starred = (await owner.get("/api/live/shows", params={"starred": True})).json()["shows"]
    assert [s["name"] for s in starred] == ["Radiohead at Avicii Arena"]
    tm.events = tm.events[1:]
    CONCERT_BACKUP = dict(CONCERT)
    CONCERT.clear()
    CONCERT.update(TRIBUTE)  # JamBase no longer has it either
    try:
        assert (await refresh(user_id)).complete
    finally:
        CONCERT.clear()
        CONCERT.update(CONCERT_BACKUP)
    names = [s["name"] for s in (await owner.get("/api/live/shows")).json()["shows"]]
    assert "Radiohead at Avicii Arena" in names

    # The calendar: a private address, the starred show once, no sign-in needed.
    address = (await owner.get("/api/live/calendar")).json()["url"]
    path = address.removeprefix(get_settings().app_url)
    feed = await httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ).get(path)
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("text/calendar")
    body = feed.text
    assert body.count("BEGIN:VEVENT") == 1
    assert "SUMMARY:Radiohead\r\n" in body and "DTSTART:20270304T193000" in body
    assert "LOCATION:Avicii Arena\\, Stockholm" in body
    assert "STATUS:TENTATIVE" in body and "You have a ticket" not in body

    # A ticket: confirmed in the calendar; unstarring forgets it.
    got = await owner.put(
        "/api/live/tickets", json={"ids": merged["ids"], "got_ticket": True}, headers=CSRF
    )
    assert got.status_code == 204
    shows_now = (await owner.get("/api/live/shows", params={"starred": True})).json()["shows"]
    assert [(s["name"], s["got_ticket"]) for s in shows_now] == [
        ("Radiohead at Avicii Arena", True)
    ]
    body = (
        await httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test").get(
            path
        )
    ).text
    assert "STATUS:CONFIRMED" in body and "DESCRIPTION:You have a ticket." in body
    assert (await owner.get("/api/live/calendar")).json()["url"] == address
    new = (await owner.post("/api/live/calendar/reset", headers=CSRF)).json()["url"]
    assert new != address
    gone = await httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ).get(path)
    assert gone.status_code == 404
    one = await owner.get(f"/api/live/shows/{merged['id']}.ics")
    assert one.status_code == 200 and "attachment" in one.headers["content-disposition"]

    # Unstarring forgets the ticket and lets the next refresh drop the show.
    await owner.put("/api/live/stars", json={"ids": merged["ids"], "starred": False}, headers=CSRF)
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(text("SELECT count(*) FROM live_shows WHERE ticket_at IS NOT NULL"))
            == 0
        )
    async with get_sessionmaker()() as db:
        await db.execute(text("UPDATE live_shows SET fetched_at = fetched_at - interval '1 day'"))
        await db.commit()
    CONCERT.clear()
    CONCERT.update(TRIBUTE)
    try:
        await refresh(user_id)
    finally:
        CONCERT.clear()
        CONCERT.update(CONCERT_BACKUP)
    names = [s["name"] for s in (await owner.get("/api/live/shows")).json()["shows"]]
    assert "Radiohead at Avicii Arena" not in names


@pytest.mark.db
async def test_listening_period(app: FastAPI, plex: FakePlex) -> None:
    """ "Top artists of the past N months" ranks by the listens in that time."""
    from plekstender.db.models import Listen

    owner, user_id = await owner_listening(app, plex)
    now = dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as db:
        for days_ago, artist in ((10, "Portishead"), (12, "Portishead"), (400, "Radiohead")):
            db.add(Listen(
                user_id=user_id, listened_at=now - dt.timedelta(days=days_ago), source="lastfm",
                artist_name=artist, track_name="Song", artist_key=artist.lower(), track_key="song",
            ))  # fmt: skip
        await db.commit()
    base = {**STOCKHOLM, "top_percent": 100}
    all_time = (await owner.put("/api/live/settings", json=base, headers=CSRF)).json()
    assert (all_time["period_months"], all_time["listened_artists"]) == (None, 2)
    recent = (
        await owner.put("/api/live/settings", json={**base, "period_months": 6}, headers=CSRF)
    ).json()
    assert (recent["period_months"], recent["listened_artists"]) == (6, 1)
    preview = await owner.get("/api/live/listened", params={"period_months": 24})
    assert preview.json() == {"listened_artists": 2}  # Radiohead's listen 400 days ago counts
    async with get_sessionmaker()() as db:
        from plekstender.live.refresh import followed_artists, period_start

        artists, _ = await followed_artists(db, user_id, 100, period_start(6))
        assert [(a.name, a.plays) for a in artists] == [("Portishead", 2)]
