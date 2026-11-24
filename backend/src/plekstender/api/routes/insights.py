"""Looking back over the whole history: on this day, taste over time, and rediscover."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    DayArtistOut,
    DayTrackOut,
    DayYearOut,
    OnThisDayOut,
    RediscoverListOut,
    RediscoverOut,
    RediscoverSongOut,
    ShareOut,
    TasteOut,
)
from plekstender.history import insights
from plekstender.history.stats import StatsError, zone

router = APIRouter(prefix="/api/stats", tags=["stats"])

TzQuery = Annotated[str, Query(max_length=64, description="IANA time zone, e.g. Europe/London")]


def _zone(name: str) -> ZoneInfo:
    try:
        return zone(name)
    except StatsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/on-this-day", response_model=OnThisDayOut)
async def on_this_day(
    db: DbDep, user: UserDep, tz: TzQuery = "UTC", server_id: int | None = None
) -> OnThisDayOut:
    """What you played on today's date in earlier years (artists linked to `server_id`)."""
    tzinfo = _zone(tz)
    library = (await get_user_server(db, user, server_id)).server_id if server_id else None
    today = dt.datetime.now(tzinfo).date()
    years = await insights.on_this_day(db, user.id, library, tzinfo, today)
    return OnThisDayOut(
        date=today,
        years=[
            DayYearOut(
                year=y.year,
                listens=y.listens,
                artists=[DayArtistOut(**asdict(a)) for a in y.artists],
                tracks=[DayTrackOut(**asdict(t)) for t in y.tracks],
                album=y.album,
                album_rating_key=y.album_rating_key,
                album_thumb=y.album_thumb,
            )
            for y in years
        ],
    )


@router.get("/taste", response_model=TasteOut)
async def taste(db: DbDep, user: UserDep, server_id: int, tz: TzQuery = "UTC") -> TasteOut:
    """Your genres and the decades your music is from, per year you listened."""
    tzinfo = _zone(tz)
    us = await get_user_server(db, user, server_id)
    found = await insights.taste(db, user.id, us.server_id, tzinfo)
    return TasteOut(
        years=found.years,
        listens=found.listens,
        genres=[ShareOut(name=g.name, shares=g.shares) for g in found.genres],
        genre_matched=found.genre_matched,
        decades=[ShareOut(name=d.name, shares=d.shares) for d in found.decades],
        decade_matched=found.decade_matched,
        median_release=found.median_release,
    )


@router.get("/rediscover", response_model=RediscoverOut)
async def rediscover(db: DbDep, user: UserDep, server_id: int) -> RediscoverOut:
    """Songs whose ratings and plays disagree: loved but forgotten or hardly played, and
    played a lot but rated low."""
    us = await get_user_server(db, user, server_id)
    sections = list(us.music_section_keys)
    lists: dict[str, RediscoverListOut] = {}
    kinds: tuple[insights.RediscoverKind, ...] = ("forgotten", "unplayed", "disliked")
    for kind in kinds:
        songs, total = await insights.rediscover(db, user.id, us.server_id, sections, kind)
        lists[kind] = RediscoverListOut(
            total=total, items=[RediscoverSongOut(**asdict(s)) for s in songs]
        )
    return RediscoverOut(
        forgotten=lists["forgotten"],
        unplayed=lists["unplayed"],
        disliked=lists["disliked"],
        loved=insights.LOVED,
        disliked_below=insights.DISLIKED,
        forgotten_days=insights.FORGOTTEN_AFTER.days,
        hardly_played=insights.HARDLY_PLAYED,
        played_a_lot=insights.PLAYED_A_LOT,
    )
