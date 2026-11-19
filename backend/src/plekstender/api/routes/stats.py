"""Listening stats for the signed-in user."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.auth import load_preferences
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    ActivityPoint,
    SkippedItemOut,
    SkipsOut,
    StatsActivityOut,
    StatsOverviewOut,
    StatsPeriodOut,
    StatsTotals,
    TopItemOut,
)
from plekstender.db.models import Listen
from plekstender.history import stats
from plekstender.history.stats import RANGE_PATTERN, Period, StatsError

router = APIRouter(prefix="/api/stats", tags=["stats"])

RangeQuery = Annotated[str, Query(pattern=RANGE_PATTERN, description="7d, 30d, 365d, y2024, all")]
TzQuery = Annotated[str, Query(max_length=64, description="IANA time zone, e.g. Europe/London")]
TOP_LIMIT = 10


def _period(range_key: str, tz_name: str) -> tuple[Period, ZoneInfo]:
    try:
        tz = stats.zone(tz_name)
        return stats.resolve_period(range_key, tz), tz
    except StatsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _top_out(item: stats.Top) -> TopItemOut:
    return TopItemOut(
        name=item.name,
        artist=item.artist,
        listens=item.listens,
        listening_ms=item.listening_ms,
        previous_rank=item.previous_rank,
        rating_key=item.rating_key,
        thumb=item.thumb,
    )


@router.get("/overview", response_model=StatsOverviewOut)
async def overview(
    db: DbDep,
    user: UserDep,
    range: RangeQuery = "30d",
    tz: TzQuery = "UTC",
    server_id: Annotated[int | None, Query(description="Link top items to this server.")] = None,
    by: Annotated[
        stats.TopBy, Query(description="Rank top lists by listens or by time.")
    ] = "listens",
) -> StatsOverviewOut:
    period, zone = _period(range, tz)
    library = await get_user_server(db, user, server_id) if server_id is not None else None

    totals = await stats.totals(db, user.id, period.start, period.end)
    previous = (
        await stats.totals(db, user.id, period.previous_start, period.start)
        if period.previous_start and period.start
        else None
    )
    tops: dict[Literal["artist", "album", "track"], list[stats.Top]] = {}
    for kind in ("artist", "album", "track"):
        items = await stats.top(db, user.id, kind, period.start, period.end, TOP_LIMIT, by)
        if period.previous_start and period.start:
            await stats.previous_ranks(
                db, user.id, kind, period.previous_start, period.start, items, by
            )
        if library is not None:
            await stats.link_library(db, user.id, library.server_id, kind, items)
        tops[kind] = items

    days = await stats.listening_days(db, user.id, zone)
    current, longest = stats.streaks(days, dt.datetime.now(zone).date())
    first = await db.scalar(
        select(func.min(Listen.listened_at)).where(
            Listen.user_id == user.id, Listen.duplicate_of.is_(None)
        )
    )
    return StatsOverviewOut(
        period=StatsPeriodOut(
            key=period.key,
            start=period.start,
            end=period.end,
            previous_start=period.previous_start,
        ),
        totals=StatsTotals(**totals),
        previous=StatsTotals(**previous) if previous else None,
        new_artists=await stats.new_artists(db, user.id, period.start, period.end),
        top_artists=[_top_out(i) for i in tops["artist"]],
        top_albums=[_top_out(i) for i in tops["album"]],
        top_tracks=[_top_out(i) for i in tops["track"]],
        current_streak=current,
        longest_streak=longest,
        first_listen_at=first,
    )


@router.get("/activity", response_model=StatsActivityOut)
async def activity(
    db: DbDep, user: UserDep, range: RangeQuery = "30d", tz: TzQuery = "UTC"
) -> StatsActivityOut:
    period, zone = _period(range, tz)
    points, grid = await stats.activity(db, user.id, period, zone)
    return StatsActivityOut(
        bucket=period.bucket,
        points=[ActivityPoint(start=d, listens=n) for d, n in points],
        heatmap=grid,
    )


@router.get("/skips", response_model=SkipsOut)
async def skips(
    db: DbDep,
    user: UserDep,
    server_id: int,
    range: RangeQuery = "30d",
    tz: TzQuery = "UTC",
    kind: Literal["artist", "album", "track"] = "track",
) -> SkipsOut:
    """Most skipped songs, albums or artists (needs the session watcher's live data)."""
    period, _ = _period(range, tz)
    us = await get_user_server(db, user, server_id)
    threshold = load_preferences(user).skip_threshold
    found = await stats.skipped(
        db, user.id, us.server_id, kind, period.start, period.end, threshold
    )
    return SkipsOut(
        threshold=threshold,
        watching=await stats.has_sessions(db, user.id),
        items=[SkippedItemOut(**s.__dict__) for s in found],
    )
