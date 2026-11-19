"""Rating queues and writing ratings to Plex."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import RateItemOut, RateQueueIn, RatingIn, RatingOut
from plekstender.plex.tv import PlexError
from plekstender.ratings.queue import build_queue
from plekstender.ratings.service import NotRateable, set_rating, undo_last

router = APIRouter(prefix="/api/servers/{server_id}", tags=["ratings"])


@router.post("/rate/queue", response_model=list[RateItemOut])
async def rate_queue(
    server_id: int, body: RateQueueIn, db: DbDep, user: UserDep
) -> list[RateItemOut]:
    """The next items to rate. POST because the list of already-shown items can be long."""
    us = await get_user_server(db, user, server_id)
    items = await build_queue(
        db,
        user_id=user.id,
        server_id=us.server_id,
        sections=us.music_section_keys,
        kind=body.kind,
        pool=body.pool,
        order=body.order,
        exclude=body.exclude,
        limit=body.limit,
        tag_id=body.tag_id,
    )
    return [RateItemOut.model_validate(item, from_attributes=True) for item in items]


@router.post(
    "/ratings",
    response_model=RatingOut,
    dependencies=[rate_limit("ratings", limit=300, window=60)],
)
async def rate(
    server_id: int,
    body: RatingIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> RatingOut:
    """Rate a song, album or artist in Plex (with your own Plex account)."""
    us = await get_user_server(db, user, server_id)
    client = server_client(us, cipher, settings, http)
    try:
        state, _ = await set_rating(
            db,
            client,
            user_id=user.id,
            server_id=us.server_id,
            rating_key=body.rating_key,
            rating=None if body.rating is None else float(body.rating),
        )
    except NotRateable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found") from exc
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Plex didn't save it: {exc}") from exc
    return RatingOut(
        rating_key=state.rating_key, item_type=state.item_type, user_rating=state.user_rating
    )


@router.post("/ratings/undo", response_model=RatingOut)
async def undo(
    server_id: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> RatingOut:
    """Undo your latest rating on this server (repeatable, newest first)."""
    us = await get_user_server(db, user, server_id)
    client = server_client(us, cipher, settings, http)
    try:
        undone = await undo_last(db, client, user_id=user.id, server_id=us.server_id)
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Plex didn't save it: {exc}") from exc
    if undone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing to undo")
    state, _ = undone
    return RatingOut(
        rating_key=state.rating_key, item_type=state.item_type, user_rating=state.user_rating
    )
