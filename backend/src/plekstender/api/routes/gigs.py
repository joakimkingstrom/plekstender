"""Gigs the user went to: listed, added and edited by hand, or taken from Live's gigs."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select, tuple_

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.live import _show
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    GigArtistOut,
    GigFromLiveIn,
    GigImportOut,
    GigIn,
    GigOut,
    GigPlace,
    GigPlaceFound,
    GigSameVenueOut,
    GigsOut,
    GigStatsOut,
    GigYear,
    LiveShowOut,
    SetlistFmImportIn,
    SetlistLookupIn,
    SetlistLookupOut,
    SetlistSearchItem,
    SetlistSearchOut,
)
from plekstender.db.instance import get_setlistfm_api_key
from plekstender.db.models import Artist, Gig, LiveShow
from plekstender.gigs import imports
from plekstender.gigs.places import PlaceError, search_places
from plekstender.gigs.service import (
    gig_from_live,
    library_matcher,
    link_artists,
    live_refs,
    place_key,
)
from plekstender.gigs.stats import gig_stats
from plekstender.live.merge import merge
from plekstender.sync.parse import normalise_artist
from plekstender.worker import enqueue_gig_places

router = APIRouter(prefix="/api/gigs", tags=["gigs"])


def gig_out(gig: Gig) -> GigOut:
    return GigOut.model_validate(gig, from_attributes=True)


FOUND = ("venue", "manual")  # place_precision: pinned at the venue itself


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").casefold() == (b or "").casefold()


async def _popularity(db: DbDep, gigs: list[Gig]) -> dict[tuple[int, int], int]:
    """How popular each library artist on these gigs is (Plekstender's score from Last.fm
    listeners, see plekstender.sync.popularity)."""
    refs = {
        (a["server_id"], a["rating_key"])
        for g in gigs
        for a in g.artists
        if a.get("server_id") is not None and a.get("rating_key") is not None
    }
    if not refs:
        return {}
    rows = await db.execute(
        select(Artist.server_id, Artist.rating_key, Artist.popularity).where(
            tuple_(Artist.server_id, Artist.rating_key).in_(list(refs)),
            Artist.popularity.is_not(None),
        )
    )
    return {(sid, key): int(pop or 0) for sid, key, pop in rows.all()}


def _by_popularity(gig: Gig, popularity: dict[tuple[int, int], int]) -> GigOut:
    """The gig with its bill biggest artist first (ones not in the library, or without a
    score, after the rest in the order they were given)."""
    out = gig_out(gig)

    def score(artist: GigArtistOut) -> int:
        if artist.server_id is None or artist.rating_key is None:
            return -1
        return popularity.get((artist.server_id, artist.rating_key), -1)

    out.artists = sorted(out.artists, key=lambda a: -score(a))  # stable: ties keep order
    return out


@router.get("", response_model=GigsOut)
async def list_gigs(
    db: DbDep,
    user: UserDep,
    year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
    artist: Annotated[str | None, Query(max_length=300)] = None,
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    city: Annotated[str | None, Query(max_length=300)] = None,
    venue: Annotated[str | None, Query(max_length=300)] = None,
    unplaced: Annotated[
        bool, Query(description="Only gigs not pinned at their venue on the map.")
    ] = False,
) -> GigsOut:
    """The user's gigs, newest first (on the same day, the one with the biggest artist
    first); `year`, `artist`, `country`, `city`, `venue` and `unplaced` narrow them."""
    every = list(
        await db.scalars(
            select(Gig).where(Gig.user_id == user.id).order_by(Gig.date.desc(), Gig.id.desc())
        )
    )
    gigs = [
        g
        for g in every
        if (not year or g.date.year == year)
        and (not artist or any(a["name"] == artist for a in g.artists))
        and (not country or (g.country or "") == country.upper())
        and (not city or _same(g.city, city))
        and (not venue or _same(g.venue, venue))
        and (not unplaced or g.place_precision not in FOUND)
    ]
    popularity = await _popularity(db, gigs)
    out = [_by_popularity(g, popularity) for g in gigs]

    def biggest(g: GigOut) -> int:
        return max(
            (popularity.get((a.server_id, a.rating_key), -1) for a in g.artists
             if a.server_id is not None and a.rating_key is not None),
            default=-1,
        )  # fmt: skip

    # Newest day first; on one day, the biggest artist's gig first (sorts are stable).
    out.sort(key=lambda g: -biggest(g))
    out.sort(key=lambda g: g.date, reverse=True)

    per_year = Counter(g.date.year for g in every)
    years = [GigYear(year=y, gigs=n) for y, n in sorted(per_year.items(), reverse=True)]
    places: dict[tuple[str, str, str], GigPlace] = {}
    for g in every:
        if not (g.country or g.city or g.venue):
            continue
        key = (g.country or "", (g.city or "").casefold(), (g.venue or "").casefold())
        if key in places:
            places[key].gigs += 1
        else:
            places[key] = GigPlace(country=g.country, city=g.city, venue=g.venue, gigs=1)
    return GigsOut(
        gigs=out,
        years=years,
        total=len(every),
        places=list(places.values()),
        unplaced=sum(g.place_precision not in FOUND for g in every),
    )


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
    await db.flush()
    if body.place and body.place_others:
        await _same_place(db, gig)
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
    gig.setlists = [s.model_dump() for s in body.setlists]
    if body.place:
        gig.latitude, gig.longitude = body.place.latitude, body.place.longitude
        gig.place_precision = "manual"
    elif place_changed or body.find_place:  # looked up again (see gigs.places)
        gig.latitude = gig.longitude = gig.place_precision = None


async def _at_venue(
    db: DbDep,
    user_id: int,
    venue: str | None,
    city: str | None,
    country: str | None,
    but: int | None,
) -> list[Gig]:
    """The person's other gigs at this venue (the same venue, town and country, ignoring
    case and accents)."""
    if not _clean(venue):
        return []
    key = place_key(_clean(venue), _clean(city), (country or "").upper() or None)
    gigs = await db.scalars(
        select(Gig).where(Gig.user_id == user_id, Gig.venue.is_not(None), Gig.id != (but or -1))
    )
    return [g for g in gigs if place_key(g.venue, g.city, g.country) == key]


async def _same_place(db: DbDep, gig: Gig) -> int:
    """A place picked by hand, given to the person's other gigs at the same venue (when they
    said so). How many."""
    others = await _at_venue(db, gig.user_id, gig.venue, gig.city, gig.country, gig.id)
    for other in others:
        other.latitude, other.longitude = gig.latitude, gig.longitude
        other.place_precision = "manual"
    return len(others)


async def _own(db: DbDep, user_id: int, gig_id: int) -> Gig:
    gig = await db.get(Gig, gig_id)
    if gig is None or gig.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gig not found")
    return gig


@router.put("/{gig_id}", response_model=GigOut)
async def edit_gig(gig_id: int, body: GigIn, db: DbDep, user: UserDep) -> GigOut:
    gig = await _own(db, user.id, gig_id)
    _check_date(body.date)
    before = (gig.venue, gig.city, gig.country)
    _apply(gig, body, link_artists(body.artists, await library_matcher(db, user.id)))
    renamed = 0
    if body.rename_others and place_key(*before) != place_key(gig.venue, gig.city, gig.country):
        renamed = await _rename_venue(db, gig, before)
    if body.place and body.place_others:
        await _same_place(db, gig)
    await db.commit()
    await db.refresh(gig)
    if gig.place_precision is None or renamed:
        await enqueue_gig_places(user.id)
    return gig_out(gig)


async def _rename_venue(db: DbDep, gig: Gig, before: tuple[str | None, ...]) -> int:
    """The person's other gigs at the gig's old venue get its new venue, town and country
    (and are looked up on the map again, as the gig itself is). How many."""
    venue, city, country = before
    others = await _at_venue(db, gig.user_id, venue, city, country, gig.id)
    for other in others:
        other.venue, other.city, other.country = gig.venue, gig.city, gig.country
        other.latitude = other.longitude = other.place_precision = None
    return len(others)


@router.get("/same-venue", response_model=GigSameVenueOut)
async def same_venue(
    db: DbDep,
    user: UserDep,
    venue: Annotated[str, Query(max_length=300)],
    city: Annotated[str | None, Query(max_length=200)] = None,
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    but: Annotated[int | None, Query(description="The gig being edited.")] = None,
) -> GigSameVenueOut:
    """How many of the person's other gigs are at this venue (to ask whether a place picked
    by hand goes for those too)."""
    return GigSameVenueOut(gigs=len(await _at_venue(db, user.id, venue, city, country, but)))


@router.get(
    "/places",
    response_model=list[GigPlaceFound],
    dependencies=[rate_limit("gig-places", 20, 60)],
)
async def find_places(
    http: HttpDep,
    settings: SettingsDep,
    _: UserDep,
    q: Annotated[str, Query(min_length=2, max_length=300)],
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
) -> list[GigPlaceFound]:
    """Places on OpenStreetMap matching a name (and town), to pick a gig's place by hand.
    Only the typed text and country are sent."""
    try:
        found = await search_places(http, settings.nominatim_url, q.strip(), country)
    except PlaceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return [GigPlaceFound(**f) for f in found]


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


@router.get("/stats", response_model=GigStatsOut)
async def stats(db: DbDep, user: UserDep) -> GigStatsOut:
    """Most seen artists and venues, gigs a year, by month and by season."""
    gigs = list(await db.scalars(select(Gig).where(Gig.user_id == user.id)))
    return GigStatsOut.model_validate(gig_stats(gigs), from_attributes=True)


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
    "/setlistfm/lookup",
    response_model=SetlistLookupOut,
    dependencies=[rate_limit("gigs-setlist-lookup", 20, 60)],
)
async def setlistfm_lookup(
    body: SetlistLookupIn, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep
) -> SetlistLookupOut:
    """A gig from a link to one of its setlists on setlist.fm: date, venue, tour, the
    whole bill (headliners first) and what each artist played."""
    key = await _setlistfm_key(db, settings)
    if imports.setlist_id(body.url) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't a link to a setlist on setlist.fm."
        )
    try:
        draft = await imports.setlistfm_gig(http, settings.setlistfm_api_url, key, body.url)
    except imports.ImportError_ as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return SetlistLookupOut.model_validate(
        {**draft.__dict__, "url": draft.url or body.url}, from_attributes=False
    )


async def _setlistfm_key(db: DbDep, settings: SettingsDep) -> str:
    key, _ = await get_setlistfm_api_key(db, settings)
    if not key:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "setlist.fm isn't set up: an admin can add an API key in Settings → Services.",
        )
    return key


@router.get(
    "/setlistfm/search",
    response_model=SetlistSearchOut,
    dependencies=[rate_limit("gigs-setlist-search", 30, 60)],
)
async def setlistfm_search(
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    artist: Annotated[str | None, Query(max_length=200)] = None,
    year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
    city: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
) -> SetlistSearchOut:
    """Setlists on setlist.fm by artist, year and town (at least one), newest first."""
    artist, city = _clean(artist), _clean(city)
    if not (artist or year or city):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Search by an artist, a year or a town."
        )
    key = await _setlistfm_key(db, settings)
    try:
        found, total = await imports.setlistfm_search(
            http, settings.setlistfm_api_url, key, artist=artist, year=year, city=city, page=page
        )
    except imports.ImportError_ as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return SetlistSearchOut(
        items=[
            SetlistSearchItem(
                url=f.draft.url,
                date=f.draft.date,
                artist=f.artist,
                venue=f.draft.venue,
                city=f.draft.city,
                country=f.draft.country,
                tour=f.draft.name,
                songs=f.songs,
            )
            for f in found
            if f.draft.url
        ],
        total=total,
        page=page,
    )


@router.get("/artist/{server_id}/{rating_key}", response_model=list[GigOut])
async def artist_gigs(server_id: int, rating_key: int, db: DbDep, user: UserDep) -> list[GigOut]:
    """The gigs you've been to where a library artist played, newest first (matched by the
    library link, or by name for gigs added before the artist was in the library)."""
    us = await get_user_server(db, user, server_id)
    artist = await db.get(Artist, (us.server_id, rating_key))
    if artist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artist not found")
    name = normalise_artist(artist.title)
    gigs = await db.scalars(
        select(Gig).where(Gig.user_id == user.id).order_by(Gig.date.desc(), Gig.id.desc())
    )
    return [
        gig_out(g)
        for g in gigs
        if any(
            (a.get("server_id") == us.server_id and a.get("rating_key") == rating_key)
            or normalise_artist(a["name"]) == name
            for a in g.artists
        )
    ]


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
            "setlist.fm isn't set up: an admin can add an API key in Settings → Services.",
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
