"""Sign in with Plex (PIN flow), sign out, current user."""

from __future__ import annotations

import datetime as dt
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select, text

from plekstender.api.deps import (
    CipherDep,
    DbDep,
    PlexTvDep,
    SettingsDep,
    SignerDep,
    UserDep,
)
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import PinCheckOut, PinStartOut, Preferences, UserOut
from plekstender.auth.access import AccessDecision, decide_access, decide_bootstrap
from plekstender.auth.sessions import (
    PIN_COOKIE,
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    delete_session,
    set_session_cookie,
)
from plekstender.config import AccessMode
from plekstender.db.instance import get_access_settings
from plekstender.db.models import PlexServer, User
from plekstender.plex.tv import PlexAccount, PlexError, PlexTvClient

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

PIN_MAX_AGE = 15 * 60
# Serialises first-user bootstrap so two simultaneous sign-ins can't both become admin.
BOOTSTRAP_LOCK_ID = 0x524F414449  # "ROADI"


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        thumb_url=user.thumb_url,
        is_admin=user.is_admin,
        preferences=load_preferences(user),
    )


def load_preferences(user: User) -> Preferences:
    """The stored preferences, dropping any that are no longer valid."""
    stored = user.preferences or {}
    valid = {}
    for name in Preferences.model_fields:
        if name in stored:
            try:
                Preferences.model_validate({name: stored[name]})
            except ValueError:
                continue
            valid[name] = stored[name]
    return Preferences.model_validate(valid)


@router.post("/plex/pin", response_model=PinStartOut, dependencies=[rate_limit("pin", 10, 60)])
async def start_plex_pin(
    response: Response, plex: PlexTvDep, signer: SignerDep, settings: SettingsDep
) -> PinStartOut:
    try:
        pin = await plex.create_pin()
    except (httpx.HTTPError, PlexError) as exc:
        log.warning("could not create Plex PIN: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach plex.tv") from exc
    response.set_cookie(
        PIN_COOKIE,
        signer.sign(str(pin.id)),
        max_age=PIN_MAX_AGE,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/api/auth",
    )
    return PinStartOut(auth_url=plex.auth_url(pin, f"{settings.app_url}/login/callback"))


@router.post(
    "/plex/check", response_model=PinCheckOut, dependencies=[rate_limit("pin-check", 120, 60)]
)
async def check_plex_pin(
    request: Request,
    response: Response,
    db: DbDep,
    plex: PlexTvDep,
    signer: SignerDep,
    cipher: CipherDep,
    settings: SettingsDep,
) -> PinCheckOut:
    signed = request.cookies.get(PIN_COOKIE)
    pin_id = signer.unsign(signed, max_age=PIN_MAX_AGE) if signed else None
    if pin_id is None:
        return PinCheckOut(status="expired", detail="Sign-in timed out, please try again.")

    try:
        pin = await plex.check_pin(int(pin_id))
        if pin is None:
            response.delete_cookie(PIN_COOKIE, path="/api/auth")
            return PinCheckOut(status="expired", detail="Sign-in timed out, please try again.")
        if not pin.auth_token:
            return PinCheckOut(status="pending")
        token = pin.auth_token
        account = await plex.get_account(token)
        decision, user = await _admit(db, plex, account, token, settings.initial_admin)
    except (httpx.HTTPError, PlexError) as exc:
        log.warning("Plex sign-in failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach plex.tv") from exc

    response.delete_cookie(PIN_COOKIE, path="/api/auth")
    if not decision.allowed:
        await db.rollback()
        log.info("sign-in denied for Plex account %s", account.id)
        return PinCheckOut(status="denied", detail=decision.reason)

    now = dt.datetime.now(dt.UTC)
    if user is None:
        user = User(plex_account_id=account.id, plex_token_enc="")
        db.add(user)
    user.plex_uuid = account.uuid
    user.username = account.username
    user.email = account.email
    user.thumb_url = account.thumb
    user.plex_token_enc = cipher.encrypt(token, purpose="user.plex_token")
    user.is_admin = user.is_admin or decision.make_admin
    user.last_login_at = now
    await db.flush()
    session_token = await create_session(db, user, settings, request.headers.get("user-agent"))
    await db.commit()
    set_session_cookie(response, session_token, settings)
    log.info("user %s signed in (admin=%s)", user.id, user.is_admin)
    return PinCheckOut(status="ok", user=user_out(user))


async def _admit(
    db: DbDep, plex: PlexTvClient, account: PlexAccount, token: str, initial_admin: str | None
) -> tuple[AccessDecision, User | None]:
    await db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": BOOTSTRAP_LOCK_ID})
    user = await db.scalar(select(User).where(User.plex_account_id == account.id))
    has_users = bool(await db.scalar(select(func.count()).select_from(User)))
    if not has_users:
        return decide_bootstrap(account, initial_admin), None

    access = await get_access_settings(db)
    accessible: set[str] | None = None
    approved: set[str] = set()
    is_admin = bool(user and user.is_admin)
    if access.mode == AccessMode.SERVERS and not is_admin:
        approved = set(
            await db.scalars(
                select(PlexServer.machine_identifier).where(PlexServer.approved.is_(True))
            )
        )
        accessible = {r.client_identifier for r in await plex.get_servers(token)}
    decision = decide_access(
        account,
        is_admin=is_admin,
        mode=access.mode,
        allowlist=access.allowlist,
        approved_server_ids=approved,
        accessible_server_ids=accessible,
    )
    return decision, user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbDep, settings: SettingsDep) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await delete_session(db, token)
        await db.commit()
    clear_session_cookie(response, settings)


@router.get("/me", response_model=UserOut)
async def me(user: UserDep) -> UserOut:
    return user_out(user)


@router.put("/me/preferences", response_model=UserOut)
async def put_preferences(body: Preferences, db: DbDep, user: UserDep) -> UserOut:
    """Replace the signed-in user's preferences."""
    user.preferences = body.model_dump()
    await db.commit()
    return user_out(user)
