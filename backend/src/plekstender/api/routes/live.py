"""Live: upcoming shows near you by the artists you listen to most.

Each user sets a place, a distance and how many of their artists to follow; the worker
looks the shows up (about daily, within the instance's JamBase budget) and they're read here.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import (
    LiveArtistCount,
    LiveArtistOut,
    LiveSettingsIn,
    LiveSettingsOut,
    LiveShowOut,
    LiveShowsOut,
    PlaceOut,
)
from plekstender.db.instance import get_jambase_api_key
from plekstender.db.models import LiveSettings, LiveShow, User
from plekstender.live import geocode
from plekstender.live.refresh import MAX_ARTISTS, follow_count, listened_artists
from plekstender.worker import LIVE_MANUAL_GAP, enqueue_live_refresh

router = APIRouter(prefix="/api/live", tags=["live"])

# Settings changes are looked up after a short pause, so a few quick changes cost one refresh.
SETTLE_SECONDS = 20


# A lookup still "queued" or "running" after this long was lost (the worker stopped).
STALE_AFTER = dt.timedelta(minutes=30)


def _stale(live: LiveSettings) -> bool:
    return (
        live.refresh_status in ("queued", "running")
        and live.refresh_requested_at is not None
        and live.refresh_requested_at < dt.datetime.now(dt.UTC) - STALE_AFTER
    )


def _available_at(live: LiveSettings | None) -> dt.datetime | None:
    if live is None or live.refresh_requested_at is None:
        return None
    at = live.refresh_requested_at + LIVE_MANUAL_GAP
    return at if at > dt.datetime.now(dt.UTC) else None


async def _out(db: DbDep, settings: SettingsDep, user: User) -> LiveSettingsOut:
    live = await db.get(LiveSettings, user.id)
    listened = len(await listened_artists(db, user.id))
    percent = live.top_percent if live else 10
    key, _ = await get_jambase_api_key(db, settings)
    return LiveSettingsOut(
        place_name=live.place_name if live else None,
        latitude=live.latitude if live else None,
        longitude=live.longitude if live else None,
        radius_km=live.radius_km if live else 100,
        top_percent=percent,
        listened_artists=listened,
        followed_artists=follow_count(listened, percent),
        max_artists=MAX_ARTISTS,
        refresh_status=("failed" if _stale(live) else live.refresh_status) if live else None,
        refresh_error=(
            "The last lookup didn't finish; refresh to try again."
            if _stale(live)
            else live.refresh_error
        )
        if live
        else None,
        refreshed_at=live.refreshed_at if live else None,
        refresh_available_at=_available_at(live),
        source_ready=key is not None,
    )


@router.get("/settings", response_model=LiveSettingsOut)
async def get_live_settings(db: DbDep, settings: SettingsDep, user: UserDep) -> LiveSettingsOut:
    return await _out(db, settings, user)


@router.put("/settings", response_model=LiveSettingsOut)
async def put_live_settings(
    body: LiveSettingsIn, db: DbDep, settings: SettingsDep, user: UserDep
) -> LiveSettingsOut:
    """Save the place, distance and artists to follow; the shows are looked up again."""
    live = await db.get(LiveSettings, user.id)
    if live is None:
        live = LiveSettings(user_id=user.id)
        db.add(live)
    changed = (live.latitude, live.longitude, live.radius_km, live.top_percent) != (
        body.latitude, body.longitude, body.radius_km, body.top_percent,
    )  # fmt: skip
    live.place_name = body.place_name.strip()
    live.latitude, live.longitude = body.latitude, body.longitude
    live.radius_km, live.top_percent = body.radius_km, body.top_percent
    await db.commit()
    if changed:
        if live.refreshed_at is not None:
            # The old list was for another place or other artists.
            await db.execute(delete(LiveShow).where(LiveShow.user_id == user.id))
            live.refreshed_at = None
            await db.commit()
        await enqueue_live_refresh(db, user.id, delay_seconds=SETTLE_SECONDS)
    return await _out(db, settings, user)


@router.delete("/settings", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_settings(db: DbDep, user: UserDep) -> Response:
    """Forget the place and the shows."""
    await db.execute(delete(LiveShow).where(LiveShow.user_id == user.id))
    await db.execute(delete(LiveSettings).where(LiveSettings.user_id == user.id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/refresh", response_model=LiveSettingsOut)
async def refresh(db: DbDep, settings: SettingsDep, user: UserDep) -> LiveSettingsOut:
    """Look the shows up again now (at most once an hour: it uses the shared budget)."""
    live = await db.get(LiveSettings, user.id)
    if live is None or live.latitude is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose a place first.")
    if live.refresh_status in ("queued", "running") and not _stale(live):
        return await _out(db, settings, user)
    if at := _available_at(live):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Shows were looked up recently; try again after {at:%H:%M} UTC.",
        )
    await enqueue_live_refresh(db, user.id)
    return await _out(db, settings, user)


@router.get("/places", response_model=list[PlaceOut], dependencies=[rate_limit("places", 60, 60)])
async def places(
    http: HttpDep,
    settings: SettingsDep,
    _: UserDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> list[PlaceOut]:
    try:
        found = await geocode.search(http, settings.geocoding_api_url, q.strip())
    except geocode.GeocodeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return [PlaceOut(name=p.name, latitude=p.latitude, longitude=p.longitude, country=p.country)
            for p in found]  # fmt: skip


@router.get("/shows", response_model=LiveShowsOut)
async def shows(
    db: DbDep,
    user: UserDep,
    artist: Annotated[str | None, Query(max_length=200)] = None,
    date_from: Annotated[dt.date | None, Query(alias="from")] = None,
    date_to: Annotated[dt.date | None, Query(alias="to")] = None,
) -> LiveShowsOut:
    """Upcoming shows, soonest first; `artist` narrows to one of the user's artists."""
    today = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)  # time zones: keep tonight
    start = max(date_from or today, today)
    query = (
        select(LiveShow)
        .where(
            LiveShow.user_id == user.id,
            func.coalesce(LiveShow.end_date, LiveShow.start_date) >= start,
        )
        .order_by(LiveShow.start_date, LiveShow.start_time.nulls_last(), LiveShow.id)
    )
    if date_to:
        query = query.where(LiveShow.start_date <= date_to)
    rows = list(await db.scalars(query))
    counts: Counter[str] = Counter()
    for row in rows:
        for name in dict.fromkeys(a["name"] for a in row.artists):
            counts[name] += 1
    if artist:
        rows = [r for r in rows if any(a["name"] == artist for a in r.artists)]
    return LiveShowsOut(
        shows=[_show(r) for r in rows],
        artists=[
            LiveArtistCount(name=n, shows=c)
            for n, c in sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))
        ],
    )


def _show(row: LiveShow) -> LiveShowOut:
    # One entry per artist (an artist on several servers is listed once).
    artists = {a["name"]: LiveArtistOut.model_validate(a) for a in row.artists}
    return LiveShowOut(
        id=row.id,
        source=row.source,
        name=row.name,
        festival=row.festival,
        start_date=row.start_date,
        start_time=row.start_time,
        end_date=row.end_date,
        status=row.status,
        venue=row.venue,
        city=row.city,
        region=row.region,
        country=row.country,
        distance_km=row.distance_km,
        ticket_url=row.ticket_url,
        seller=row.seller,
        page_url=row.page_url,
        artists=sorted(artists.values(), key=lambda a: not a.headliner),
        performers=[LiveArtistOut.model_validate(p) for p in row.performers],
    )
