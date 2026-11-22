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
from plekstender.api.schemas import (
    PinCheckOut,
    PinStartIn,
    PinStartOut,
    Preferences,
    ProfileOut,
    ProfileServer,
    SetupOut,
    UserOut,
)
from plekstender.auth import setup
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
from plekstender.db.models import Gig, Listen, PlexServer, User, UserItemState, UserServer
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


@router.get("/setup", response_model=SetupOut)
async def setup_state(db: DbDep) -> SetupOut:
    """Whether the instance is claimed yet (if not, sign-in asks for the setup code)."""
    return SetupOut(claimed=await setup.is_claimed(db))


@router.post("/plex/pin", response_model=PinStartOut, dependencies=[rate_limit("pin", 10, 60)])
async def start_plex_pin(
    response: Response,
    db: DbDep,
    plex: PlexTvDep,
    signer: SignerDep,
    settings: SettingsDep,
    body: PinStartIn | None = None,
) -> PinStartOut:
    """Start signing in with Plex. While nobody has signed in, the setup code from the log
    is checked first; its hash goes along in the sign-in cookie for the claim."""
    setup_hash = ""
    if not await setup.is_claimed(db):
        if await setup.current_hash(db) is None:  # none made at start-up (e.g. no database yet)
            await setup.issue(db, settings.app_url)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A setup code has just been written to the web app's log. Enter it to continue.",
            )
        setup_hash = await setup.check(db, body.setup_code if body else None) or ""
        if not setup_hash:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "That isn't this Plekstender's setup code. It's in the web app's log "
                "(docker compose logs frontend); the newest one counts.",
            )
    try:
        pin = await plex.create_pin()
    except (httpx.HTTPError, PlexError) as exc:
        log.warning("could not create Plex PIN: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach plex.tv") from exc
    response.set_cookie(
        PIN_COOKIE,
        signer.sign(f"{pin.id}:{setup_hash}" if setup_hash else str(pin.id)),
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
    value = signer.unsign(signed, max_age=PIN_MAX_AGE) if signed else None
    pin_id, _, setup_hash = (value or "").partition(":")
    if not pin_id:
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
        decision, user = await _admit(
            db, plex, account, token, settings.initial_admin, setup_hash or None
        )
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
    if decision.make_admin:  # the first sign-in: the instance is claimed
        await setup.clear(db)
    await db.flush()
    session_token = await create_session(db, user, settings, request.headers.get("user-agent"))
    await db.commit()
    set_session_cookie(response, session_token, settings)
    log.info("user %s signed in (admin=%s)", user.id, user.is_admin)
    return PinCheckOut(status="ok", user=user_out(user))


async def _admit(
    db: DbDep,
    plex: PlexTvClient,
    account: PlexAccount,
    token: str,
    initial_admin: str | None,
    setup_hash: str | None = None,
) -> tuple[AccessDecision, User | None]:
    await db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": BOOTSTRAP_LOCK_ID})
    user = await db.scalar(select(User).where(User.plex_account_id == account.id))
    has_users = bool(await db.scalar(select(func.count()).select_from(User)))
    if not has_users:
        decision = decide_bootstrap(account, initial_admin)
        # The setup code entered when this sign-in started must still be the current one.
        current = await setup.current_hash(db)
        if decision.allowed and (not setup_hash or setup_hash != current):
            return AccessDecision(
                False,
                reason="Enter the setup code from the web app's log, then sign in again.",
            ), None
        return decision, None

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


@router.get("/me/profile", response_model=ProfileOut)
async def profile(db: DbDep, user: UserDep) -> ProfileOut:
    """The profile page: who you are here, and a few numbers."""
    servers = await db.scalars(
        select(UserServer).where(UserServer.user_id == user.id).order_by(UserServer.id)
    )
    listens = (
        await db.execute(
            select(func.count(), func.min(Listen.listened_at)).where(
                Listen.user_id == user.id, Listen.duplicate_of.is_(None)
            )
        )
    ).one()
    ratings = await db.scalar(
        select(func.count()).where(
            UserItemState.user_id == user.id, UserItemState.user_rating.is_not(None)
        )
    )
    gigs = await db.scalar(select(func.count()).where(Gig.user_id == user.id))
    return ProfileOut(
        username=user.username,
        email=user.email,
        thumb_url=user.thumb_url,
        is_admin=user.is_admin,
        joined_at=user.created_at,
        servers=[ProfileServer(name=us.server.name, is_owner=us.is_owner) for us in servers],
        scrobbles=listens[0],
        first_listen_at=listens[1],
        ratings=ratings or 0,
        gigs=gigs or 0,
    )


@router.put("/me/preferences", response_model=UserOut)
async def put_preferences(body: Preferences, db: DbDep, user: UserDep) -> UserOut:
    """Replace the signed-in user's preferences."""
    user.preferences = body.model_dump()
    await db.commit()
    return user_out(user)
