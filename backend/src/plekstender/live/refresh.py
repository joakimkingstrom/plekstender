"""Looking up a user's upcoming shows: their most-listened artists, near their place.

Runs in the worker, one source after the other (JamBase, then Ticketmaster), each with its
own budget: every request is counted before it's sent and stops at the instance's cap
(JAMBASE_MONTHLY_CAP a month, TICKETMASTER_DAILY_CAP a day). A source's shows are replaced
only after a complete run of that source, so a run cut short keeps its last list.
"""

from __future__ import annotations

import datetime as dt
import difflib
import logging
import math
import re
from collections.abc import Awaitable, Callable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from functools import partial

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plekstender.api.routes.library import plays_of, visible
from plekstender.config import Settings
from plekstender.db.instance import (
    add_jambase_unknown_ids,
    get_jambase_api_key,
    get_jambase_unknown_ids,
    get_jambase_usage,
    get_ticketmaster_api_key,
    reserve_jambase_call,
    reserve_ticketmaster_call,
    save_jambase_quota,
)
from plekstender.db.models import (
    Artist,
    ArtistArtwork,
    Listen,
    LiveFeed,
    LiveSettings,
    LiveShow,
    UserServer,
)
from plekstender.live import feeds, jambase, ticketmaster
from plekstender.live.events import Event
from plekstender.live.geocode import distance_km
from plekstender.security.crypto import DecryptionError, TokenCipher
from plekstender.sync.parse import normalise_artist

log = logging.getLogger(__name__)

# Pages per JamBase batch at most (100 shows a page): a safety net against runaway paging.
MAX_PAGES = 3
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SKIP_NAMES = {"various artists", "various", "unknown artist", "[unknown]"}
FUZZY = 0.88
# Retries of a batch without an ID JamBase didn't know, before searching the rest by name.
ID_RETRIES = 3
# Ticketmaster: windows of this many days (split when one has more than 1,000 shows), and
# at most this many requests a refresh.
TM_WINDOW_DAYS = 30
TM_MAX_REQUESTS = 80


@dataclass
class Followed:
    """An artist the user follows, merged across their servers."""

    name: str
    mbid: str | None
    plays: int
    refs: list[tuple[int, int]] = field(default_factory=list)  # (server_id, rating_key)


def follow_count(listened: int, percent: int) -> int:
    """How many artists the top `percent`% of `listened` is (at least one)."""
    if listened <= 0:
        return 0
    return max(1, math.ceil(listened * percent / 100))


def period_start(months: int | None, now: dt.datetime | None = None) -> dt.datetime | None:
    """The start of "the past `months` months" (None: all time)."""
    if not months:
        return None
    now = now or dt.datetime.now(dt.UTC)
    year, month = divmod(now.year * 12 + now.month - 1 - months, 12)
    day = min(now.day, 28)
    return now.replace(year=year, month=month + 1, day=day)


async def listened_artists(
    db: AsyncSession, user_id: int, since: dt.datetime | None = None
) -> list[Followed]:
    """Every artist in the user's libraries they've played, most played first (merged across
    servers by MusicBrainz ID, else by name). All time counts Plex's plays or the listen
    history, whichever is higher; a period (`since`) counts the listens in it."""
    servers = list(await db.scalars(select(UserServer).where(UserServer.user_id == user_id)))
    recent = None
    if since is not None:
        recent = (
            select(Listen.artist_key, func.count().label("n"))
            .where(
                Listen.user_id == user_id,
                Listen.duplicate_of.is_(None),
                Listen.listened_at >= since,
            )
            .group_by(Listen.artist_key)
            .subquery()
        )
    merged: dict[str, Followed] = {}
    for us in servers:
        state, cond, where = visible(Artist, user_id, us)
        work = ArtistArtwork
        plays = recent.c.n if recent is not None else plays_of(state)
        query = (
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
        )
        if recent is not None:
            query = query.join(recent, recent.c.artist_key == Artist.name_key)
        rows = await db.execute(query.where(*where, plays > 0))
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
    db: AsyncSession, user_id: int, percent: int, since: dt.datetime | None = None
) -> tuple[list[Followed], int]:
    """The artists to look up (the top `percent`%) and how many the user has played."""
    listened = await listened_artists(db, user_id, since)
    return listened[: follow_count(len(listened), percent)], len(listened)


def batches(
    artists: list[Followed], unknown: AbstractSet[str] = frozenset()
) -> list[list[Followed]]:
    """JamBase requests: artists with a MusicBrainz ID by ID, the rest (and those whose ID
    JamBase didn't know) by name."""
    with_id = [a for a in artists if a.mbid and a.mbid not in unknown]
    by_name = [replace(a, mbid=None) for a in artists if a not in with_id]
    size = jambase.BATCH
    return [with_id[i : i + size] for i in range(0, len(with_id), size)] + [
        by_name[i : i + size] for i in range(0, len(by_name), size)
    ]


class Matcher:
    """Finds which of the user's artists a performer is: by MusicBrainz ID when the source
    gives one, else by name (normalised; optionally a close match too)."""

    def __init__(self, artists: list[Followed], fuzzy: bool = True) -> None:
        self.by_mbid = {a.mbid: a for a in artists if a.mbid}
        self.by_key: dict[str, Followed] = {}
        for a in artists:
            self.by_key.setdefault(normalise_artist(a.name), a)
        self.fuzzy = fuzzy

    def __call__(self, name: str, mbid: str | None = None) -> Followed | None:
        if mbid and (found := self.by_mbid.get(mbid.lower())):
            return found
        key = normalise_artist(name)
        if found := self.by_key.get(key):
            return found
        if not self.fuzzy:
            return None
        close = difflib.get_close_matches(key, list(self.by_key), n=1, cutoff=FUZZY)
        return self.by_key[close[0]] if close else None


def match(performer: str, batch: list[Followed]) -> Followed | None:
    return Matcher(batch)(performer)


class Stop(Exception):
    """A source can't go on (no budget left, key refused, quota used up)."""


Found = dict[str, tuple[Event, dict[tuple[int, int], dict[str, object]]]]


@dataclass
class Context:
    sessions: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    settings: Settings
    now: Callable[[], dt.datetime]
    started: dt.datetime
    latitude: float
    longitude: float
    radius_km: int
    artists: list[Followed]

    @property
    def today(self) -> dt.date:
        return self.started.date()


@dataclass
class SourceRun:
    found: Found = field(default_factory=dict)
    requests: int = 0
    error: str | None = None
    # Shows whose `source_id` starts with one of these aren't cleaned up (a calendar link
    # that couldn't be read keeps its last shows).
    keep: list[str] = field(default_factory=list)


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
    """Look up the user's shows from every source that has a key, and store them. Records
    the outcome on their settings."""
    started = now()
    async with sessions() as db:
        live = await db.get(LiveSettings, user_id)
        if live is None or live.latitude is None or live.longitude is None:
            return Result(0, 0, 0, complete=False, error="No place is set.")
        live.refresh_status, live.refresh_error = "running", None
        await db.commit()
        since = period_start(live.period_months, started)
        artists, _ = await followed_artists(db, user_id, live.top_percent, since)
        ctx = Context(
            sessions, http, settings, now, started,
            live.latitude, live.longitude, live.radius_km, artists,
        )  # fmt: skip
        jambase_key, _ = await get_jambase_api_key(db, settings)
        tm_key, _ = await get_ticketmaster_api_key(db, settings)
        feed_ids = list(await db.scalars(select(LiveFeed.id).where(LiveFeed.user_id == user_id)))
        everyone = await listened_artists(db, user_id) if feed_ids else []

    runs: dict[str, SourceRun] = {}
    if jambase_key:
        runs["jambase"] = await _run(partial(_jambase, ctx, jambase_key))
    if tm_key:
        runs["ticketmaster"] = await _run(partial(_ticketmaster, ctx, tm_key))
    if feed_ids:
        runs["calendar"] = await _run(partial(_calendars, ctx, feed_ids, everyone))
    errors = [run.error for run in runs.values() if run.error]
    if not runs:
        errors = ["No source for shows is set up: an admin can add a JamBase or Ticketmaster "
                  "key in Settings → Connections, or add a calendar link."]  # fmt: skip
    complete = not errors

    async with sessions() as db:
        for source, run in runs.items():
            if run.found:
                await _store(db, user_id, source, run.found, ctx, now())
            if not run.error:
                # Shows not seen in a complete run are gone (cancelled, moved, out of range).
                await db.execute(
                    delete(LiveShow).where(
                        LiveShow.user_id == user_id,
                        LiveShow.source == source,
                        LiveShow.fetched_at < started,
                        LiveShow.starred_at.is_(None),
                        *(~LiveShow.source_id.startswith(prefix) for prefix in run.keep),
                    )
                )
        live = await db.get(LiveSettings, user_id)
        if live is not None:
            live.refresh_status = "done" if complete else "failed"
            live.refresh_error = " ".join(errors) or None
            # The JamBase requests a refresh takes (the daily schedule budgets with it).
            live.request_count = runs["jambase"].requests if "jambase" in runs else 0
            live.artist_count = len(artists)
            if complete or any(not run.error for run in runs.values()):
                live.refreshed_at = now()
        await db.commit()
    for message in errors:
        log.warning("Live refresh for user %s: %s", user_id, message)
    return Result(
        sum(len(run.found) for run in runs.values()),
        len(artists),
        sum(run.requests for run in runs.values()),
        complete,
        " ".join(errors) or None,
    )


async def _run(work: Callable[[SourceRun], Awaitable[None]]) -> SourceRun:
    run = SourceRun()
    try:
        await work(run)
    except Stop as exc:
        run.error = str(exc)
    except (jambase.JamBaseError, ticketmaster.TicketmasterError) as exc:
        run.error = str(exc)
    return run


async def _jambase(ctx: Context, api_key: str, run: SourceRun) -> None:
    """The user's artists, 20 a request (by MusicBrainz ID, else by name)."""
    async with ctx.sessions() as db:
        unknown = await get_jambase_unknown_ids(db, ctx.started)
    todo = batches(ctx.artists, unknown)
    await _check_quota(ctx, api_key, len(todo))
    until = ctx.today + dt.timedelta(days=jambase.DAYS_AHEAD)
    newly_unknown: set[str] = set()

    async def fetch(batch: list[Followed]) -> None:
        match = Matcher(batch)
        page, pages = 1, 1
        while page <= min(pages, MAX_PAGES):
            async with ctx.sessions() as db:
                if not await reserve_jambase_call(db, ctx.settings.jambase_monthly_cap, ctx.now()):
                    raise Stop(
                        f"This month's JamBase requests ({ctx.settings.jambase_monthly_cap}) "
                        "are used up; shows are looked up again next month."
                    )
            run.requests += 1
            events, pages = await jambase.events(
                ctx.http,
                ctx.settings.jambase_api_url,
                api_key,
                latitude=ctx.latitude,
                longitude=ctx.longitude,
                radius_km=ctx.radius_km,
                date_from=ctx.today,
                date_to=until,
                artist_ids=[f"musicbrainz:{a.mbid}" for a in batch if a.mbid] or None,
                artist_names=[a.name for a in batch if not a.mbid] or None,
                page=page,
            )
            for event in events:
                _collect(run.found, event, match)
            page += 1

    try:
        # JamBase refuses a whole request when it doesn't know an ID in it, naming each
        # one: leave those out and try again, and look those artists up by name.
        by_name: list[Followed] = []
        queue = [(batch, 0) for batch in todo]
        while queue:
            batch, retries = queue.pop(0)
            try:
                await fetch(batch)
            except jambase.JamBaseRequestError as exc:
                if not batch[0].mbid:
                    raise
                named = {
                    i.removeprefix("musicbrainz:")
                    for i in getattr(exc, "identifiers", [])
                    if i.startswith("musicbrainz:")
                }
                culprits = [a for a in batch if a.mbid in named]
                if not culprits:
                    # Refused without naming an ID: search everyone by name this time.
                    log.warning("JamBase refused the artist IDs (%s); searching by name", exc)
                    by_name.extend(batch)
                    for rest_batch, _ in queue:
                        if rest_batch[0].mbid:
                            by_name.extend(rest_batch)
                    queue = [(b, r) for b, r in queue if not b[0].mbid]
                    continue
                if retries >= ID_RETRIES:
                    log.warning("JamBase refused artist IDs (%s); searching by name", exc)
                    by_name.extend(batch)
                    continue
                newly_unknown.update(a.mbid for a in culprits if a.mbid)
                by_name.extend(culprits)
                rest = [a for a in batch if a not in culprits]
                if rest:
                    queue.insert(0, (rest, retries + 1))
        for batch in batches([replace(a, mbid=None) for a in by_name]):
            await fetch(batch)
    finally:
        async with ctx.sessions() as db:
            await add_jambase_unknown_ids(db, newly_unknown, ctx.started)
            await db.commit()


async def _ticketmaster(ctx: Context, api_key: str, run: SourceRun) -> None:
    """All music events near the user, a month at a time, matched against their artists."""
    if not ctx.artists:
        return
    match = Matcher(ctx.artists, fuzzy=False)  # many unrelated shows: exact names only
    end = ctx.today + dt.timedelta(days=jambase.DAYS_AHEAD)
    windows: list[tuple[dt.date, dt.date]] = []
    day = ctx.today
    while day <= end:
        last = min(end, day + dt.timedelta(days=TM_WINDOW_DAYS - 1))
        windows.append((day, last))
        day = last + dt.timedelta(days=1)

    while windows:
        start, last = windows.pop(0)
        page, pages = 0, 1
        while page < pages:
            await _tm_reserve(ctx, run)
            events, total, pages = await ticketmaster.events(
                ctx.http,
                ctx.settings.ticketmaster_api_url,
                api_key,
                latitude=ctx.latitude,
                longitude=ctx.longitude,
                radius_km=ctx.radius_km,
                start=start,
                end=last,
                page=page,
            )
            if page == 0 and total > ticketmaster.MAX_ITEMS and last > start:
                # More than can be paged through: read the halves instead.
                middle = start + (last - start) // 2
                windows[:0] = [(start, middle), (middle + dt.timedelta(days=1), last)]
                break
            for event in events:
                _collect(run.found, event, match)
            page += 1
            if (page + 1) * ticketmaster.PAGE_SIZE > ticketmaster.MAX_ITEMS:
                break


async def _tm_reserve(ctx: Context, run: SourceRun) -> None:
    if run.requests >= TM_MAX_REQUESTS:
        raise Stop(
            "There are too many shows near you to read them all from Ticketmaster; "
            "a smaller distance helps."
        )
    async with ctx.sessions() as db:
        if not await reserve_ticketmaster_call(db, ctx.settings.ticketmaster_daily_cap, ctx.now()):
            raise Stop("Today's Ticketmaster requests are used up; it's tried again tomorrow.")
    run.requests += 1


async def _calendars(
    ctx: Context, feed_ids: list[int], everyone: list[Followed], run: SourceRun
) -> None:
    """The user's calendar links: every upcoming event in them (it's their own calendar),
    linked to their artists where the billing names them."""
    cipher = TokenCipher(ctx.settings.derive_key("token-encryption"))
    match = Matcher(everyone, fuzzy=False)
    for feed_id in feed_ids:
        async with ctx.sessions() as db:
            feed = await db.get(LiveFeed, feed_id)
            if feed is None:
                continue
            try:
                url = cipher.decrypt(feed.url_enc, purpose=feeds.PURPOSE)
            except DecryptionError:
                feed.error = "This link can't be read any more; remove it and add it again."
                await db.commit()
                run.keep.append(f"{feed_id}:")
                continue
        try:
            text = await feeds.read(ctx.http, url)
            found = feeds.events(text, feed_id, ctx.today)
            error = None
        except feeds.FeedError as exc:
            found, error = [], str(exc)
            run.keep.append(f"{feed_id}:")
        for event in found:
            _collect(run.found, event, match, keep_unmatched=True, whole=feeds.acts(event.name)[0])
        async with ctx.sessions() as db:
            feed = await db.get(LiveFeed, feed_id)
            if feed is not None:
                feed.error = error
                if error is None:
                    feed.fetched_at, feed.show_count = ctx.now(), len(found)
                await db.commit()


async def _check_quota(ctx: Context, api_key: str, needed: int) -> None:
    """Ask JamBase (free) how much is left, and stop before going over its quota."""
    try:
        quota = await jambase.quota(ctx.http, ctx.settings.jambase_api_url, api_key)
    except jambase.JamBaseKeyError as exc:
        raise Stop(str(exc)) from exc
    except jambase.JamBaseError:
        return  # the counter and the cap still apply
    async with ctx.sessions() as db:
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
    found: Found,
    event: Event,
    match: Matcher,
    *,
    keep_unmatched: bool = False,
    whole: str | None = None,
) -> None:
    matched: dict[tuple[int, int], dict[str, object]] = {}
    # A calendar event's whole billing may be one name ("Florence and the Machine").
    if whole and (artist := match(whole)):
        performers = [replace(event.performers[0], name=whole)] if event.performers else []
        event = replace(event, performers=performers)
    for performer in event.performers:
        if artist := match(performer.name, performer.mbid):
            for server_id, rating_key in artist.refs:
                matched[(server_id, rating_key)] = {
                    "name": artist.name,
                    "headliner": performer.headliner,
                    "server_id": server_id,
                    "rating_key": rating_key,
                }
    if not matched and not keep_unmatched:
        return  # nobody the user follows (or a name search's look-alike)
    if event.id in found:
        found[event.id][1].update(matched)
    else:
        found[event.id] = (event, matched)


async def _store(
    db: AsyncSession,
    user_id: int,
    source: str,
    found: Found,
    ctx: Context,
    fetched_at: dt.datetime,
) -> None:
    rows = []
    for event, matched in found.values():
        distance = (
            round(distance_km(ctx.latitude, ctx.longitude, event.latitude, event.longitude), 1)
            if event.latitude is not None and event.longitude is not None
            else None
        )
        rows.append(
            {
                "user_id": user_id,
                "source": source,
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
    left of the month's JamBase requests (so the budget lasts the month). Someone who
    follows many artists costs more, so they're refreshed less often when it's tight."""
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
        cost = request_count or max(1, math.ceil((artist_count or 150) / jambase.BATCH))
        # The first refresh of the day always goes, if the month has room for it.
        if (chosen and spent + cost > allowance) or cost > left:
            continue
        chosen.append(user_id)
        spent += cost
    return chosen
