"""Looking up a user's upcoming shows: their most-listened artists, near their place.

Runs in the worker. Every JamBase request is counted before it's sent and stops at the
instance's monthly cap (JAMBASE_MONTHLY_CAP); shows are replaced only after a complete run,
so a run cut short keeps the last list.
"""

from __future__ import annotations

import datetime as dt
import difflib
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plekstender.api.routes.library import plays_of, visible
from plekstender.config import Settings
from plekstender.db.instance import (
    get_jambase_api_key,
    get_jambase_usage,
    reserve_jambase_call,
    save_jambase_quota,
)
from plekstender.db.models import Artist, ArtistArtwork, LiveSettings, LiveShow, UserServer
from plekstender.live import jambase
from plekstender.live.geocode import distance_km
from plekstender.sync.parse import normalise_artist

log = logging.getLogger(__name__)

MAX_ARTISTS = 150
# Pages per batch at most (100 shows a page): a safety net against runaway paging.
MAX_PAGES = 3
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SKIP_NAMES = {"various artists", "various", "unknown artist", "[unknown]"}
FUZZY = 0.88


@dataclass
class Followed:
    """An artist the user follows, merged across their servers."""

    name: str
    mbid: str | None
    plays: int
    refs: list[tuple[int, int]] = field(default_factory=list)  # (server_id, rating_key)


def follow_count(listened: int, percent: int) -> int:
    """How many artists the top `percent`% of `listened` is (at least one, at most 150)."""
    if listened <= 0:
        return 0
    return max(1, min(MAX_ARTISTS, math.ceil(listened * percent / 100)))


async def listened_artists(db: AsyncSession, user_id: int) -> list[Followed]:
    """Every artist the user has played, most played first (merged across servers by
    MusicBrainz ID, else by name)."""
    servers = list(await db.scalars(select(UserServer).where(UserServer.user_id == user_id)))
    merged: dict[str, Followed] = {}
    for us in servers:
        state, cond, where = visible(Artist, user_id, us)
        plays = plays_of(state)
        work = ArtistArtwork
        rows = await db.execute(
            select(
                Artist.title,
                func.coalesce(Artist.mbid, work.mbid),
                plays,
                Artist.server_id,
                Artist.rating_key,
            )
            .join(state, cond)
            .outerjoin(
                work,
                (work.server_id == Artist.server_id) & (work.rating_key == Artist.rating_key),
            )
            .where(*where, plays > 0)
        )
        for title, raw_mbid, count, server_id, rating_key in rows.all():
            name = (title or "").strip()
            if not name or name.lower() in SKIP_NAMES:
                continue
            mbid = raw_mbid.lower() if raw_mbid and UUID.match(raw_mbid.lower()) else None
            key = mbid or normalise_artist(name)
            if found := merged.get(key):
                found.plays = max(found.plays, int(count))
                found.refs.append((server_id, rating_key))
            else:
                merged[key] = Followed(name, mbid, int(count), [(server_id, rating_key)])
    return sorted(merged.values(), key=lambda a: (-a.plays, a.name.lower()))


async def followed_artists(
    db: AsyncSession, user_id: int, percent: int
) -> tuple[list[Followed], int]:
    """The artists to look up (the top `percent`%) and how many the user has played."""
    listened = await listened_artists(db, user_id)
    return listened[: follow_count(len(listened), percent)], len(listened)


def batches(artists: list[Followed]) -> list[list[Followed]]:
    """Requests to make: artists with a MusicBrainz ID by ID, the rest by name."""
    with_id = [a for a in artists if a.mbid]
    by_name = [a for a in artists if not a.mbid]
    size = jambase.BATCH
    return [with_id[i : i + size] for i in range(0, len(with_id), size)] + [
        by_name[i : i + size] for i in range(0, len(by_name), size)
    ]


def match(performer: str, batch: list[Followed]) -> Followed | None:
    """The batch's artist this performer is: same name (normalised), else a close one."""
    key = normalise_artist(performer)
    keys = {normalise_artist(a.name): a for a in batch}
    if found := keys.get(key):
        return found
    close = difflib.get_close_matches(key, list(keys), n=1, cutoff=FUZZY)
    return keys[close[0]] if close else None


class Stop(Exception):
    """The run can't go on (no budget left, key refused, quota used up)."""


@dataclass
class Result:
    shows: int
    artists: int
    requests: int
    complete: bool
    error: str | None = None


async def refresh_user(
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    settings: Settings,
    user_id: int,
    *,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Result:
    """Look up the user's shows and store them. Records the outcome on their settings."""
    started = now()
    async with sessions() as db:
        live = await db.get(LiveSettings, user_id)
        if live is None or live.latitude is None or live.longitude is None:
            return Result(0, 0, 0, complete=False, error="No place is set.")
        lat, lon, radius, percent = live.latitude, live.longitude, live.radius_km, live.top_percent
        live.refresh_status, live.refresh_error = "running", None
        await db.commit()
        api_key, _ = await get_jambase_api_key(db, settings)
        artists, _ = await followed_artists(db, user_id, percent)

    requests = 0
    found: dict[str, tuple[jambase.Event, dict[tuple[int, int], dict[str, object]]]] = {}
    error: str | None = None
    try:
        if not api_key:
            raise Stop(
                "JamBase isn't set up: an admin can add an API key in Settings → Connections."
            )
        todo = batches(artists)
        await _check_quota(sessions, http, settings, api_key, len(todo))
        today = started.date()
        until = today + dt.timedelta(days=jambase.DAYS_AHEAD)
        for batch in todo:
            page, pages = 1, 1
            while page <= min(pages, MAX_PAGES):
                async with sessions() as db:
                    if not await reserve_jambase_call(db, settings.jambase_monthly_cap, now()):
                        raise Stop(
                            f"This month's JamBase requests ({settings.jambase_monthly_cap}) are "
                            "used up; shows are looked up again next month."
                        )
                requests += 1
                events, pages = await jambase.events(
                    http,
                    settings.jambase_api_url,
                    api_key,
                    latitude=lat,
                    longitude=lon,
                    radius_km=radius,
                    date_from=today,
                    date_to=until,
                    artist_ids=[f"musicbrainz:{a.mbid}" for a in batch if a.mbid] or None,
                    artist_names=[a.name for a in batch if not a.mbid] or None,
                    page=page,
                )
                for event in events:
                    _collect(found, event, batch)
                page += 1
    except Stop as exc:
        error = str(exc)
    except jambase.JamBaseError as exc:
        error = str(exc)

    complete = error is None
    async with sessions() as db:
        if found:
            await _store(db, user_id, found, lat, lon, now())
        if complete:
            # Shows not seen in a complete run are gone (cancelled, moved, or out of range).
            await db.execute(
                delete(LiveShow).where(
                    LiveShow.user_id == user_id,
                    LiveShow.source == "jambase",
                    LiveShow.fetched_at < started,
                )
            )
        live = await db.get(LiveSettings, user_id)
        if live is not None:
            live.refresh_status = "done" if complete else "failed"
            live.refresh_error = error
            live.request_count = requests
            live.artist_count = len(artists)
            if complete:
                live.refreshed_at = now()
        await db.commit()
    if error:
        log.info("Live refresh for user %s stopped: %s", user_id, error)
    return Result(len(found), len(artists), requests, complete, error)


async def _check_quota(
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    settings: Settings,
    api_key: str,
    needed: int,
) -> None:
    """Ask JamBase (free) how much is left, and stop before going over its quota."""
    try:
        quota = await jambase.quota(http, settings.jambase_api_url, api_key)
    except jambase.JamBaseKeyError as exc:
        raise Stop(str(exc)) from exc
    except jambase.JamBaseError:
        return  # the counter and the cap still apply
    async with sessions() as db:
        await save_jambase_quota(db, quota)
        await db.commit()
    remaining = quota.get("remainingCalls")
    if isinstance(remaining, int) and remaining < needed:
        end = str(quota.get("periodEnd") or "")[:10]
        raise Stop(
            "JamBase's monthly quota is nearly used up"
            + (f" (it renews {end})" if end else "")
            + "; shows are looked up again then."
        )


def _collect(
    found: dict[str, tuple[jambase.Event, dict[tuple[int, int], dict[str, object]]]],
    event: jambase.Event,
    batch: list[Followed],
) -> None:
    matched: dict[tuple[int, int], dict[str, object]] = {}
    for performer in event.performers:
        if artist := match(performer.name, batch):
            for server_id, rating_key in artist.refs:
                matched[(server_id, rating_key)] = {
                    "name": artist.name,
                    "headliner": performer.headliner,
                    "server_id": server_id,
                    "rating_key": rating_key,
                }
    if not matched:
        return  # a name search found someone else of a similar name
    if event.id in found:
        found[event.id][1].update(matched)
    else:
        found[event.id] = (event, matched)


async def _store(
    db: AsyncSession,
    user_id: int,
    found: dict[str, tuple[jambase.Event, dict[tuple[int, int], dict[str, object]]]],
    lat: float,
    lon: float,
    fetched_at: dt.datetime,
) -> None:
    rows = []
    for event, matched in found.values():
        distance = (
            round(distance_km(lat, lon, event.latitude, event.longitude), 1)
            if event.latitude is not None and event.longitude is not None
            else None
        )
        rows.append(
            {
                "user_id": user_id,
                "source": "jambase",
                "source_id": event.id,
                "name": event.name,
                "festival": event.festival,
                "start_date": event.start_date,
                "start_time": event.start_time,
                "end_date": event.end_date,
                "status": event.status,
                "venue": event.venue,
                "city": event.city,
                "region": event.region,
                "country": event.country,
                "latitude": event.latitude,
                "longitude": event.longitude,
                "distance_km": distance,
                "ticket_url": event.ticket_url,
                "seller": event.seller,
                "page_url": event.page_url,
                "performers": [
                    {"name": p.name, "headliner": p.headliner} for p in event.performers
                ],
                "artists": list(matched.values()),
                "fetched_at": fetched_at,
            }
        )
    stmt = insert(LiveShow).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[LiveShow.user_id, LiveShow.source, LiveShow.source_id],
        set_={c: stmt.excluded[c] for c in rows[0] if c not in {"user_id", "source", "source_id"}},
    )
    await db.execute(stmt)


async def due_users(
    db: AsyncSession, settings: Settings, now: dt.datetime, min_age: dt.timedelta
) -> list[int]:
    """Users whose shows to refresh today, oldest first, within today's share of what's
    left of the month's requests (so the budget lasts the month)."""
    rows = (
        await db.execute(
            select(LiveSettings.user_id, LiveSettings.artist_count, LiveSettings.request_count)
            .where(
                LiveSettings.latitude.is_not(None),
                (LiveSettings.refreshed_at.is_(None)) | (LiveSettings.refreshed_at < now - min_age),
            )
            .order_by(LiveSettings.refreshed_at.asc().nulls_first(), LiveSettings.user_id)
        )
    ).all()
    usage = await get_jambase_usage(db, now)
    left = max(0, settings.jambase_monthly_cap - usage.calls)
    next_month = (now.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    days_left = max(1, (next_month.date() - now.date()).days)
    allowance = max(left // days_left, 0)
    chosen: list[int] = []
    spent = 0
    for user_id, artist_count, request_count in rows:
        cost = request_count or max(1, math.ceil((artist_count or MAX_ARTISTS) / jambase.BATCH))
        # The first refresh of the day always goes, if the month has room for it.
        if (chosen and spent + cost > allowance) or cost > left:
            continue
        chosen.append(user_id)
        spent += cost
    return chosen
