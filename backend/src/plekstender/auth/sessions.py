"""Server-side sessions referenced by an opaque, httpOnly cookie."""

from __future__ import annotations

import datetime as dt
import secrets

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from plekstender.config import Settings
from plekstender.db.models import Session, User
from plekstender.security.crypto import hash_session_token

SESSION_COOKIE = "plekstender_session"
PIN_COOKIE = "plekstender_pin"
# Only extend a session's expiry when it's been at least this long since the last extension.
RENEW_AFTER = dt.timedelta(hours=1)


async def create_session(
    db: AsyncSession, user: User, settings: Settings, user_agent: str | None
) -> str:
    token = secrets.token_urlsafe(32)
    now = dt.datetime.now(dt.UTC)
    db.add(
        Session(
            id=hash_session_token(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + dt.timedelta(days=settings.session_ttl_days),
            user_agent=(user_agent or "")[:512] or None,
        )
    )
    return token


async def load_session(
    db: AsyncSession, token: str, settings: Settings
) -> tuple[Session, bool] | None:
    """Returns (session, renewed) for a valid token, or None. Expired sessions are deleted."""
    session = await db.scalar(
        select(Session)
        .options(joinedload(Session.user))
        .where(Session.id == hash_session_token(token))
    )
    if session is None:
        return None
    now = dt.datetime.now(dt.UTC)
    if session.expires_at <= now:
        await db.delete(session)
        await db.commit()
        return None
    renewed = False
    if now - session.last_seen_at >= RENEW_AFTER:
        session.last_seen_at = now
        session.expires_at = now + dt.timedelta(days=settings.session_ttl_days)
        await db.commit()
        renewed = True
    return session, renewed


async def delete_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(Session).where(Session.id == hash_session_token(token)))


async def delete_expired_sessions(db: AsyncSession) -> int:
    result = await db.execute(delete(Session).where(Session.expires_at <= dt.datetime.now(dt.UTC)))
    return int(getattr(result, "rowcount", 0) or 0)


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, secure=settings.secure_cookies, samesite="lax", path="/"
    )


async def end_sessions_without_access(db: AsyncSession) -> int:
    """After the access rules change: sign out the people who no longer qualify, rather than
    waiting for their sessions to run out. Admins always qualify. With approved servers,
    "qualifies" means they've connected an approved server here (anyone who has access but
    hasn't connected one just signs in again). Returns how many sessions ended."""
    from plekstender.config import AccessMode
    from plekstender.db.instance import get_access_settings
    from plekstender.db.models import PlexServer, UserServer

    access = await get_access_settings(db)
    if access.mode == AccessMode.OPEN:
        return 0
    users = list(await db.scalars(select(User).where(User.is_admin.is_(False))))
    if access.mode == AccessMode.ALLOWLIST:
        listed = {x.lower() for x in access.allowlist}
        out = [
            u.id
            for u in users
            if not ({(u.username or "").lower(), (u.email or "").lower()} & listed)
        ]
    else:
        approved = set(
            await db.scalars(
                select(UserServer.user_id)
                .join(PlexServer, PlexServer.id == UserServer.server_id)
                .where(PlexServer.approved.is_(True))
            )
        )
        out = [u.id for u in users if u.id not in approved]
    if not out:
        return 0
    result = await db.execute(delete(Session).where(Session.user_id.in_(out)))
    return int(getattr(result, "rowcount", 0) or 0)
