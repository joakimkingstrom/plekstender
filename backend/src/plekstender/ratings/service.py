"""Writing ratings to Plex (with the user's own token) and undoing them."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import RatingEvent, UserItemState
from plekstender.plex.server import PlexServerClient


class NotRateable(LookupError):
    pass


async def set_rating(
    db: AsyncSession,
    client: PlexServerClient,
    *,
    user_id: int,
    server_id: int,
    rating_key: int,
    rating: float | None,
) -> tuple[UserItemState, RatingEvent]:
    """Rate an item the user can see (0-10, None clears). Plex first, then the mirror."""
    state = await db.get(UserItemState, (user_id, server_id, rating_key))
    if state is None:
        raise NotRateable(rating_key)
    await client.rate(rating_key, rating)
    event = RatingEvent(
        user_id=user_id,
        server_id=server_id,
        rating_key=rating_key,
        item_type=state.item_type,
        old_rating=state.user_rating,
        new_rating=rating,
    )
    db.add(event)
    state.user_rating = rating
    state.last_rated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return state, event


async def undo_last(
    db: AsyncSession, client: PlexServerClient, *, user_id: int, server_id: int
) -> tuple[UserItemState, RatingEvent] | None:
    """Put back the rating from before the user's latest (not yet undone) change."""
    event = await db.scalar(
        select(RatingEvent)
        .where(
            RatingEvent.user_id == user_id,
            RatingEvent.server_id == server_id,
            RatingEvent.undone_at.is_(None),
        )
        .order_by(RatingEvent.id.desc())
        .limit(1)
    )
    if event is None:
        return None
    state = await db.get(UserItemState, (user_id, server_id, event.rating_key))
    if state is None:
        event.undone_at = dt.datetime.now(dt.UTC)
        await db.commit()
        return None
    await client.rate(event.rating_key, event.old_rating)
    state.user_rating = event.old_rating
    state.last_rated_at = dt.datetime.now(dt.UTC)
    event.undone_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return state, event
