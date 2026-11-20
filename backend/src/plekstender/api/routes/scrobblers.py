"""More places to scrobble to (each person's own): ListenBrainz-compatible servers such as
Maloja, Koito or Multi-Scrobbler, and Libre.fm. New Plex plays from when one is added are
sent there, like to ListenBrainz."""

from __future__ import annotations

import datetime as dt
from typing import cast

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import ScrobbleService, ScrobbleTargetIn, ScrobbleTargetOut
from plekstender.db.models import ScrobbleTarget
from plekstender.history import scrobblers
from plekstender.history.listenbrainz import ListenBrainzError, count_pending
from plekstender.worker import enqueue_listenbrainz

router = APIRouter(prefix="/api/scrobblers", tags=["listens"])
MAX_TARGETS = 10


async def _out(db: DbDep, target: ScrobbleTarget) -> ScrobbleTargetOut:
    return ScrobbleTargetOut(
        id=target.id,
        service=cast(ScrobbleService, target.service),
        label=scrobblers.service_label(target.service),
        url=target.api_url,
        username=target.username,
        enabled_at=target.enabled_at,
        sent=target.sent_count,
        pending=await count_pending(db, target),
        last_submitted_at=target.last_submitted_at,
        paused=target.paused,
        error=target.last_error,
    )


@router.get("", response_model=list[ScrobbleTargetOut])
async def list_targets(db: DbDep, user: UserDep) -> list[ScrobbleTargetOut]:
    rows = await db.scalars(
        select(ScrobbleTarget).where(ScrobbleTarget.user_id == user.id).order_by(ScrobbleTarget.id)
    )
    return [await _out(db, t) for t in rows]


@router.post(
    "",
    response_model=ScrobbleTargetOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("scrobblers", 10, 60)],
)
async def add_target(
    body: ScrobbleTargetIn, db: DbDep, http: HttpDep, settings: SettingsDep, user: UserDep
) -> ScrobbleTargetOut:
    """Add a service (checking the token, or signing in to Libre.fm once). Adding one that's
    there already (same address) replaces its token and resumes it where it stopped."""
    kind = scrobblers.SERVICES[body.service][0]
    try:
        if kind == "librefm":
            url = scrobblers.librefm_base(body.url)
            if not body.username or not body.password:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the username and password."
                )
            try:
                secret, username = await scrobblers.LibreFmClient(http, url).session(
                    body.username.strip(), body.password
                )
            except ListenBrainzError as exc:
                code = (
                    status.HTTP_400_BAD_REQUEST
                    if exc.status == 401
                    else status.HTTP_502_BAD_GATEWAY
                )
                raise HTTPException(code, str(exc)) from exc
            name: str | None = username
        else:
            if not body.url or not body.token:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the address and token."
                )
            url = scrobblers.listenbrainz_base(body.url)
            secret = body.token.strip()
            name = await scrobblers.check_listenbrainz(http, url, secret)
    except scrobblers.ScrobblerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    target = await db.scalar(
        select(ScrobbleTarget).where(
            ScrobbleTarget.user_id == user.id,
            ScrobbleTarget.kind == kind,
            ScrobbleTarget.api_url == url,
        )
    )
    if target is None:
        count = len(
            list(
                await db.scalars(select(ScrobbleTarget.id).where(ScrobbleTarget.user_id == user.id))
            )
        )
        if count >= MAX_TARGETS:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"At most {MAX_TARGETS} scrobble services."
            )
        target = ScrobbleTarget(
            user_id=user.id,
            kind=kind,
            service=body.service,
            api_url=url,
            enabled_at=dt.datetime.now(dt.UTC),
            submitted_through_id=0,
            sent_count=0,
            paused=False,
        )
        db.add(target)
    target.token_enc = scrobblers.encrypt(settings, secret)
    target.username = name
    target.paused, target.last_error = False, None
    await db.commit()
    await db.refresh(target)
    await enqueue_listenbrainz([user.id])
    return await _out(db, target)


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_target(target_id: int, db: DbDep, user: UserDep) -> Response:
    target = await db.get(ScrobbleTarget, target_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scrobble service not found")
    await db.delete(target)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
