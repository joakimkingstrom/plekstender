"""More places to scrobble to (each person's own): ListenBrainz-compatible servers such as
Maloja, Koito or Multi-Scrobbler, Libre.fm and Last.fm. New Plex plays from when one is
added are sent there, like to ListenBrainz."""

from __future__ import annotations

import datetime as dt
import logging
from typing import cast
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from plekstender.api.deps import DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import (
    LastFmScrobblingOut,
    ScrobbleService,
    ScrobbleTargetIn,
    ScrobbleTargetOut,
)
from plekstender.db.instance import get_lastfm_api_key, get_lastfm_shared_secret
from plekstender.db.models import ScrobbleTarget, User
from plekstender.history import scrobblers
from plekstender.history.listenbrainz import ListenBrainzError, count_pending
from plekstender.worker import enqueue_listenbrainz

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scrobblers", tags=["listens"])
MAX_TARGETS = 10
# Where the Last.fm sign-in comes back to (with ?lastfm=connected, or a reason it didn't).
BACK = "/settings/scrobbling"


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
    if kind == "lastfm":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Last.fm is added by signing in there: GET /api/scrobblers/lastfm/connect.",
        )
    try:
        if kind == "librefm":
            url = scrobblers.librefm_base(body.url)
            if not body.username or not body.password:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the username and password."
                )
            try:
                secret, username = await scrobblers.AudioscrobblerClient(http, url).session(
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

    target = await _save(db, settings, user, body.service, url, secret, name)
    return await _out(db, target)


async def _save(
    db: DbDep,
    settings: SettingsDep,
    user: User,
    service: str,
    url: str,
    secret: str,
    name: str | None,
) -> ScrobbleTarget:
    """Add the service, or (same address) replace its token and resume it where it stopped."""
    kind = scrobblers.SERVICES[service][0]
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
            service=service,
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
    return target


async def _lastfm_keys(db: DbDep, settings: SettingsDep) -> tuple[str, str] | None:
    key, _ = await get_lastfm_api_key(db, settings)
    secret = await get_lastfm_shared_secret(db, settings)
    return (key, secret) if key and secret else None


@router.get("/lastfm", response_model=LastFmScrobblingOut)
async def lastfm_scrobbling(db: DbDep, settings: SettingsDep, _: UserDep) -> LastFmScrobblingOut:
    """Whether people can add Last.fm (an admin has set the API key and its secret)."""
    return LastFmScrobblingOut(available=await _lastfm_keys(db, settings) is not None)


def _back(**params: str) -> RedirectResponse:
    return RedirectResponse(f"{BACK}?{urlencode(params)}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/lastfm/connect", response_class=RedirectResponse, status_code=303)
async def lastfm_connect(db: DbDep, settings: SettingsDep, user: UserDep) -> RedirectResponse:
    """Off to last.fm to approve Plekstender; it comes back to the callback below."""
    keys = await _lastfm_keys(db, settings)
    if keys is None:
        return _back(lastfm="unavailable")
    state = scrobblers.connect_state(settings, user.id)
    callback = f"{settings.app_url}/api/scrobblers/lastfm/callback?{urlencode({'state': state})}"
    return RedirectResponse(
        scrobblers.lastfm_auth_url(keys[0], callback), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get(
    "/lastfm/callback",
    response_class=RedirectResponse,
    status_code=303,
    dependencies=[rate_limit("lastfm-connect", 10, 60)],
)
async def lastfm_callback(
    db: DbDep,
    http: HttpDep,
    settings: SettingsDep,
    user: UserDep,
    state: str = Query(max_length=100),
    token: str = Query(max_length=100),
) -> RedirectResponse:
    """Back from last.fm: swap the approved token for a session key and add Last.fm."""
    if not scrobblers.check_state(settings, user.id, state):
        return _back(lastfm="expired")
    keys = await _lastfm_keys(db, settings)
    if keys is None:
        return _back(lastfm="unavailable")
    client = scrobblers.AudioscrobblerClient(
        http, settings.lastfm_api_url, api_key=keys[0], api_secret=keys[1], name="Last.fm"
    )
    try:
        secret, name = await client.web_session(token)
    except ListenBrainzError as exc:
        log.warning("Last.fm sign-in failed for user %s: %s", user.id, exc)
        return _back(lastfm="refused" if exc.status in (400, 401) else "failed")
    try:
        await _save(db, settings, user, "lastfm", settings.lastfm_api_url, secret, name or None)
    except HTTPException:
        return _back(lastfm="full")
    return _back(lastfm="connected")


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_target(target_id: int, db: DbDep, user: UserDep) -> Response:
    target = await db.get(ScrobbleTarget, target_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scrobble service not found")
    await db.delete(target)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
