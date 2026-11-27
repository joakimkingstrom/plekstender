"""Remote control of Plex players (Plexamp, the Plex apps): which ones the signed-in account
has, what they're playing, and commands for them (see plekstender.plex.players)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.library import visible
from plekstender.api.routes.servers import get_user_server, server_client, user_plex_token
from plekstender.api.schemas import (
    PlayerCommandIn,
    PlayerNowOut,
    PlayerOut,
    PlayerPlayIn,
    PlayerSentOut,
    PlayersOut,
)
from plekstender.db.models import Album, PlexServer, Track, User, UserServer
from plekstender.plex import players as plex_players
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError, PlexTvClient

router = APIRouter(prefix="/api/servers/{server_id}/players", tags=["players"])


async def _players(
    user: User, us: UserServer, server: PlexServerClient, settings: SettingsDep, http: HttpDep,
    cipher: CipherDep,
) -> list[plex_players.Player]:  # fmt: skip
    tv = PlexTvClient(settings, http)
    try:
        resources = await tv.get_resources(user_plex_token(user, cipher))
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "plex.tv couldn't be reached") from exc
    try:
        clients = await server.clients()
        sessions = await server.sessions()
    except PlexError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The server couldn't be reached") from exc
    return plex_players.merge(resources, clients, sessions)


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


async def _player(
    user: User, us: UserServer, server: PlexServerClient, player_id: str, settings: SettingsDep,
    http: HttpDep, cipher: CipherDep,
) -> plex_players.Player:  # fmt: skip
    for player in await _players(user, us, server, settings, http, cipher):
        if player.id == player_id:
            return player
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Player not found")


def _check_route(user: User, route: str) -> None:
    # Straight to a player means requests to addresses on Plekstender's own network: the
    # admin's call (the server relays everyone else's).
    if route == "direct" and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can send straight to players")


@router.get("", response_model=PlayersOut)
async def list_players(
    server_id: int, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep,
    cipher: CipherDep,
) -> PlayersOut:  # fmt: skip
    """Your players (Plexamp, the Plex apps): those on your Plex account and those the server
    is in touch with, with what each is playing."""
    us = await get_user_server(db, user, server_id)
    server = server_client(us, cipher, settings, http)
    found = await _players(user, us, server, settings, http, cipher)
    return PlayersOut(players=[_out(p) for p in found], direct_allowed=user.is_admin)


@router.post(
    "/{player_id}/command",
    response_model=PlayerSentOut,
    dependencies=[rate_limit("player-command", limit=60, window=60)],
)
async def player_command(
    server_id: int, player_id: str, body: PlayerCommandIn, db: DbDep, user: UserDep,
    settings: SettingsDep, http: HttpDep, cipher: CipherDep,
) -> PlayerSentOut:  # fmt: skip
    """Play, pause, skip: through the server, or (admins) straight to the player."""
    _check_route(user, body.route)
    us = await get_user_server(db, user, server_id)
    server = server_client(us, cipher, settings, http)
    player = (
        await _player(user, us, server, player_id, settings, http, cipher)
        if body.route == "direct"
        else plex_players.Player(id=player_id, name=player_id)
    )
    sent = await plex_players.send(
        settings, http, server, player, body.route, plex_players.COMMANDS[body.command],
        account_token=user_plex_token(user, cipher),
    )  # fmt: skip
    return PlayerSentOut(ok=sent.ok, route=sent.route, detail=sent.detail)


@router.post(
    "/{player_id}/play",
    response_model=PlayerSentOut,
    dependencies=[rate_limit("player-command", limit=60, window=60)],
)
async def player_play(
    server_id: int, player_id: str, body: PlayerPlayIn, db: DbDep, user: UserDep,
    settings: SettingsDep, http: HttpDep, cipher: CipherDep,
) -> PlayerSentOut:  # fmt: skip
    """Play a song or an album on a player (a play queue on the server, then playMedia)."""
    _check_route(user, body.route)
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
    player = (
        await _player(user, us, server, player_id, settings, http, cipher)
        if body.route == "direct"
        else plex_players.Player(id=player_id, name=player_id)
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
        settings, http, server, player, body.route, "playback/playMedia", params,
        account_token=user_plex_token(user, cipher),
    )  # fmt: skip
    return PlayerSentOut(ok=sent.ok, route=sent.route, detail=sent.detail)
