"""Listening history: the user's listens and their Last.fm import."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import (
    DeletedOut,
    LastFmImportIn,
    LastFmStatusOut,
    ListenImportOut,
    ListenOut,
    ListenPage,
    ListenSource,
    ListenSummary,
    PlexCaptureOut,
)
from plekstender.db.instance import get_lastfm_api_key
from plekstender.db.models import HistoryCursor, Listen, ListenImport, User, UserServer
from plekstender.history.lastfm import LastFmClient, LastFmError
from plekstender.history.lastfm_import import next_window_start
from plekstender.worker import ACTIVE, enqueue_listen_import

router = APIRouter(prefix="/api/listens", tags=["listens"])


def listen_out(listen: Listen) -> ListenOut:
    return ListenOut(
        id=listen.id,
        listened_at=listen.listened_at,
        source=listen.source,
        artist_name=listen.artist_name,
        album_name=listen.album_name,
        track_name=listen.track_name,
        duplicate=listen.duplicate_of is not None,
    )


def import_out(job: ListenImport | None) -> ListenImportOut | None:
    return None if job is None else ListenImportOut.model_validate(job, from_attributes=True)


async def active_import(db: DbDep, user: User) -> ListenImport | None:
    return await db.scalar(
        select(ListenImport)
        .where(ListenImport.user_id == user.id, ListenImport.status.in_(ACTIVE))
        .order_by(ListenImport.id.desc())
        .limit(1)
    )


@router.get("", response_model=ListenPage)
async def list_listens(
    db: DbDep,
    user: UserDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ListenPage:
    total = await db.scalar(select(func.count()).where(Listen.user_id == user.id)) or 0
    rows = await db.scalars(
        select(Listen)
        .where(Listen.user_id == user.id)
        .order_by(Listen.listened_at.desc(), Listen.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return ListenPage(items=[listen_out(r) for r in rows], total=total)


@router.get("/summary", response_model=ListenSummary)
async def listen_summary(db: DbDep, user: UserDep) -> ListenSummary:
    counted = (Listen.user_id == user.id, Listen.duplicate_of.is_(None))
    total, first_at, last_at = (
        await db.execute(
            select(func.count(), func.min(Listen.listened_at), func.max(Listen.listened_at)).where(
                *counted
            )
        )
    ).one()
    by_source = dict(
        (
            await db.execute(
                select(Listen.source, func.count()).where(*counted).group_by(Listen.source)
            )
        ).all()
    )
    return ListenSummary(total=total, first_at=first_at, last_at=last_at, by_source=by_source)


@router.delete("", response_model=DeletedOut)
async def delete_listens(
    db: DbDep, user: UserDep, source: ListenSource | None = None
) -> DeletedOut:
    """Delete the user's listens from one source, or all of them."""
    if await active_import(db, user) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the running import to finish.")
    stmt = delete(Listen).where(Listen.user_id == user.id)
    if source is not None:
        stmt = stmt.where(Listen.source == source)
    result = await db.execute(stmt)
    if source in (None, "lastfm"):
        # Forget past imports too, so the next one fetches the whole history again.
        await db.execute(
            delete(ListenImport).where(
                ListenImport.user_id == user.id, ListenImport.source == "lastfm"
            )
        )
    await db.commit()
    return DeletedOut(deleted=int(getattr(result, "rowcount", 0) or 0))


@router.delete("/{listen_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_listen(listen_id: int, db: DbDep, user: UserDep) -> Response:
    result = await db.execute(
        delete(Listen).where(Listen.id == listen_id, Listen.user_id == user.id)
    )
    if not getattr(result, "rowcount", 0):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listen not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Plex plays ----------------------------------------------------------------------------


@router.get("/plex", response_model=list[PlexCaptureOut])
async def plex_capture_status(db: DbDep, user: UserDep) -> list[PlexCaptureOut]:
    """How Plex play history is read for each of the user's servers."""
    own = list(
        await db.scalars(
            select(UserServer).where(UserServer.user_id == user.id).order_by(UserServer.id)
        )
    )
    counts = dict(
        (
            await db.execute(
                select(Listen.server_id, func.count())
                .where(Listen.user_id == user.id, Listen.source == "plex")
                .group_by(Listen.server_id)
            )
        ).all()
    )
    out = []
    for us in own:
        owner = (
            us
            if us.is_owner
            else await db.scalar(
                select(UserServer).where(
                    UserServer.server_id == us.server_id, UserServer.is_owner.is_(True)
                )
            )
        )
        reader = owner or us
        cursors = list(
            await db.scalars(select(HistoryCursor).where(HistoryCursor.user_server_id == reader.id))
        )
        sections = set(reader.music_section_keys)
        current = [c for c in cursors if c.section_key in sections]
        complete = bool(sections) and len(current) == len(sections)
        out.append(
            PlexCaptureOut(
                server_id=us.id,
                server_name=us.server.name,
                via_owner=owner is not None,
                checked_at=(
                    min((c.last_run_at for c in current if c.last_run_at), default=None)
                    if complete
                    else None
                ),
                backfilled=complete and all(c.backfilled_at for c in current),
                error=next((c.last_error for c in current if c.last_error), None),
                listens=counts.get(us.server_id, 0),
            )
        )
    return out


# --- Last.fm import ------------------------------------------------------------------------


@router.get("/lastfm", response_model=LastFmStatusOut)
async def lastfm_status(db: DbDep, user: UserDep, settings: SettingsDep) -> LastFmStatusOut:
    api_key, _ = await get_lastfm_api_key(db, settings)
    recent = list(
        await db.scalars(
            select(ListenImport)
            .where(ListenImport.user_id == user.id, ListenImport.source == "lastfm")
            .order_by(ListenImport.id.desc())
            .limit(5)
        )
    )
    active = next((j for j in recent if j.status in ACTIVE), None)
    last = next((j for j in recent if j.status not in ACTIVE), None)
    return LastFmStatusOut(
        available=api_key is not None,
        username=recent[0].username if recent else None,
        active=import_out(active),
        last=import_out(last),
    )


@router.post(
    "/lastfm",
    response_model=ListenImportOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("lastfm-import", limit=10, window=60)],
)
async def start_lastfm_import(
    body: LastFmImportIn, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep
) -> ListenImportOut:
    """Import the user's Last.fm scrobbles (only newer ones if imported before)."""
    if await active_import(db, user) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An import is already running.")
    api_key, _ = await get_lastfm_api_key(db, settings)
    if api_key is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Last.fm import isn't set up on this instance: an admin needs to add an API key.",
        )
    try:
        info = await LastFmClient(http, api_key, settings.lastfm_api_url).user_info(body.username)
    except LastFmError as exc:
        code = status.HTTP_502_BAD_GATEWAY if exc.transient else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc

    job = ListenImport(
        user_id=user.id,
        source="lastfm",
        username=info.name,
        status="queued",
        window_from=await next_window_start(db, user.id, info.name),
        window_to=dt.datetime.now(dt.UTC),
        progress={"total": info.playcount},
        stats={},
    )
    db.add(job)
    await db.commit()
    await enqueue_listen_import(db, job)
    return ListenImportOut.model_validate(job, from_attributes=True)


@router.post("/imports/{import_id}/resume", response_model=ListenImportOut)
async def resume_import(import_id: int, db: DbDep, user: UserDep) -> ListenImportOut:
    """Continue a failed import from the last page it stored."""
    job = await db.get(ListenImport, import_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    if job.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed imports can be resumed.")
    if await active_import(db, user) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An import is already running.")
    job.status = "queued"
    job.error = None
    job.finished_at = None
    await db.commit()
    await enqueue_listen_import(db, job)
    return ListenImportOut.model_validate(job, from_attributes=True)
