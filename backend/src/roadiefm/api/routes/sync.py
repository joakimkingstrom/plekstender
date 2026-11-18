"""Library sync status and manual sync."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from roadiefm.api.deps import DbDep, UserDep
from roadiefm.api.ratelimit import rate_limit
from roadiefm.api.routes.servers import get_user_server
from roadiefm.api.schemas import LibraryCounts, SyncIn, SyncRunOut, SyncStatusOut
from roadiefm.db.instance import get_library_settings
from roadiefm.db.models import SyncRun, UserItemState, UserSectionSync, UserServer
from roadiefm.worker import ACTIVE, enqueue_sync

router = APIRouter(prefix="/api/servers/{server_id}/sync", tags=["sync"])


def run_out(run: SyncRun | None) -> SyncRunOut | None:
    if run is None:
        return None
    return SyncRunOut.model_validate(run, from_attributes=True)


async def library_counts(db: DbDep, user_id: int, us: UserServer) -> LibraryCounts:
    s = UserItemState
    visible = (
        s.user_id == user_id,
        s.server_id == us.server_id,
        s.section_key.in_(us.music_section_keys),
    )
    by_type = dict(
        (
            await db.execute(
                select(s.item_type, func.count()).where(*visible).group_by(s.item_type)
            )
        ).all()
    )
    rated, plays = (
        await db.execute(
            select(
                func.count().filter(s.user_rating.is_not(None)),
                func.coalesce(func.sum(s.view_count), 0),
            ).where(*visible, s.item_type == "track")
        )
    ).one()
    return LibraryCounts(
        artists=by_type.get("artist", 0),
        albums=by_type.get("album", 0),
        tracks=by_type.get("track", 0),
        rated_tracks=rated,
        plays=plays,
    )


@router.get("", response_model=SyncStatusOut)
async def sync_status(server_id: int, db: DbDep, user: UserDep) -> SyncStatusOut:
    us = await get_user_server(db, user, server_id)
    recent = list(
        await db.scalars(
            select(SyncRun)
            .where(SyncRun.user_server_id == us.id)
            .order_by(SyncRun.id.desc())
            .limit(5)
        )
    )
    schedule = await get_library_settings(db)
    active = next((r for r in recent if r.status in ACTIVE), None)
    last = next((r for r in recent if r.status not in ACTIVE), None)
    times = (
        await db.execute(
            select(
                func.min(UserSectionSync.last_full_sync_at),
                func.min(UserSectionSync.last_sync_started_at),
            ).where(
                UserSectionSync.user_server_id == us.id,
                UserSectionSync.section_key.in_(us.music_section_keys),
            )
        )
    ).one()
    return SyncStatusOut(
        active=run_out(active),
        last=run_out(last),
        last_full_sync_at=times[0],
        last_sync_at=times[1],
        counts=await library_counts(db, user.id, us),
        incremental_minutes=schedule.incremental_minutes,
        full_days=schedule.full_days,
    )


@router.post(
    "",
    response_model=SyncRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("sync", 10, 60)],
)
async def start_sync(server_id: int, body: SyncIn, db: DbDep, user: UserDep) -> SyncRunOut:
    us = await get_user_server(db, user, server_id)
    if not us.music_section_keys:
        raise HTTPException(status.HTTP_409_CONFLICT, "Pick a music library first")
    run = await enqueue_sync(db, us, body.kind, "manual")
    out = run_out(run)
    assert out is not None
    return out
