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
