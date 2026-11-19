"""Rating queues and writing ratings to Plex."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import (
    RatedItemOut,
    RatedPage,
    RateItemOut,
    RateQueueIn,
    RatingIn,
    RatingOut,
)
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


_RATED = """
    FROM user_item_state s
    LEFT JOIN tracks t
      ON s.item_type = 'track' AND t.server_id = s.server_id AND t.rating_key = s.rating_key
    LEFT JOIN albums talb ON talb.server_id = t.server_id AND talb.rating_key = t.album_rating_key
    LEFT JOIN artists tart ON tart.server_id = t.server_id AND tart.rating_key = t.artist_rating_key
    LEFT JOIN albums al
      ON s.item_type = 'album' AND al.server_id = s.server_id AND al.rating_key = s.rating_key
    LEFT JOIN artists alart
      ON alart.server_id = al.server_id AND alart.rating_key = al.artist_rating_key
    LEFT JOIN artists ar
      ON s.item_type = 'artist' AND ar.server_id = s.server_id AND ar.rating_key = s.rating_key
    WHERE s.user_id = :user_id AND s.server_id = :server_id AND s.section_key = ANY(:sections)
      AND s.user_rating IS NOT NULL
      AND coalesce(t.rating_key, al.rating_key, ar.rating_key) IS NOT NULL
      AND (CAST(:kind AS text) IS NULL OR s.item_type = :kind)
"""


# The latest change made here (not undone) to each item: what an undo would put back.
_LATEST_EVENT = """
    LEFT JOIN LATERAL (
        SELECT e.id, e.old_rating, e.new_rating
        FROM rating_events e
        WHERE e.user_id = s.user_id AND e.server_id = s.server_id
          AND e.rating_key = s.rating_key AND e.undone_at IS NULL
        ORDER BY e.id DESC
        LIMIT 1
    ) ev ON TRUE
"""


_RATED_WITH_UNDO = _RATED.replace("WHERE s.user_id", _LATEST_EVENT + " WHERE s.user_id", 1)


@router.post("/ratings/{rating_key}/undo", response_model=RatingOut)
async def undo_item(
    server_id: int,
    rating_key: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> RatingOut:
    """Undo your latest rating of one item (repeatable, stepping further back)."""
    us = await get_user_server(db, user, server_id)
    client = server_client(us, cipher, settings, http)
    try:
        undone = await undo_last(
            db, client, user_id=user.id, server_id=us.server_id, rating_key=rating_key
        )
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Plex didn't save it: {exc}") from exc
    if undone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing to undo")
    state, _ = undone
    return RatingOut(
        rating_key=state.rating_key, item_type=state.item_type, user_rating=state.user_rating
    )


@router.get("/ratings", response_model=RatedPage)
async def rated(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: Literal["track", "album", "artist"] | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> RatedPage:
    """Everything you've rated, the most recently rated first (in Plex or here). Items
    rated before Plex kept rating times come last."""
    us = await get_user_server(db, user, server_id)
    params = {
        "user_id": user.id,
        "server_id": us.server_id,
        "sections": list(us.music_section_keys),
        "kind": kind,
        "offset": offset,
        "limit": limit,
    }
    total = await db.scalar(text(f"SELECT count(*) {_RATED}"), params) or 0
    rows = await db.execute(
        text(
            f"""
            SELECT s.rating_key, s.item_type, s.user_rating, s.last_rated_at,
                   coalesce(t.title, al.title, ar.title) AS title,
                   CASE s.item_type
                     WHEN 'track' THEN concat_ws(' · ', coalesce(t.original_title, tart.title),
                                                 talb.title)
                     WHEN 'album' THEN alart.title
                   END AS subtitle,
                   coalesce(talb.thumb, al.thumb, ar.thumb) AS thumb,
                   CASE s.item_type
                     WHEN 'track' THEN t.album_rating_key
                     WHEN 'album' THEN al.rating_key
                     ELSE ar.rating_key
                   END AS link_key,
                   ev.old_rating AS undo_to,
                   (ev.id IS NOT NULL AND ev.new_rating IS NOT DISTINCT FROM s.user_rating)
                     AS undoable
            {_RATED_WITH_UNDO}
            ORDER BY s.last_rated_at DESC NULLS LAST, s.rating_key DESC
            OFFSET :offset LIMIT :limit
            """  # noqa: S608 - constant SQL fragments
        ),
        params,
    )
    return RatedPage(
        items=[
            RatedItemOut(
                rating_key=r.rating_key,
                kind=r.item_type,
                title=r.title,
                subtitle=r.subtitle or None,
                thumb=r.thumb,
                user_rating=r.user_rating,
                rated_at=r.last_rated_at,
                link_key=r.link_key,
                undo_to=r.undo_to if r.undoable else None,
                undoable=bool(r.undoable),
            )
            for r in rows.all()
        ],
        total=int(total),
    )
