"""Gigs the user went to: listed, added and edited by hand, or taken from Live's gigs."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import extract, func, select

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.live import _show
from plekstender.api.schemas import GigFromLiveIn, GigIn, GigOut, GigsOut, GigYear, LiveShowOut
from plekstender.db.models import Gig, LiveShow
from plekstender.gigs.service import gig_from_live, library_matcher, link_artists, live_refs
from plekstender.live.merge import merge
from plekstender.worker import enqueue_gig_places

router = APIRouter(prefix="/api/gigs", tags=["gigs"])


def gig_out(gig: Gig) -> GigOut:
    return GigOut.model_validate(gig, from_attributes=True)


@router.get("", response_model=GigsOut)
async def list_gigs(
    db: DbDep,
    user: UserDep,
    year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
    artist: Annotated[str | None, Query(max_length=300)] = None,
) -> GigsOut:
    """The user's gigs, newest first; `year` and `artist` narrow them."""
    query = select(Gig).where(Gig.user_id == user.id).order_by(Gig.date.desc(), Gig.id.desc())
    if year:
        query = query.where(extract("year", Gig.date) == year)
    gigs = list(await db.scalars(query))
    if artist:
        gigs = [g for g in gigs if any(a["name"] == artist for a in g.artists)]
    per_year = await db.execute(
        select(extract("year", Gig.date).label("y"), func.count())
        .where(Gig.user_id == user.id)
        .group_by("y")
        .order_by(extract("year", Gig.date).desc())
    )
    years = [GigYear(year=int(y), gigs=n) for y, n in per_year.all()]
    return GigsOut(gigs=[gig_out(g) for g in gigs], years=years, total=sum(y.gigs for y in years))


def _check_date(day: dt.date) -> None:
    if day > dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A gig you went to can't be in the future."
        )


def _clean(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


@router.post("", response_model=GigOut, status_code=status.HTTP_201_CREATED)
async def add_gig(body: GigIn, db: DbDep, user: UserDep) -> GigOut:
    _check_date(body.date)
    match = await library_matcher(db, user.id)
    gig = Gig(user_id=user.id, source="manual")
    _apply(gig, body, link_artists(body.artists, match))
    db.add(gig)
    await db.commit()
    await db.refresh(gig)
    await enqueue_gig_places(user.id)
    return gig_out(gig)


def _apply(gig: Gig, body: GigIn, artists: list[dict[str, object]]) -> None:
    place_changed = (gig.venue, gig.city, gig.country) != (
        _clean(body.venue), _clean(body.city), (body.country or "").upper() or None,
    )  # fmt: skip
    gig.date = body.date
    gig.name = _clean(body.name)
    gig.venue, gig.city = _clean(body.venue), _clean(body.city)
    gig.country = (body.country or "").upper() or None
    gig.notes = (body.notes or "").strip() or None
    gig.url = body.url
    gig.artists = artists
    if place_changed:  # looked up again (see gigs.places)
        gig.latitude = gig.longitude = gig.place_precision = None


async def _own(db: DbDep, user_id: int, gig_id: int) -> Gig:
    gig = await db.get(Gig, gig_id)
    if gig is None or gig.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gig not found")
    return gig


@router.put("/{gig_id}", response_model=GigOut)
async def edit_gig(gig_id: int, body: GigIn, db: DbDep, user: UserDep) -> GigOut:
    gig = await _own(db, user.id, gig_id)
    _check_date(body.date)
    _apply(gig, body, link_artists(body.artists, await library_matcher(db, user.id)))
    await db.commit()
    await db.refresh(gig)
    if gig.place_precision is None:
        await enqueue_gig_places(user.id)
    return gig_out(gig)


@router.delete("/{gig_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gig(gig_id: int, db: DbDep, user: UserDep) -> Response:
    await db.delete(await _own(db, user.id, gig_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _past_live(db: DbDep, user_id: int) -> list[list[LiveShow]]:
    """Live gigs that have happened, starred or with a ticket, not yet in the history."""
    today = dt.datetime.now(dt.UTC).date()
    rows = list(
        await db.scalars(
            select(LiveShow)
            .where(
                LiveShow.user_id == user_id,
                LiveShow.start_date <= today,
                (LiveShow.starred_at.is_not(None)) | (LiveShow.ticket_at.is_not(None)),
            )
            .order_by(LiveShow.start_date.desc(), LiveShow.id)
        )
    )
    known = set(
        await db.scalars(select(Gig.source_ref).where(Gig.user_id == user_id, Gig.source == "live"))
    )
    return [g for g in merge(rows) if not (live_refs(g) & known)]


@router.get("/suggestions", response_model=list[LiveShowOut])
async def suggestions(db: DbDep, user: UserDep) -> list[LiveShowOut]:
    """Starred (or ticketed) Live gigs that have happened: did the user go?"""
    return [_show(group) for group in await _past_live(db, user.id)]


@router.post("/from-live", response_model=GigOut, status_code=status.HTTP_201_CREATED)
async def from_live(body: GigFromLiveIn, db: DbDep, user: UserDep) -> GigOut:
    """Record a Live gig as one the user went to."""
    for group in await _past_live(db, user.id):
        if {r.id for r in group} & set(body.ids):
            gig = gig_from_live(group, user.id)
            db.add(gig)
            await db.commit()
            await db.refresh(gig)
            return gig_out(gig)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No such past gig to record")


@router.get("/artists", response_model=list[str])
async def gig_artists(db: DbDep, user: UserDep) -> list[str]:
    """Everyone on the user's gigs, most seen first (for the filter)."""
    counts: Counter[str] = Counter()
    for artists in await db.scalars(select(Gig.artists).where(Gig.user_id == user.id)):
        counts.update(a["name"] for a in artists)
    return [n for n, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))]
