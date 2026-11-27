"""Library → Discover: artists to add (see plekstender.artist_suggestions)."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import Response as RawResponse
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from plekstender import discover_art, musicbrainz
from plekstender.api.deps import DbDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    ArtistSuggestionOut,
    ArtistSuggestionsOut,
    HideSuggestionIn,
    SuggestionArtOut,
    SuggestionSourceOut,
)
from plekstender.artist_suggestions import SOURCES_SHOWN, suggestions
from plekstender.db.models import DiscoverArt, HiddenSuggestion
from plekstender.worker import enqueue_discover_art

router = APIRouter(prefix="/api/servers/{server_id}/library/discover", tags=["library"])
art_router = APIRouter(prefix="/api/discover-art", tags=["library"])


@router.get("", response_model=ArtistSuggestionsOut)
async def artists_to_add(
    server_id: int,
    db: DbDep,
    user: UserDep,
    hidden: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ArtistSuggestionsOut:
    """Artists not in the library that Plex names as similar to the most library artists
    (then by your listens to them). `hidden`: the ones you've hidden instead."""
    us = await get_user_server(db, user, server_id)
    found = await suggestions(db, user.id, us, hidden=hidden)
    hidden_count = await db.scalar(select(func.count()).where(HiddenSuggestion.user_id == user.id))
    # The top ones get artwork (the big cards and the banner rows): look up any not yet.
    top = [] if hidden else [s.key for s in found[: discover_art.TOP]]
    art = await discover_art.for_keys(db, top)
    if await discover_art.missing(db, top):
        await enqueue_discover_art()
    page = found[offset : offset + limit]
    return ArtistSuggestionsOut(
        items=[
            ArtistSuggestionOut(
                key=s.key,
                name=s.name,
                similar_to=s.similar_to,
                sources=[
                    SuggestionSourceOut(name=x.name, rating_key=x.rating_key)
                    for x in s.sources[:SOURCES_SHOWN]
                ],
                listens=s.listens,
                art=_art(art.get(s.key)),
            )
            for s in page
        ],
        total=len(found),
        hidden=hidden_count or 0,
    )


def _art(found: DiscoverArt | None) -> SuggestionArtOut | None:
    if found is None or not found.mbid or not (found.background or found.banner or found.logo):
        return None
    url = f"/api/discover-art/{found.mbid}"
    return SuggestionArtOut(
        background=f"{url}/background" if found.background else None,
        banner=f"{url}/banner" if found.banner else None,
        logo=f"{url}/logo" if found.logo else None,
    )


@art_router.get(
    "/{mbid}/{kind}",
    response_class=RawResponse,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}, 404: {}},
    dependencies=[rate_limit("discover-art", limit=600, window=60)],
)
async def discover_artwork(
    mbid: str, kind: discover_art.Kind, user: UserDep, settings: SettingsDep
) -> RawResponse:
    """A suggested artist's fanart.tv background, banner or logo, as kept by Plekstender."""
    if not musicbrainz.MBID.fullmatch(mbid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        body = await asyncio.to_thread(discover_art.path(settings, mbid, kind).read_bytes)
    except OSError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from exc
    return RawResponse(
        body,
        media_type=discover_art.media_type(body),
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/hidden", status_code=status.HTTP_204_NO_CONTENT)
async def hide(server_id: int, body: HideSuggestionIn, db: DbDep, user: UserDep) -> Response:
    """Stop suggesting an artist (for you, on every server)."""
    await get_user_server(db, user, server_id)
    await db.execute(
        insert(HiddenSuggestion)
        .values(user_id=user.id, artist_key=body.key, name=body.name)
        .on_conflict_do_nothing(index_elements=["user_id", "artist_key"])
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/hidden/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def unhide(server_id: int, key: str, db: DbDep, user: UserDep) -> Response:
    """Suggest an artist again."""
    await get_user_server(db, user, server_id)
    await db.execute(
        delete(HiddenSuggestion).where(
            HiddenSuggestion.user_id == user.id, HiddenSuggestion.artist_key == key
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
