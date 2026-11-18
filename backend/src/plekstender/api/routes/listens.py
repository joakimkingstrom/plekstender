"""Listening history: the user's listens and their Last.fm import."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, tuple_

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import (
    DeletedOut,
    ImportStatusOut,
    LastFmImportIn,
    ListenBrainzImportIn,
    ListenBrainzStatusOut,
    ListenBrainzTokenIn,
    ListenImportOut,
    ListenOut,
    ListenPage,
    ListenSource,
    ListenSummary,
    PlexCaptureOut,
)
from plekstender.db import get_sessionmaker
from plekstender.db.instance import get_lastfm_api_key
from plekstender.db.models import (
    HistoryCursor,
    Listen,
    ListenBrainzLink,
    ListenImport,
    User,
    UserServer,
)
from plekstender.history.lastfm import LastFmClient, LastFmError
from plekstender.history.lastfm_import import next_window_start
from plekstender.history.listenbrainz import (
    TOKEN_PURPOSE,
    ListenBrainzClient,
    ListenBrainzError,
    count_pending,
    listen_payload,
)
from plekstender.history.listenbrainz_import import ListenBrainzImportError, listen_count
from plekstender.worker import ACTIVE, enqueue_listen_import, enqueue_listenbrainz

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


@router.get(
    "/export.jsonl",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}}},
)
async def export_listens(user: UserDep) -> StreamingResponse:
    """All your listens (duplicates left out), oldest first, as ListenBrainz JSON lines."""
    user_id = user.id

    async def lines() -> AsyncIterator[str]:
        # Own session: the request's is closed once streaming starts.
        async with get_sessionmaker()() as db:
            after: tuple[dt.datetime, int] | None = None
            while True:
                query = select(Listen).where(
                    Listen.user_id == user_id, Listen.duplicate_of.is_(None)
                )
                if after is not None:
                    query = query.where(tuple_(Listen.listened_at, Listen.id) > after)
                batch = list(
                    await db.scalars(query.order_by(Listen.listened_at, Listen.id).limit(1000))
                )
                if not batch:
                    return
                for listen in batch:
                    yield json.dumps(listen_payload(listen), ensure_ascii=False) + "\n"
                after = (batch[-1].listened_at, batch[-1].id)

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="plekstender-listens.jsonl"'},
    )


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
    if source in (None, "lastfm", "listenbrainz"):
        # Forget past imports too, so the next one fetches the whole history again.
        forget = delete(ListenImport).where(ListenImport.user_id == user.id)
        if source is not None:
            forget = forget.where(ListenImport.source == source)
        await db.execute(forget)
    await db.commit()
    return DeletedOut(deleted=int(getattr(result, "rowcount", 0) or 0))


@router.delete("/{listen_id:int}", status_code=status.HTTP_204_NO_CONTENT)
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


async def import_status(db: DbDep, user: User, source: str, available: bool) -> ImportStatusOut:
    recent = list(
        await db.scalars(
            select(ListenImport)
            .where(ListenImport.user_id == user.id, ListenImport.source == source)
            .order_by(ListenImport.id.desc())
            .limit(5)
        )
    )
    active = next((j for j in recent if j.status in ACTIVE), None)
    last = next((j for j in recent if j.status not in ACTIVE), None)
    return ImportStatusOut(
        available=available,
        username=recent[0].username if recent else None,
        active=import_out(active),
        last=import_out(last),
    )


@router.get("/lastfm", response_model=ImportStatusOut)
async def lastfm_status(db: DbDep, user: UserDep, settings: SettingsDep) -> ImportStatusOut:
    api_key, _ = await get_lastfm_api_key(db, settings)
    return await import_status(db, user, "lastfm", api_key is not None)


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


# --- ListenBrainz import -------------------------------------------------------------------


@router.get("/imports/listenbrainz", response_model=ImportStatusOut)
async def listenbrainz_import_status(db: DbDep, user: UserDep) -> ImportStatusOut:
    return await import_status(db, user, "listenbrainz", True)


@router.post(
    "/imports/listenbrainz",
    response_model=ListenImportOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("listenbrainz-import", limit=10, window=60)],
)
async def start_listenbrainz_import(
    body: ListenBrainzImportIn, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep
) -> ListenImportOut:
    """Import a ListenBrainz user's public listens (only newer ones if imported before)."""
    if await active_import(db, user) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An import is already running.")
    username = body.username.strip()
    try:
        total = await listen_count(http, settings, username)
    except ListenBrainzImportError as exc:
        code = status.HTTP_502_BAD_GATEWAY if exc.transient else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    job = ListenImport(
        user_id=user.id,
        source="listenbrainz",
        username=username,
        status="queued",
        window_from=await next_window_start(db, user.id, username, "listenbrainz"),
        window_to=dt.datetime.now(dt.UTC),
        progress={"total": total},
        stats={},
    )
    db.add(job)
    await db.commit()
    await enqueue_listen_import(db, job)
    return ListenImportOut.model_validate(job, from_attributes=True)


# --- ListenBrainz -----------------------------------------------------------------------


async def listenbrainz_out(db: DbDep, link: ListenBrainzLink | None) -> ListenBrainzStatusOut:
    if link is None:
        return ListenBrainzStatusOut(
            connected=False,
            username=None,
            enabled_at=None,
            sent=0,
            pending=0,
            last_submitted_at=None,
            paused=False,
            error=None,
        )
    return ListenBrainzStatusOut(
        connected=True,
        username=link.username,
        enabled_at=link.enabled_at,
        sent=link.sent_count,
        pending=await count_pending(db, link),
        last_submitted_at=link.last_submitted_at,
        paused=link.paused,
        error=link.last_error,
    )


@router.get("/listenbrainz", response_model=ListenBrainzStatusOut)
async def listenbrainz_status(db: DbDep, user: UserDep) -> ListenBrainzStatusOut:
    return await listenbrainz_out(db, await db.get(ListenBrainzLink, user.id))


@router.put(
    "/listenbrainz",
    response_model=ListenBrainzStatusOut,
    dependencies=[rate_limit("listenbrainz-token", limit=10, window=60)],
)
async def connect_listenbrainz(
    body: ListenBrainzTokenIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> ListenBrainzStatusOut:
    """Start (or resume, with a new token) scrobbling new Plex plays to ListenBrainz."""
    try:
        username = await ListenBrainzClient(
            http, settings.listenbrainz_api_url, body.token
        ).validate()
    except ListenBrainzError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if username is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ListenBrainz didn't accept this token.")

    token_enc = cipher.encrypt(body.token, purpose=TOKEN_PURPOSE)
    link = await db.get(ListenBrainzLink, user.id)
    if link is None or link.username.lower() != username.lower():
        if link is not None:
            await db.delete(link)
            await db.flush()
        link = ListenBrainzLink(
            user_id=user.id,
            token_enc=token_enc,
            username=username,
            enabled_at=dt.datetime.now(dt.UTC),
            submitted_through_id=0,
            sent_count=0,
            paused=False,
        )
        db.add(link)
    else:  # same account, new token: keep going where it stopped
        link.token_enc = token_enc
        link.paused = False
        link.last_error = None
    await db.commit()
    await enqueue_listenbrainz([user.id])
    return await listenbrainz_out(db, link)


@router.delete("/listenbrainz", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_listenbrainz(db: DbDep, user: UserDep) -> Response:
    await db.execute(delete(ListenBrainzLink).where(ListenBrainzLink.user_id == user.id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
