"""Live: upcoming shows from JamBase near the user, by the artists they play most."""

from __future__ import annotations

import datetime as dt
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

    assert [follow_count(n, 10) for n in (0, 1, 9, 10, 25, 5000)] == [0, 1, 1, 1, 3, 150]
    artists = [Followed(f"A{i}", f"{i:08d}-0000-0000-0000-000000000000", 1) for i in range(25)]
    artists += [Followed(f"B{i}", None, 1) for i in range(3)]
    assert [len(b) for b in batches(artists)] == [20, 5, 3]
    assert all(a.mbid for a in batches(artists)[1]) and not any(a.mbid for a in batches(artists)[2])


class FakeJamBase:
    def __init__(self, plex: FakePlex) -> None:
        self.requests: list[httpx.Request] = []
        self.remaining = 1000
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
        if params.get("artistId") == f"musicbrainz:{RADIOHEAD_MBID}":
            found = [CONCERT, FESTIVAL]
        elif params.get("artistName") == "Portishead":
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
