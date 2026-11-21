"""How every connection is doing, for the Home page's Status card: the user's own (servers,
scrobble services, calendar links) and the instance's (service keys, SearXNG).

Only what Plekstender already knows is read (no outside calls), except SearXNG, which runs
next to it: its health is checked with a short timeout and kept a minute. Usage and expiry
details of the instance's services are for admins; others see whether each is set up.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Literal

import httpx
from fastapi import APIRouter
from sqlalchemy import select

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.schemas import ConnectionStatus
from plekstender.config import Settings
from plekstender.db.instance import (
    DataThistleTokens,
    DataThistleUsage,
    get_acoustid_api_key,
    get_datathistle_tokens,
    get_datathistle_usage,
    get_fanart_api_key,
    get_jambase_api_key,
    get_jambase_usage,
    get_lastfm_api_key,
    get_setlistfm_api_key,
    get_spotify_app,
    get_ticketmaster_api_key,
    get_ticketmaster_usage,
)
from plekstender.db.models import ListenBrainzLink, LiveFeed, ScrobbleTarget, SyncRun, UserServer
from plekstender.history.scrobblers import service_label

router = APIRouter(prefix="/api/status", tags=["health"])

State = Literal["ok", "warning", "error", "off"]
NEAR_LIMIT = 0.8
SEARXNG_TTL = 60.0
SERVICES = "/settings/services"
_searxng: tuple[str, float, bool] | None = None  # (url, checked at, healthy)


async def searxng_healthy(http: httpx.AsyncClient, url: str) -> bool:
    global _searxng
    if _searxng and _searxng[0] == url and time.monotonic() - _searxng[1] < SEARXNG_TTL:
        return _searxng[2]
    try:
        resp = await http.get(f"{url.rstrip('/')}/healthz", timeout=2.0)
        healthy = resp.status_code == 200
    except httpx.HTTPError:
        healthy = False
    _searxng = (url, time.monotonic(), healthy)
    return healthy


def instance(
    name: str,
    state: State,
    detail: str | None = None,
    link: str | None = None,
    usage: float | None = None,
) -> ConnectionStatus:
    return ConnectionStatus(
        name=name, group="instance", state=state, detail=detail, link=link, usage=usage
    )


def with_limit(
    name: str, calls: int, cap: int, period: str, link: str, admin: bool
) -> ConnectionStatus:
    """A service with a request limit: amber from 80 %, red at the limit."""
    numbers = f"{calls:,} of {cap:,} requests {period}"
    used = min(1.0, calls / cap) if cap and admin else None
    if calls >= cap:
        detail = f"Plekstender's limit reached: {numbers}" if admin else "At its limit"
        return instance(name, "error", detail, link, used)
    state: State = "warning" if calls >= cap * NEAR_LIMIT else "ok"
    return instance(name, state, numbers if admin else None, link, used)


def data_thistle(
    tokens: DataThistleTokens, usage: DataThistleUsage, admin: bool, now: dt.datetime
) -> ConnectionStatus:
    link = f"{SERVICES}/gigs"
    expired = (tokens.refresh_expires is not None and tokens.refresh_expires <= now) and (
        tokens.access_expires is None or tokens.access_expires <= now
    )
    left = f"{usage.remaining:,} requests left this month" if usage.remaining is not None else None
    low = (
        usage.remaining is not None
        and bool(usage.limit)
        and usage.remaining < (usage.limit or 0) * (1 - NEAR_LIMIT)
    )
    state: State
    detail: str | None
    if expired:
        state, detail = "error", "Its tokens have run out: paste new ones"
    elif tokens.error:
        state, detail = "warning", f"Renewing failed: {tokens.error}"
    else:
        state, detail = ("warning" if low else "ok"), left
    used = (
        min(1.0, max(0.0, 1 - usage.remaining / usage.limit))
        if admin and usage.remaining is not None and usage.limit
        else None
    )
    if not admin:
        detail = None if state == "ok" else "Needs an admin's attention"
    return instance("Data Thistle", state, detail, link, used)


async def instance_connections(
    db: DbDep, http: httpx.AsyncClient, settings: Settings, admin: bool
) -> list[ConnectionStatus]:
    out: list[ConnectionStatus] = []
    gigs = f"{SERVICES}/gigs"

    def simple(name: str, configured: bool, link: str) -> None:
        out.append(
            instance(
                name, "ok" if configured else "off", None if configured else "Not set up", link
            )
        )

    if (await get_jambase_api_key(db, settings))[0]:
        calls = (await get_jambase_usage(db)).calls
        out.append(
            with_limit("JamBase", calls, settings.jambase_monthly_cap, "this month", gigs, admin)
        )
    else:
        simple("JamBase", False, gigs)
    if (await get_ticketmaster_api_key(db, settings))[0]:
        calls = (await get_ticketmaster_usage(db)).calls
        out.append(
            with_limit("Ticketmaster", calls, settings.ticketmaster_daily_cap, "today", gigs, admin)
        )
    else:
        simple("Ticketmaster", False, gigs)
    tokens = await get_datathistle_tokens(db, settings)
    if tokens is None:
        simple("Data Thistle", False, gigs)
    else:
        usage = await get_datathistle_usage(db)
        out.append(data_thistle(tokens, usage, admin, dt.datetime.now(dt.UTC)))
    simple("setlist.fm", (await get_setlistfm_api_key(db, settings))[0] is not None, gigs)
    simple(
        "Last.fm", (await get_lastfm_api_key(db, settings))[0] is not None, "/settings/scrobbling"
    )
    simple("Spotify", await get_spotify_app(db, settings) is not None, f"{SERVICES}/playlists")
    simple(
        "fanart.tv", (await get_fanart_api_key(db, settings))[0] is not None, f"{SERVICES}/artwork"
    )
    simple(
        "AcoustID", (await get_acoustid_api_key(db, settings))[0] is not None, f"{SERVICES}/files"
    )
    searxng = "SearXNG (image search)"
    if not settings.searxng_url:
        out.append(instance(searxng, "off", "Not set up"))
    elif await searxng_healthy(http, settings.searxng_url):
        out.append(instance(searxng, "ok"))
    else:
        out.append(instance(searxng, "error", "Not answering"))
    if not admin:
        for item in out:
            item.link = None  # the Services pages are for admins
    return out


async def your_connections(db: DbDep, user_id: int) -> list[ConnectionStatus]:
    out: list[ConnectionStatus] = []

    def add(
        name: str, state: State, detail: str | None, link: str, subtitle: str | None = None
    ) -> None:
        out.append(
            ConnectionStatus(
                name=name,
                group="yours",
                state=state,
                subtitle=subtitle,
                detail=detail,
                link=link,
            )
        )

    servers = await db.scalars(
        select(UserServer).where(UserServer.user_id == user_id).order_by(UserServer.id)
    )
    for us in servers:
        last = await db.scalar(
            select(SyncRun)
            .where(SyncRun.user_server_id == us.id, SyncRun.status.in_(("succeeded", "failed")))
            .order_by(SyncRun.created_at.desc())
            .limit(1)
        )
        if last is not None and last.status == "failed":
            add("Plex", "error", last.error or "The last sync failed", "/settings", us.server.name)
        else:
            add("Plex", "ok", None, "/settings", us.server.name)
    link = await db.get(ListenBrainzLink, user_id)
    if link is not None:
        state: State = "error" if link.paused else "warning" if link.last_error else "ok"
        add("ListenBrainz", state, link.last_error, "/settings/scrobbling", link.username)
    targets = await db.scalars(
        select(ScrobbleTarget).where(ScrobbleTarget.user_id == user_id).order_by(ScrobbleTarget.id)
    )
    for target in targets:
        state = "error" if target.paused else "warning" if target.last_error else "ok"
        add(
            service_label(target.service),
            state,
            target.last_error,
            "/settings/scrobbling",
            target.username,
        )
    for feed in await db.scalars(select(LiveFeed).where(LiveFeed.user_id == user_id)):
        add(
            "Calendar link",
            "warning" if feed.error else "ok",
            feed.error,
            "/gigs",
            feed.name or feed.host,
        )
    return out


@router.get("/connections", response_model=list[ConnectionStatus])
async def connections(
    db: DbDep, http: HttpDep, settings: SettingsDep, user: UserDep
) -> list[ConnectionStatus]:
    """Every connection, the user's own first."""
    yours = await your_connections(db, user.id)
    return [*yours, *await instance_connections(db, http, settings, user.is_admin)]
