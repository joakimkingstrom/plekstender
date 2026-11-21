"""Library → Discover: artists to add (see plekstender.artist_suggestions)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    ArtistSuggestionOut,
    ArtistSuggestionsOut,
    HideSuggestionIn,
    SuggestionSourceOut,
)
from plekstender.artist_suggestions import SOURCES_SHOWN, suggestions
from plekstender.db.models import HiddenSuggestion

router = APIRouter(prefix="/api/servers/{server_id}/library/discover", tags=["library"])


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
            )
            for s in found[offset : offset + limit]
        ],
        total=len(found),
        hidden=hidden_count or 0,
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
