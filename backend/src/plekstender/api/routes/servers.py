"""Connect Plex servers and choose music libraries."""

from __future__ import annotations

import datetime as dt
import logging
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from plekstender.api.deps import CipherDep, DbDep, HttpDep, PlexTvDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import (
    AvailableServerOut,
    ConnectServerIn,
    ConnectServerOut,
    ProbeOut,
    SectionOut,
    SelectSectionsIn,
    ServerOut,
)
from plekstender.config import AccessMode
from plekstender.db.instance import get_access_settings
from plekstender.db.models import PlexServer, SyncRun, User, UserItemState, UserServer
from plekstender.plex.server import PlexServerClient, choose_connection, client_for
from plekstender.plex.tv import PlexAuthError, PlexError
from plekstender.security.crypto import DecryptionError, TokenCipher
from plekstender.worker import enqueue_sync

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/servers", tags=["servers"])


def server_out(us: UserServer) -> ServerOut:
    return ServerOut(
        id=us.id,
        machine_identifier=us.server.machine_identifier,
        name=us.server.name,
        is_owner=us.is_owner,
        approved=us.server.approved,
        connection_kind=us.connection_kind,
        music_section_keys=us.music_section_keys,
        last_connected_at=us.last_connected_at,
    )


def user_plex_token(user: User, cipher: TokenCipher) -> str:
    try:
        return cipher.decrypt(user.plex_token_enc, purpose="user.plex_token")
    except DecryptionError as exc:
        # APP_SECRET_KEY changed: stored tokens can't be read, the user must sign in again.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again") from exc


async def get_user_server(db: DbDep, user: User, server_id: int) -> UserServer:
    us = await db.scalar(
        select(UserServer).where(UserServer.id == server_id, UserServer.user_id == user.id)
    )
    if us is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")
    return us


def server_client(
    us: UserServer, cipher: TokenCipher, settings: SettingsDep, http: httpx.AsyncClient
) -> PlexServerClient:
    try:
        return client_for(settings, http, us, cipher)
    except DecryptionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Reconnect this server") from exc


@router.get("", response_model=list[ServerOut])
async def list_servers(db: DbDep, user: UserDep) -> list[ServerOut]:
    rows = await db.scalars(
        select(UserServer).where(UserServer.user_id == user.id).order_by(UserServer.id)
    )
    return [server_out(us) for us in rows]


@router.get("/available", response_model=list[AvailableServerOut])
async def available_servers(
    db: DbDep, user: UserDep, plex: PlexTvDep, cipher: CipherDep
) -> list[AvailableServerOut]:
    try:
        resources = await plex.get_servers(user_plex_token(user, cipher))
    except PlexAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again") from exc
    except (httpx.HTTPError, PlexError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach plex.tv") from exc

    access = await get_access_settings(db)
    servers = {s.machine_identifier: s for s in await db.scalars(select(PlexServer))}
    connected = set(
        await db.scalars(
            select(PlexServer.machine_identifier)
            .join(UserServer)
            .where(UserServer.user_id == user.id)
        )
    )
    out = []
    for r in resources:
        known = servers.get(r.client_identifier)
        out.append(
            AvailableServerOut(
                machine_identifier=r.client_identifier,
                name=r.name,
                owned=r.owned,
                online=r.presence,
                version=r.product_version,
                connected=r.client_identifier in connected,
                allowed=_may_connect(user, access.mode, known),
            )
        )
    return out


def _may_connect(user: User, mode: AccessMode, server: PlexServer | None) -> bool:
    if user.is_admin or mode != AccessMode.SERVERS:
        return True
    return server is not None and server.approved


@router.post(
    "",
    response_model=ConnectServerOut,
    dependencies=[rate_limit("server-connect", 20, 60)],
)
async def connect_server(
    body: ConnectServerIn,
    db: DbDep,
    user: UserDep,
    plex: PlexTvDep,
    cipher: CipherDep,
    settings: SettingsDep,
    http: HttpDep,
) -> ConnectServerOut:
    """Connect (or re-connect, e.g. after an IP change) one of the user's Plex servers."""
    try:
        resources = await plex.get_servers(user_plex_token(user, cipher))
    except PlexAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again") from exc
    except (httpx.HTTPError, PlexError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach plex.tv") from exc
    resource = next((r for r in resources if r.client_identifier == body.machine_identifier), None)
    if resource is None or not resource.access_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That server isn't on your Plex account")

    server = await db.scalar(
        select(PlexServer).where(PlexServer.machine_identifier == resource.client_identifier)
    )
    access = await get_access_settings(db)
    if not _may_connect(user, access.mode, server):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This server hasn't been approved")

    chosen, probes = await choose_connection(settings, http, resource)
    probe_out = [
        ProbeOut(uri_host=urlsplit(p.uri).hostname or "", kind=p.kind, ok=p.ok, error=p.error)
        for p in probes
    ]
    if chosen is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Plekstender couldn't reach this server on any of its addresses.",
        )

    if server is None:
        server = PlexServer(machine_identifier=resource.client_identifier, name=resource.name)
        db.add(server)
    server.name = resource.name
    if resource.public_address and resource.owned:
        server.public_address = resource.public_address[:64]
    # An admin connecting a server they own approves it for their users automatically.
    if user.is_admin and resource.owned:
        server.approved = True
    await db.flush()

    us = await db.scalar(
        select(UserServer).where(UserServer.user_id == user.id, UserServer.server_id == server.id)
    )
    if us is None:
        us = UserServer(user_id=user.id, server_id=server.id, music_section_keys=[])
        db.add(us)
    us.server = server
    us.access_token_enc = cipher.encrypt(resource.access_token, purpose="user_server.access_token")
    us.is_owner = resource.owned
    us.connection_uri = chosen.uri
    us.connection_kind = chosen.kind
    us.last_connected_at = dt.datetime.now(dt.UTC)

    if not us.music_section_keys:
        client = PlexServerClient(settings, http, chosen.uri, resource.access_token)
        try:
            sections = await client.music_sections()
        except httpx.HTTPError, PlexError:
            sections = []
        if len(sections) == 1:
            us.music_section_keys = [sections[0].key]

    await db.commit()
    log.info("user %s connected server %s via %s", user.id, server.id, chosen.kind)
    if us.music_section_keys and not await _has_synced(db, us):
        await enqueue_sync(db, us, "full", "setup")
    return ConnectServerOut(server=server_out(us), probes=probe_out)


async def _has_synced(db: DbDep, us: UserServer) -> bool:
    return (
        await db.scalar(select(SyncRun.id).where(SyncRun.user_server_id == us.id).limit(1))
    ) is not None


@router.get("/{server_id}/sections", response_model=list[SectionOut])
async def list_sections(
    server_id: int,
    db: DbDep,
    user: UserDep,
    cipher: CipherDep,
    settings: SettingsDep,
    http: HttpDep,
) -> list[SectionOut]:
    us = await get_user_server(db, user, server_id)
    client = server_client(us, cipher, settings, http)
    try:
        sections = await client.music_sections()
    except PlexAuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Reconnect this server") from exc
    except (httpx.HTTPError, PlexError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach the server") from exc
    selected = set(us.music_section_keys)
    return [SectionOut(key=s.key, title=s.title, selected=s.key in selected) for s in sections]


@router.put("/{server_id}/sections", response_model=ServerOut)
async def select_sections(
    server_id: int,
    body: SelectSectionsIn,
    db: DbDep,
    user: UserDep,
    cipher: CipherDep,
    settings: SettingsDep,
    http: HttpDep,
) -> ServerOut:
    us = await get_user_server(db, user, server_id)
    client = server_client(us, cipher, settings, http)
    try:
        valid = {s.key for s in await client.music_sections()}
    except (httpx.HTTPError, PlexError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach the server") from exc
    unknown = set(body.keys) - valid
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown music library")
    previous = set(us.music_section_keys)
    us.music_section_keys = sorted(set(body.keys), key=body.keys.index)
    await db.commit()
    if us.music_section_keys and set(us.music_section_keys) != previous:
        # New libraries need a full sync before they show up.
        await enqueue_sync(db, us, "full", "setup")
    return server_out(us)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_server(server_id: int, db: DbDep, user: UserDep) -> None:
    us = await get_user_server(db, user, server_id)
    # Personal state is keyed by server, not connection: remove it explicitly.
    await db.execute(
        delete(UserItemState).where(
            UserItemState.user_id == user.id, UserItemState.server_id == us.server_id
        )
    )
    await db.delete(us)
    await db.commit()
