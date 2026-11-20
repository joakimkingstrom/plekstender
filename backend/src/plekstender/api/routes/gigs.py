"""Gigs the user went to: listed, added and edited by hand, or taken from Live's gigs."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import extract, func, select

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.live import _show
from plekstender.api.schemas import (
    GigFromLiveIn,
    GigImportOut,
    GigIn,
    GigOut,
    GigsOut,
    GigYear,
    LiveShowOut,
    SetlistFmImportIn,
)
from plekstender.db.instance import get_setlistfm_api_key
from plekstender.db.models import Gig, LiveShow
from plekstender.gigs import imports
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


# --- import and export ---------------------------------------------------------------------

MAX_FILE = 2 * 1024 * 1024


async def _file(request: Request) -> str:
    body = await request.body()
    if len(body) > MAX_FILE:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Files can be up to 2 MB.")
    return body.decode("utf-8-sig", errors="replace")


async def _stored(
    db: DbDep, user_id: int, drafts: list[imports.Draft], problems: list[str]
) -> GigImportOut:
    result = await imports.store(db, user_id, drafts)
    if result.added or result.updated:
        await enqueue_gig_places(user_id)
    return GigImportOut(
        added=result.added, updated=result.updated, skipped=result.skipped, problems=problems[:20]
    )


@router.post(
    "/import/setlistfm",
    response_model=GigImportOut,
    dependencies=[rate_limit("gigs-setlistfm", 5, 60)],
)
async def import_setlistfm(
    body: SetlistFmImportIn, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep
) -> GigImportOut:
    """The concerts the user marked as attended on setlist.fm (their profile's name)."""
    key, _ = await get_setlistfm_api_key(db, settings)
    if not key:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "setlist.fm isn't set up: an admin can add an API key in Settings → Connections.",
        )
    try:
        drafts = await imports.setlistfm_attended(
            http, settings.setlistfm_api_url, key, body.username
        )
    except imports.ImportError_ as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return await _stored(db, user.id, drafts, [])


@router.post(
    "/import/csv",
    response_model=GigImportOut,
    dependencies=[rate_limit("gigs-import", 20, 60)],
    openapi_extra={"requestBody": {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
async def import_csv(request: Request, db: DbDep, user: UserDep) -> GigImportOut:
    """Gigs from a CSV (a header row naming at least the date and artist columns)."""
    try:
        drafts, problems = imports.parse_csv(await _file(request))
    except imports.ImportError_ as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return await _stored(db, user.id, drafts, problems)


@router.post(
    "/import/ical",
    response_model=GigImportOut,
    dependencies=[rate_limit("gigs-import", 20, 60)],
    openapi_extra={"requestBody": {"content": {"text/calendar": {"schema": {"type": "string"}}}}},
)
async def import_ical(request: Request, db: DbDep, user: UserDep) -> GigImportOut:
    """Past events in a calendar file, as gigs."""
    text = await _file(request)
    if "BEGIN:VCALENDAR" not in text[:2000].upper():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't a calendar (.ics) file."
        )
    drafts = imports.parse_ical(text, dt.datetime.now(dt.UTC).date())
    return await _stored(db, user.id, drafts, [])


async def _all(db: DbDep, user_id: int) -> list[Gig]:
    return list(
        await db.scalars(select(Gig).where(Gig.user_id == user_id).order_by(Gig.date, Gig.id))
    )


@router.get("/export.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
async def export_csv(db: DbDep, user: UserDep) -> Response:
    return Response(
        imports.to_csv(await _all(db, user.id)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gigs.csv"'},
    )


@router.get(
    "/export.json", response_class=Response, responses={200: {"content": {"application/json": {}}}}
)
async def export_json(db: DbDep, user: UserDep) -> Response:
    return Response(
        imports.to_json(await _all(db, user.id)),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="gigs.json"'},
    )


@router.get(
    "/export.ics", response_class=Response, responses={200: {"content": {"text/calendar": {}}}}
)
async def export_ics(db: DbDep, user: UserDep) -> Response:
    return Response(
        imports.to_ical(await _all(db, user.id)),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gigs.ics"'},
    )
