"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.auth.sessions import SESSION_COOKIE, load_session, set_session_cookie
from plekstender.config import Settings, get_settings
from plekstender.db import get_db
from plekstender.db.models import User
from plekstender.plex.tv import PlexTvClient
from plekstender.security.crypto import Signer, TokenCipher

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def get_http(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http
    return client


HttpDep = Annotated[httpx.AsyncClient, Depends(get_http)]


def get_cipher(settings: SettingsDep) -> TokenCipher:
    return TokenCipher(settings.derive_key("token-encryption"))


def get_signer(settings: SettingsDep) -> Signer:
    return Signer(settings.derive_key("cookie-signing"))


def get_plex_tv(settings: SettingsDep, http: HttpDep) -> PlexTvClient:
    return PlexTvClient(settings, http)


CipherDep = Annotated[TokenCipher, Depends(get_cipher)]
SignerDep = Annotated[Signer, Depends(get_signer)]
PlexTvDep = Annotated[PlexTvClient, Depends(get_plex_tv)]


async def current_user(
    request: Request, response: Response, db: DbDep, settings: SettingsDep
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    loaded = await load_session(db, token, settings)
    if loaded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    session, renewed = loaded
    if renewed:
        set_session_cookie(response, token, settings)
    return session.user


UserDep = Annotated[User, Depends(current_user)]


async def require_admin(user: UserDep) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user


AdminDep = Annotated[User, Depends(require_admin)]
