"""Remote control of Plex players (Plexamp, the Plex apps): which ones the signed-in account
has, what they're playing, their queue, and commands for them (see plekstender.plex.players).

Commands go through the server when it's in touch with the player (its /clients: players
on its network, found by GDM), else straight to the player, at an address plex.tv lists.
Straight to players means requests from Plekstender to addresses on its own network: admins
only, unless an admin lets everyone."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.api.deps import AdminDep, CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.library import visible
from plekstender.api.routes.servers import get_user_server, server_client, user_plex_token
from plekstender.api.schemas import (
    PlayerAccessIn,
    PlayerAccessOut,
    PlayerCommandIn,
    PlayerNowOut,
    PlayerOut,
    PlayerPlayIn,
    PlayerQueueOut,
    PlayerSentOut,
    PlayersOut,
    PlayerStateOut,
    QueueItemOut,
)
from plekstender.db.instance import get_instance_value, put_instance_value
from plekstender.db.models import Album, PlexServer, Track, User
from plekstender.plex import players as plex_players
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError, PlexTvClient, Resource

router = APIRouter(prefix="/api/servers/{server_id}/players", tags=["players"])
access_router = APIRouter(prefix="/api/admin/players", tags=["players"])

ACCESS_KEY = "players"
# plex.tv's list of the account's devices, kept a minute (the widget asks every few seconds).
RESOURCES_FOR = 60.0
_resources: dict[int, tuple[float, list[Resource]]] = {}
# The server's players (/clients), kept a little while too, per server connection.
CLIENTS_FOR = 15.0
_clients: dict[int, tuple[float, list[dict[str, Any]]]] = {}


async def _access(db: AsyncSession) -> str:
    stored = await get_instance_value(db, ACCESS_KEY) or {}
    return "everyone" if stored.get("access") == "everyone" else "admins"


async def _may_direct(db: AsyncSession, user: User) -> bool:
    return user.is_admin or await _access(db) == "everyone"


async def _account_resources(
    user: User, settings: SettingsDep, http: HttpDep, cipher: CipherDep
) -> list[Resource]:
    cached = _resources.get(user.id)
    if cached and time.monotonic() - cached[0] < RESOURCES_FOR:
        return cached[1]
    try:
        found = await PlexTvClient(settings, http).get_resources(user_plex_token(user, cipher))
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "plex.tv couldn't be reached") from exc
    _resources[user.id] = (time.monotonic(), found)
    return found


async def _server_clients(us_id: int, server: PlexServerClient, fresh: bool) -> list[Any]:
    cached = _clients.get(us_id)
    if not fresh and cached and time.monotonic() - cached[0] < CLIENTS_FOR:
        return cached[1]
    found = await server.clients()
    _clients[us_id] = (time.monotonic(), found)
    return found


async def _players(
    user: User, us_id: int, server: PlexServerClient, settings: SettingsDep, http: HttpDep,
    cipher: CipherDep, *, sessions: bool = True,
) -> list[plex_players.Player]:  # fmt: skip
    resources = await _account_resources(user, settings, http, cipher)
    try:
        clients = await _server_clients(us_id, server, fresh=sessions)
        playing = await server.sessions() if sessions else []
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The server couldn't be reached") from exc
    return plex_players.merge(resources, clients, playing)


async def _player(
    user: User, us_id: int, server: PlexServerClient, player_id: str, settings: SettingsDep,
    http: HttpDep, cipher: CipherDep,
) -> plex_players.Player:  # fmt: skip
    """One player: its addresses (plex.tv's) and whether the server is in touch with it."""
    for player in await _players(user, us_id, server, settings, http, cipher, sessions=False):
        if player.id == player_id:
            return player
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Player not found")


def _out(player: plex_players.Player) -> PlayerOut:
    return PlayerOut(
        id=player.id,
        name=player.name,
        product=player.product,
        platform=player.platform,
        version=player.version,
        on_account=player.on_account,
        on_server=player.on_server,
        online=player.online,
        direct=bool(player.addresses),
        playing=PlayerNowOut(**player.playing) if player.playing else None,
    )


async def _check_route(db: AsyncSession, user: User, route: str) -> None:
    if route == "direct" and not await _may_direct(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only admins can control players on this instance"
        )


async def _target(
    db: AsyncSession, user: User, us_id: int, server: PlexServerClient, player_id: str,
    route: str, settings: SettingsDep, http: HttpDep, cipher: CipherDep,
) -> tuple[plex_players.Player, plex_players.Route]:  # fmt: skip
    """The player, and the way to it: "auto" is through the server when it's in touch with
    the player, else straight to it."""
    if route == "server":
        return plex_players.Player(id=player_id, name=player_id), "server"
    player = await _player(user, us_id, server, player_id, settings, http, cipher)
    chosen: plex_players.Route = "server" if route == "auto" and player.on_server else "direct"
    await _check_route(db, user, chosen)
    return player, chosen


@router.get("", response_model=PlayersOut)
async def list_players(
    server_id: int, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep,
    cipher: CipherDep,
) -> PlayersOut:  # fmt: skip
    """Your players (Plexamp, the Plex apps): those on your Plex account and those the server
    is in touch with, with what each is playing."""
    us = await get_user_server(db, user, server_id)
    server = server_client(us, cipher, settings, http)
    found = await _players(user, us.id, server, settings, http, cipher)
    return PlayersOut(players=[_out(p) for p in found], direct_allowed=await _may_direct(db, user))


@router.get("/{player_id}/state", response_model=PlayerStateOut)
async def player_state(
    server_id: int, player_id: str, db: DbDep, user: UserDep, settings: SettingsDep,
    http: HttpDep, cipher: CipherDep,
) -> PlayerStateOut:  # fmt: skip
    """What a player is playing, where it is in the song, shuffle and repeat: as the player
    says itself (asked straight)."""
    us = await get_user_server(db, user, server_id)
    server = server_client(us, cipher, settings, http)
    player, route = await _target(
        db, user, us.id, server, player_id, "auto", settings, http, cipher
    )
    state = await plex_players.timeline(
        settings, http, server, player, route, user_plex_token(user, cipher)
    )
    return PlayerStateOut(
        reachable=state.reachable, detail=state.detail, state=state.state,
        position_ms=state.position_ms, duration_ms=state.duration_ms,
        rating_key=state.rating_key, queue_id=state.queue_id, queue_item_id=state.queue_item_id,
        shuffle=state.shuffle, repeat=state.repeat, controllable=state.controllable,
        title=state.title, artist=state.artist, album=state.album, thumb=state.thumb,
    )  # fmt: skip


def _queue_item(item: dict[str, Any]) -> QueueItemOut | None:
    try:
        item_id = int(item["playQueueItemID"])
    except KeyError, TypeError, ValueError:
        return None
    rating_key = item.get("ratingKey")
    return QueueItemOut(
        item_id=item_id,
        key=str(item.get("key") or f"/library/metadata/{rating_key}"),
        rating_key=plex_players.number(rating_key),
        title=item.get("title"),
        artist=item.get("originalTitle") or item.get("grandparentTitle"),
        album=item.get("parentTitle"),
        duration_ms=item.get("duration"),
        thumb=item.get("parentThumb") or item.get("thumb"),
    )


@router.get("/{player_id}/queue", response_model=PlayerQueueOut)
async def player_queue(
    server_id: int, player_id: str, db: DbDep, user: UserDep, settings: SettingsDep,
    http: HttpDep, cipher: CipherDep, queue_id: int = Query(ge=1),
) -> PlayerQueueOut:  # fmt: skip
    """A player's queue (the play queue its state names), from the server."""
    us = await get_user_server(db, user, server_id)
    server = server_client(us, cipher, settings, http)
    try:
        container = await server.play_queue(queue_id)
    except PlexError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The server has no such queue") from exc
    items = [i for i in map(_queue_item, container.get("Metadata") or []) if i is not None]
    selected = container.get("playQueueSelectedItemID")
    return PlayerQueueOut(items=items, current_item_id=plex_players.number(selected))


def _command(body: PlayerCommandIn) -> tuple[str, dict[str, Any]]:
    command = plex_players.COMMANDS[body.command]
    if body.command in ("seek", "shuffle", "repeat", "skip_to") and body.value is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This command needs a value")
    if body.command == "seek":
        return command, {"offset": max(0, body.value or 0)}
    if body.command == "shuffle":
        return command, {"shuffle": 1 if body.value else 0}
    if body.command == "repeat":
        return command, {"repeat": min(max(body.value or 0, 0), 2)}
    if body.command == "skip_to":
        if body.key is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "skip_to needs the key")
        return command, {"key": body.key, "playQueueItemID": body.value}
    return command, {}


@router.post(
    "/{player_id}/command",
    response_model=PlayerSentOut,
    dependencies=[rate_limit("player-command", limit=120, window=60)],
)
async def player_command(
    server_id: int, player_id: str, body: PlayerCommandIn, db: DbDep, user: UserDep,
    settings: SettingsDep, http: HttpDep, cipher: CipherDep,
) -> PlayerSentOut:  # fmt: skip
    """Play, pause, skip, seek, shuffle, repeat, or go to a song in the queue."""
    command, params = _command(body)
    us = await get_user_server(db, user, server_id)
    server = server_client(us, cipher, settings, http)
    player, route = await _target(
        db, user, us.id, server, player_id, body.route, settings, http, cipher
    )
    sent = await plex_players.send(
        settings, http, server, player, route, command, params,
        account_token=user_plex_token(user, cipher),
    )  # fmt: skip
    return PlayerSentOut(ok=sent.ok, route=sent.route, detail=sent.detail)


@router.post(
    "/{player_id}/play",
    response_model=PlayerSentOut,
    dependencies=[rate_limit("player-command", limit=120, window=60)],
)
async def player_play(
    server_id: int, player_id: str, body: PlayerPlayIn, db: DbDep, user: UserDep,
    settings: SettingsDep, http: HttpDep, cipher: CipherDep,
) -> PlayerSentOut:  # fmt: skip
    """Play a song or an album on a player (a play queue on the server, then playMedia)."""
    us = await get_user_server(db, user, server_id)
    found = False
    for model in (Track, Album):
        state, cond, where = visible(model, user.id, us)
        found = found or bool(
            await db.scalar(
                select(model.rating_key)
                .join(state, cond)
                .where(*where, model.rating_key == body.rating_key)
            )
        )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Song or album not found")
    plex_server = await db.get(PlexServer, us.server_id)
    assert plex_server is not None
    server = server_client(us, cipher, settings, http)
    player, route = await _target(
        db, user, us.id, server, player_id, body.route, settings, http, cipher
    )
    try:
        queue_id, key = await server.create_play_queue(
            plex_server.machine_identifier, body.rating_key
        )
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Plex didn't make a play queue") from exc
    params = plex_players.play_media_params(
        server.base_uri, plex_server.machine_identifier, server.token, queue_id, key
    )
    sent = await plex_players.send(
        settings, http, server, player, route, "playback/playMedia", params,
        account_token=user_plex_token(user, cipher),
    )  # fmt: skip
    return PlayerSentOut(ok=sent.ok, route=sent.route, detail=sent.detail)


@access_router.get("", response_model=PlayerAccessOut)
async def get_player_access(db: DbDep, _: AdminDep) -> PlayerAccessOut:
    return PlayerAccessOut(access="everyone" if await _access(db) == "everyone" else "admins")


@access_router.put("", response_model=PlayerAccessOut)
async def put_player_access(body: PlayerAccessIn, db: DbDep, _: AdminDep) -> PlayerAccessOut:
    """Who may control players: admins only (the default), or everyone. Commands go straight
    from Plekstender to the players, on its own network."""
    await put_instance_value(db, ACCESS_KEY, {"access": body.access})
    await db.commit()
    return PlayerAccessOut(access=body.access)
