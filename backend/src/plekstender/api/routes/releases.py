"""Library -> Releases: new releases from the artists you follow, and what's missing from
the library (MusicBrainz release groups, read by the worker; see plekstender.releases)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response as RawResponse
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from plekstender import musicbrainz, releases
from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    MissingArtistOut,
    MissingReleasesOut,
    NewReleaseOut,
    NewReleasesOut,
    ReleaseArtistOut,
    ReleaseCoverageOut,
    ReleaseKind,
    ReleaseOut,
)
from plekstender.artwork import images
from plekstender.artwork.fetch import FetchError, fetch
from plekstender.artwork.images import ImageError
from plekstender.db.models import HiddenRelease

router = APIRouter(prefix="/api/servers/{server_id}/releases", tags=["releases"])
covers_router = APIRouter(prefix="/api/releases", tags=["releases"])

Kinds = Annotated[list[ReleaseKind], Query(description="Kinds of release to show.")]
DEFAULT_KINDS: list[ReleaseKind] = ["album", "ep"]


def _release_out(r: releases.Release) -> ReleaseOut:
    return ReleaseOut(
        mbid=r.mbid,
        title=r.title,
        kind=r.kind,
        first_release_date=r.first_release_date,
        in_library=r.in_library,
        hidden=r.hidden,
    )


@router.get("/new", response_model=NewReleasesOut)
async def new_releases(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: Kinds = DEFAULT_KINDS,
    include_hidden: bool = False,
) -> NewReleasesOut:
    """The last 90 days' releases, and those coming up, by artists you follow: rated 4 stars or
    more, or among your 50 most played."""
    us = await get_user_server(db, user, server_id)
    sections = list(us.music_section_keys)
    today = dt.datetime.now(dt.UTC).date()
    found = await releases.new_releases(
        db, user.id, us.server_id, sections, list(kind), today, include_hidden
    )
    return NewReleasesOut(
        items=[
            NewReleaseOut(
                **_release_out(r).model_dump(),
                artist=ReleaseArtistOut(rating_key=a.rating_key, name=a.name, thumb=a.thumb),
                upcoming=r.released is not None and r.released > today,
            )
            for r, a in found
        ],
        following=await releases.following(db, user.id, us.server_id, sections),
        new_days=releases.NEW_FOR.days,
    )


@router.get("/missing", response_model=MissingReleasesOut)
async def missing_releases(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: Kinds = DEFAULT_KINDS,
    q: Annotated[str | None, Query(max_length=200, description="Artist name contains")] = None,
    include_hidden: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MissingReleasesOut:
    """Released albums (or other kinds) of your library's artists that it doesn't have, by
    artist, most played first."""
    us = await get_user_server(db, user, server_id)
    sections = list(us.music_section_keys)
    artists, total = await releases.missing_releases(
        db,
        user.id,
        us.server_id,
        sections,
        list(kind),
        dt.datetime.now(dt.UTC).date(),
        q=q,
        include_hidden=include_hidden,
        offset=offset,
        limit=limit,
    )
    cover = await releases.coverage(db, user.id, us.server_id, sections)
    return MissingReleasesOut(
        artists=[
            MissingArtistOut(
                rating_key=a.rating_key,
                name=a.name,
                thumb=a.thumb,
                releases=[_release_out(r) for r in a.releases],
            )
            for a in artists
        ],
        total_artists=total,
        coverage=ReleaseCoverageOut(
            artists=cover.artists, with_mbid=cover.with_mbid, checked=cover.checked
        ),
    )


def _check_mbid(mbid: str) -> str:
    if not musicbrainz.MBID.fullmatch(mbid):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Not a MusicBrainz ID")
    return mbid


@router.put("/hidden/{mbid}", status_code=status.HTTP_204_NO_CONTENT)
async def hide_release(server_id: int, mbid: str, db: DbDep, user: UserDep) -> None:
    """Don't show this release (on any server)."""
    await get_user_server(db, user, server_id)
    stmt = insert(HiddenRelease).values(user_id=user.id, mbid=_check_mbid(mbid))
    await db.execute(stmt.on_conflict_do_nothing())
    await db.commit()


@router.delete("/hidden/{mbid}", status_code=status.HTTP_204_NO_CONTENT)
async def show_release(server_id: int, mbid: str, db: DbDep, user: UserDep) -> None:
    await get_user_server(db, user, server_id)
    await db.execute(
        delete(HiddenRelease).where(
            HiddenRelease.user_id == user.id, HiddenRelease.mbid == _check_mbid(mbid)
        )
    )
    await db.commit()


@covers_router.get(
    "/{mbid}/cover",
    response_class=RawResponse,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}, 404: {}},
    dependencies=[rate_limit("release-cover", limit=600, window=60)],
)
async def release_cover(
    mbid: str, user: UserDep, settings: SettingsDep, http: HttpDep
) -> RawResponse:
    """A release group's front cover from the Cover Art Archive, small, fetched by the server
    (the page only loads images from here)."""
    url = f"{settings.coverartarchive_url.rstrip('/')}/release-group/{_check_mbid(mbid)}/front-250"
    try:
        data, _ = await fetch(http, url, max_bytes=5 * 1024 * 1024)
        body, content_type = images.thumbnail(data, 250)
    except FetchError, ImageError:
        # Most often there's no cover; let the browser remember that for a while too.
        return RawResponse(status_code=404, headers={"Cache-Control": "private, max-age=86400"})
    return RawResponse(
        body, media_type=content_type, headers={"Cache-Control": "private, max-age=604800"}
    )
