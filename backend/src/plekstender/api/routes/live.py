"""Live: upcoming shows near you by the artists you listen to most.

Each user sets a place, a distance and which of their artists to follow (the top N% of all
time, or of the past months); the worker looks the shows up (about daily, within the
instance's budgets) and they're read here. Starred shows are kept, and are in a private
calendar feed (iCal) the user can subscribe to.
"""

from __future__ import annotations

import datetime as dt
import secrets
from collections import Counter
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import ColumnElement, delete, func, or_, select, update

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    GigShowPlaylistOut,
    LiveArtistCount,
    LiveArtistOut,
    LiveCalendarOut,
    LiveFeedIn,
    LiveFeedOut,
    LiveListenedOut,
    LiveSettingsIn,
    LiveSettingsOut,
    LiveShowOut,
    LiveShowsOut,
    LiveStarIn,
    LiveTicketIn,
    LiveTicketOut,
    LiveVenueCount,
    PlaceOut,
)
from plekstender.config import Settings
from plekstender.db.instance import (
    get_datathistle_tokens,
    get_jambase_api_key,
    get_ticketmaster_api_key,
)
from plekstender.db.models import (
    GigPlaylist,
    LiveFeed,
    LiveSeen,
    LiveSettings,
    LiveShow,
    Playlist,
    User,
)
from plekstender.gigs.playlists import ShowPlaylistError, make_show_playlist, show_refs
from plekstender.live import feeds, geocode, ical
from plekstender.live.merge import merge
from plekstender.live.refresh import (
    DATATHISTLE_KEEP,
    NEW_FOR,
    follow_count,
    listened_artists,
    period_start,
    seen_key,
)
from plekstender.security.crypto import hash_session_token
from plekstender.worker import LIVE_MANUAL_GAP, enqueue_live_refresh, enqueue_playlist_build

router = APIRouter(prefix="/api/live", tags=["live"])

# Settings changes are looked up after a short pause, so a few quick changes cost one refresh.
SETTLE_SECONDS = 20
# A lookup still "queued" or "running" after this long was lost (the worker stopped).
STALE_AFTER = dt.timedelta(minutes=30)
LOST = "The last lookup didn't finish; refresh to try again."
# The calendar keeps starred shows this long after they've happened.
CALENDAR_PAST = dt.timedelta(days=60)


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


Source = Literal["jambase", "ticketmaster", "datathistle"]
# Opening the Live tab looks Data Thistle up again when its listings are older than this
# (they may be kept a day).
DATATHISTLE_AGE = dt.timedelta(hours=20)
DATATHISTLE_RETRY = dt.timedelta(minutes=15)


async def _sources(db: DbDep, settings: Settings) -> list[Source]:
    out: list[Source] = []
    if (await get_jambase_api_key(db, settings))[0]:
        out.append("jambase")
    if (await get_ticketmaster_api_key(db, settings))[0]:
        out.append("ticketmaster")
    if await get_datathistle_tokens(db, settings):
        out.append("datathistle")
    return out


async def _out(db: DbDep, settings: Settings, user: User) -> LiveSettingsOut:
    live = await db.get(LiveSettings, user.id)
    months = live.period_months if live else None
    listened = len(await listened_artists(db, user.id, period_start(months)))
    percent = live.top_percent if live else 10
    sources = await _sources(db, settings)
    has_feeds = bool(
        await db.scalar(
            select(func.count()).select_from(LiveFeed).where(LiveFeed.user_id == user.id)
        )
    )
    stale = live is not None and _stale(live)
    return LiveSettingsOut(
        place_name=live.place_name if live else None,
        latitude=live.latitude if live else None,
        longitude=live.longitude if live else None,
        radius_km=live.radius_km if live else 100,
        top_percent=percent,
        period_months=months,
        listened_artists=listened,
        followed_artists=follow_count(listened, percent),
        refresh_status=("failed" if stale else live.refresh_status) if live else None,
        refresh_error=(LOST if stale else live.refresh_error) if live else None,
        refreshed_at=live.refreshed_at if live else None,
        refresh_available_at=_available_at(live),
        source_ready=bool(sources) or has_feeds,
        sources=sources,
        datathistle_at=live.datathistle_at if live else None,
        datathistle_error=live.datathistle_error if live else None,
    )


@router.get("/settings", response_model=LiveSettingsOut)
async def get_live_settings(db: DbDep, settings: SettingsDep, user: UserDep) -> LiveSettingsOut:
    """The Live tab's settings and status. Opening the tab looks Data Thistle up again
    when its listings are getting old (they may only be kept a day)."""
    live = await db.get(LiveSettings, user.id)
    # Again after a day; after a failed lookup, after a short while.
    wait = DATATHISTLE_RETRY if live is not None and live.datathistle_error else DATATHISTLE_AGE
    if (
        live is not None
        and live.latitude is not None
        and (live.datathistle_at is None or live.datathistle_at < dt.datetime.now(dt.UTC) - wait)
        and await get_datathistle_tokens(db, settings)
    ):
        await enqueue_live_refresh(db, user.id, datathistle="only")
    return await _out(db, settings, user)


@router.get("/listened", response_model=LiveListenedOut)
async def listened(
    db: DbDep,
    user: UserDep,
    period_months: Annotated[int | None, Query(ge=1, le=600)] = None,
) -> LiveListenedOut:
    """How many artists the user played in a period (for the settings form's preview)."""
    found = await listened_artists(db, user.id, period_start(period_months))
    return LiveListenedOut(listened_artists=len(found))


@router.put("/settings", response_model=LiveSettingsOut)
async def put_live_settings(
    body: LiveSettingsIn, db: DbDep, settings: SettingsDep, user: UserDep
) -> LiveSettingsOut:
    """Save the place, distance and artists to follow; the shows are looked up again."""
    live = await db.get(LiveSettings, user.id)
    if live is None:
        live = LiveSettings(user_id=user.id)
        db.add(live)
    before = (live.latitude, live.longitude, live.radius_km, live.top_percent, live.period_months)
    after = (body.latitude, body.longitude, body.radius_km, body.top_percent, body.period_months)
    live.place_name = body.place_name.strip()
    live.latitude, live.longitude = body.latitude, body.longitude
    live.radius_km, live.top_percent = body.radius_km, body.top_percent
    live.period_months = body.period_months
    await db.commit()
    if before != after:
        if live.refreshed_at is not None:
            # The old list was for another place or other artists (starred shows stay).
            await db.execute(
                delete(LiveShow).where(LiveShow.user_id == user.id, LiveShow.starred_at.is_(None))
            )
            live.refreshed_at = None
            await db.commit()
        await enqueue_live_refresh(db, user.id, delay_seconds=SETTLE_SECONDS)
    return await _out(db, settings, user)


@router.delete("/settings", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_settings(db: DbDep, user: UserDep) -> Response:
    """Forget the place, the shows (starred too), the calendar links and address."""
    await db.execute(delete(LiveShow).where(LiveShow.user_id == user.id))
    await db.execute(delete(LiveFeed).where(LiveFeed.user_id == user.id))
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
            f"Gigs were looked up recently; try again after {at:%H:%M} UTC.",
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
    return [
        PlaceOut(name=p.name, latitude=p.latitude, longitude=p.longitude, country=p.country)
        for p in found
    ]


@router.get("/shows", response_model=LiveShowsOut)
async def shows(
    db: DbDep,
    user: UserDep,
    artist: Annotated[str | None, Query(max_length=200)] = None,
    venue: Annotated[str | None, Query(max_length=300)] = None,
    date_from: Annotated[dt.date | None, Query(alias="from")] = None,
    date_to: Annotated[dt.date | None, Query(alias="to")] = None,
    starred: bool = False,
    new: bool = False,
    tickets: bool = False,
) -> LiveShowsOut:
    """Upcoming shows, soonest first, each once however many sources found it; `artist`
    narrows to one of the user's artists, `venue` to one venue, `starred` to starred shows,
    `tickets` to the ones they have a ticket for, `new` to shows first found in the past week.
    The artist and venue lists (for the filters) count the shows the other filters allow."""
    groups = await _upcoming(db, user.id, date_from, date_to)
    if starred:
        groups = [g for g in groups if g.starred]
    if tickets:
        groups = [g for g in groups if g.got_ticket]
    if new:
        groups = [g for g in groups if g.is_new]
    by_artist = (
        [g for g in groups if any(a.name == artist for a in g.artists)] if artist else groups
    )
    by_venue = [g for g in groups if g.venue == venue] if venue else groups
    artist_counts: Counter[str] = Counter(a.name for g in by_venue for a in g.artists)
    venue_counts: Counter[str] = Counter(g.venue for g in by_artist if g.venue)
    city_of = {g.venue: g.city for g in groups if g.venue}
    return LiveShowsOut(
        shows=[g for g in by_artist if not venue or g.venue == venue],
        artists=[
            LiveArtistCount(name=n, shows=c)
            for n, c in sorted(artist_counts.items(), key=lambda x: (-x[1], x[0].lower()))
        ],
        venues=[
            LiveVenueCount(name=n, city=city_of.get(n), shows=c)
            for n, c in sorted(venue_counts.items(), key=lambda x: (-x[1], x[0].lower()))
        ],
    )


async def _upcoming(
    db: DbDep,
    user_id: int,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    *where: ColumnElement[bool],
) -> list[LiveShowOut]:
    """The user's upcoming gigs, soonest first, each once however many sources found it."""
    today = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)  # time zones: keep tonight
    start = max(date_from or today, today)
    query = (
        select(LiveShow)
        .where(
            LiveShow.user_id == user_id,
            func.coalesce(LiveShow.end_date, LiveShow.start_date) >= start,
            _not_expired(),
            *where,
        )
        .order_by(LiveShow.start_date, LiveShow.start_time.nulls_last(), LiveShow.id)
    )
    if date_to:
        query = query.where(LiveShow.start_date <= date_to)
    sightings = await db.execute(
        select(LiveSeen.key, LiveSeen.first_seen_at).where(LiveSeen.user_id == user_id)
    )
    seen = {key: when for key, when in sightings.all()}
    playlists = {
        ref: playlist_id
        for ref, playlist_id in await db.execute(
            select(GigPlaylist.ref, GigPlaylist.playlist_id).where(
                GigPlaylist.user_id == user_id,
                GigPlaylist.kind == "before",
                GigPlaylist.playlist_id.is_not(None),
            )
        )
    }
    out = []
    for group in merge(list(await db.scalars(query))):
        show = _show(group, seen)
        show.playlist_id = next((playlists[r] for r in show_refs(group) if r in playlists), None)
        out.append(show)
    return out


@router.post(
    "/shows/{show_id}/playlist",
    response_model=GigShowPlaylistOut,
    dependencies=[rate_limit("gig-playlist", limit=10, window=60)],
)
async def make_playlist(
    show_id: int, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep
) -> GigShowPlaylistOut:
    """Make (or rebuild) the before-the-gig playlist for this gig now: what the artists have
    been playing lately. Also when it was deleted before; Plex gets it from the worker."""
    try:
        made, playlist_id = await make_show_playlist(db, http, settings, user, show_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gig not found") from exc
    except ShowPlaylistError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    playlist = await db.get(Playlist, playlist_id) if playlist_id else None
    for build_id in made.to_build:
        if (queued := await db.get(Playlist, build_id)) is not None:
            await enqueue_playlist_build(queued)
    setlist = ((playlist.display or {}).get("setlist") or {}) if playlist else {}
    return GigShowPlaylistOut(
        playlist_id=playlist_id,
        songs=len((playlist.definition or {}).get("tracks") or []) if playlist else 0,
        missing=int(setlist.get("missing_count") or 0),
    )


@router.get("/artists/{server_id}/{rating_key}/gigs", response_model=list[LiveShowOut])
async def artist_gigs(
    server_id: int, rating_key: int, db: DbDep, user: UserDep
) -> list[LiveShowOut]:
    """One library artist's upcoming gigs (for their page)."""
    us = await get_user_server(db, user, server_id)
    match = LiveShow.artists.contains([{"server_id": us.server_id, "rating_key": rating_key}])
    return await _upcoming(db, user.id, None, None, match)


@router.get("/artists/{server_id}", response_model=list[int])
async def artists_with_gigs(server_id: int, db: DbDep, user: UserDep) -> list[int]:
    """The library artists (rating keys) with an upcoming gig, to mark their pictures."""
    us = await get_user_server(db, user, server_id)
    keys: set[int] = set()
    for show in await _upcoming(db, user.id):
        keys.update(
            a.rating_key for a in show.artists if a.server_id == us.server_id and a.rating_key
        )
    return sorted(keys)


def _not_expired() -> ColumnElement[bool]:
    """Data Thistle's listings older than a day aren't shown (their terms)."""
    return or_(
        LiveShow.source != "datathistle",
        LiveShow.fetched_at >= dt.datetime.now(dt.UTC) - DATATHISTLE_KEEP,
    )


def _show(group: list[LiveShow], seen: dict[str, dt.datetime] | None = None) -> LiveShowOut:
    """One show from its sources' rows (the first is preferred for the details); `seen`
    gives when each was first found (the earliest counts)."""
    row = group[0]
    sightings = [s for r in group if seen and (s := seen.get(seen_key(r.source, r.source_id)))]
    first_seen = min(sightings) if sightings else None
    artists: dict[str, LiveArtistOut] = {}
    for r in group:
        for a in r.artists:  # one entry per artist (on several servers / sources: once)
            artists.setdefault(a["name"], LiveArtistOut.model_validate(a))
    tickets: dict[str, LiveTicketOut] = {}
    for r in group:
        if url := (r.ticket_url or r.page_url):
            tickets.setdefault(
                url,
                LiveTicketOut(
                    source=r.source, url=url, seller=r.seller, is_ticket=bool(r.ticket_url)
                ),
            )
    performers = max((r.performers for r in group), key=len)
    priced = next((r for r in group if r.price_min is not None), None)
    located = next((r for r in group if r.latitude is not None), row)
    return LiveShowOut(
        id=row.id,
        ids=[r.id for r in group],
        source=row.source,
        sources=[r.source for r in group],
        starred=any(r.starred_at for r in group),
        got_ticket=any(r.ticket_at for r in group),
        first_seen_at=first_seen,
        is_new=first_seen is not None and first_seen >= dt.datetime.now(dt.UTC) - NEW_FOR,
        tickets=list(tickets.values()),
        name=row.name,
        festival=any(r.festival for r in group),
        start_date=row.start_date,
        start_time=row.start_time or next((r.start_time for r in group if r.start_time), None),
        end_date=row.end_date,
        status=row.status,
        venue=row.venue,
        city=row.city,
        region=row.region,
        country=row.country,
        distance_km=located.distance_km,
        ticket_url=row.ticket_url,
        seller=row.seller,
        page_url=row.page_url,
        price_min=priced.price_min if priced else None,
        price_max=priced.price_max if priced else None,
        currency=priced.currency if priced else None,
        artists=sorted(artists.values(), key=lambda a: not a.headliner),
        performers=[LiveArtistOut.model_validate(p) for p in performers],
    )


@router.put("/stars", status_code=status.HTTP_204_NO_CONTENT)
async def star(body: LiveStarIn, db: DbDep, user: UserDep) -> Response:
    """Star (or unstar) a show: all its sources' rows, so it stays starred whichever is
    listed first. Unstarring forgets the ticket too."""
    rows = update(LiveShow).where(LiveShow.user_id == user.id, LiveShow.id.in_(body.ids))
    if body.starred:
        rows = rows.values(starred_at=func.coalesce(LiveShow.starred_at, dt.datetime.now(dt.UTC)))
    else:
        rows = rows.values(starred_at=None, ticket_at=None)
    await db.execute(rows)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/tickets", status_code=status.HTTP_204_NO_CONTENT)
async def got_ticket(body: LiveTicketIn, db: DbDep, user: UserDep) -> Response:
    """Mark (or unmark) that the user has a ticket for a show; marking stars it too."""
    now = dt.datetime.now(dt.UTC)
    rows = update(LiveShow).where(LiveShow.user_id == user.id, LiveShow.id.in_(body.ids))
    if body.got_ticket:
        rows = rows.values(
            ticket_at=func.coalesce(LiveShow.ticket_at, now),
            starred_at=func.coalesce(LiveShow.starred_at, now),
        )
    else:
        rows = rows.values(ticket_at=None)
    await db.execute(rows)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- calendar links (iCal) as a source ---------------------------------------------------

MAX_FEEDS = 5


def _feed_out(feed: LiveFeed) -> LiveFeedOut:
    return LiveFeedOut(
        id=feed.id,
        host=feed.host,
        name=feed.name,
        fetched_at=feed.fetched_at,
        error=feed.error,
        show_count=feed.show_count,
    )


@router.get("/feeds", response_model=list[LiveFeedOut])
async def list_feeds(db: DbDep, user: UserDep) -> list[LiveFeedOut]:
    rows = await db.scalars(
        select(LiveFeed).where(LiveFeed.user_id == user.id).order_by(LiveFeed.id)
    )
    return [_feed_out(f) for f in rows]


@router.post(
    "/feeds",
    response_model=LiveFeedOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("live-feeds", 20, 60)],
)
async def add_feed(
    body: LiveFeedIn, db: DbDep, http: HttpDep, cipher: CipherDep, user: UserDep
) -> LiveFeedOut:
    """Add a calendar link (checked by reading it once); its shows are looked up next."""
    live = await db.get(LiveSettings, user.id)
    if live is None or live.latitude is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose a place first.")
    count = await db.scalar(
        select(func.count()).select_from(LiveFeed).where(LiveFeed.user_id == user.id)
    )
    if (count or 0) >= MAX_FEEDS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"At most {MAX_FEEDS} calendar links.")
    try:
        url = feeds.normalise_url(body.url)
        text = await feeds.read(http, url)
    except feeds.FeedError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    feed = LiveFeed(
        user_id=user.id,
        url_enc=cipher.encrypt(url, purpose=feeds.PURPOSE),
        host=(urlsplit(url).hostname or "").removeprefix("www."),
        name=feeds.calendar_name(text),
    )
    db.add(feed)
    await db.commit()
    await enqueue_live_refresh(db, user.id, delay_seconds=SETTLE_SECONDS)
    await db.refresh(feed)
    return _feed_out(feed)


@router.delete("/feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_feed(feed_id: int, db: DbDep, user: UserDep) -> Response:
    """Remove a calendar link and its shows (starred ones stay)."""
    feed = await db.get(LiveFeed, feed_id)
    if feed is None or feed.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Calendar link not found")
    await db.execute(
        delete(LiveShow).where(
            LiveShow.user_id == user.id,
            LiveShow.source == "calendar",
            LiveShow.source_id.startswith(f"{feed_id}:"),
            LiveShow.starred_at.is_(None),
        )
    )
    await db.delete(feed)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the calendar of starred shows ---------------------------------------------------------


def _calendar_url(settings: Settings, token: str) -> str:
    return f"{settings.app_url}/api/live/calendar/{token}.ics"


@router.get("/calendar", response_model=LiveCalendarOut)
async def calendar_address(db: DbDep, user: UserDep) -> LiveCalendarOut:
    """Whether the user has a calendar address, and since when (the address itself is only
    shown when it's made)."""
    live = await db.get(LiveSettings, user.id)
    exists = live is not None and live.calendar_token_hash is not None
    return LiveCalendarOut(exists=exists, created_at=live.calendar_created_at if live else None)


@router.post("/calendar", response_model=LiveCalendarOut)
async def new_calendar_address(db: DbDep, settings: SettingsDep, user: UserDep) -> LiveCalendarOut:
    """A new private address for the calendar of starred shows, shown this once. An earlier
    address stops working."""
    live = await db.get(LiveSettings, user.id)
    if live is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose a place first.")
    token = secrets.token_urlsafe(24)
    live.calendar_token_hash = hash_session_token(token)
    live.calendar_created_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return LiveCalendarOut(
        exists=True, created_at=live.calendar_created_at, url=_calendar_url(settings, token)
    )


def _calendar_shows(groups: list[list[LiveShow]]) -> list[ical.CalendarShow]:
    out = []
    for group in groups:
        show = _show(group)
        names = [a.name for a in show.artists]
        title = show.name if show.festival else " + ".join(names) or show.name
        others = [p.name for p in show.performers if p.name not in names]
        where = ", ".join(x for x in (show.venue, show.city) if x)
        lines = ["You have a ticket."] if show.got_ticket else []
        if others:
            lines.append("With " + ", ".join(others[:10]))
        lines += [f"Tickets ({t.seller or t.source}): {t.url}" for t in show.tickets]
        out.append(
            ical.CalendarShow(
                uid=f"{group[0].source_id}@plekstender",
                title=title,
                start_date=show.start_date,
                start_time=show.start_time,
                end_date=show.end_date,
                status=show.status,
                got_ticket=show.got_ticket,
                location=where or None,
                url=show.tickets[0].url if show.tickets else None,
                description="\n".join(lines),
                updated=max(r.fetched_at for r in group),
            )
        )
    return out


async def _starred(db: DbDep, user_id: int) -> list[list[LiveShow]]:
    since = dt.datetime.now(dt.UTC).date() - CALENDAR_PAST
    rows = list(
        await db.scalars(
            select(LiveShow)
            .where(
                LiveShow.user_id == user_id,
                func.coalesce(LiveShow.end_date, LiveShow.start_date) >= since,
                _not_expired(),
            )
            .order_by(LiveShow.start_date, LiveShow.id)
        )
    )
    # Merge first, so a show starred under one source is one event.
    return [g for g in merge(rows) if any(r.starred_at for r in g)]


@router.get(
    "/calendar/{token}.ics",
    response_class=Response,
    dependencies=[rate_limit("live-calendar", 120, 60)],
    responses={200: {"content": {"text/calendar": {}}}},
)
async def calendar_feed(token: str, db: DbDep) -> Response:
    """The starred shows (no sign-in: calendar apps fetch it; the address is the secret)."""
    hashed = hash_session_token(token)
    live = await db.scalar(select(LiveSettings).where(LiveSettings.calendar_token_hash == hashed))
    if live is None or len(token) < 20:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown calendar")
    body = ical.calendar(_calendar_shows(await _starred(db, live.user_id)), "Plekstender: gigs")
    return Response(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "private, max-age=900"},
    )


@router.get(
    "/shows/{show_id}.ics",
    response_class=Response,
    responses={200: {"content": {"text/calendar": {}}}},
)
async def show_file(show_id: int, db: DbDep, user: UserDep) -> Response:
    """One show as a calendar file, to add it to a calendar by hand."""
    row = await db.get(LiveShow, show_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gig not found")
    same = list(
        await db.scalars(
            select(LiveShow).where(
                LiveShow.user_id == user.id, LiveShow.start_date == row.start_date
            )
        )
    )
    group = next((g for g in merge(same) if row in g), [row])
    body = ical.calendar(_calendar_shows([group]), row.name)
    return Response(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="gig-{show_id}.ics"'},
    )
