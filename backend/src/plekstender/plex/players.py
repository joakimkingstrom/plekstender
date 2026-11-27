"""Plex players (Plexamp, the Plex apps) and remote control, the way Plex's own "cast" does it
(the companion protocol):

- Players are listed by plex.tv (the account's devices that provide "player") and by the
  server (`/clients`: players it's in touch with now). A player can be in either or both.
- A command (`playback/pause`...) goes through the server, which relays it to the player
  (`/player/...` with X-Plex-Target-Client-Identifier), or straight to the player, at an
  address plex.tv lists for it (players on the same network as Plekstender only).
- Playing something: a play queue on the server, then `playback/playMedia` saying where the
  server is and which queue to play.

Not every player still takes every route: Settings → Players tries them.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from plekstender.config import Settings
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError, Resource, plex_headers

Route = Literal["server", "direct"]
COMMANDS = {
    "play": "playback/play",
    "pause": "playback/pause",
    "next": "playback/skipNext",
    "previous": "playback/skipPrevious",
    "stop": "playback/stop",
}
DIRECT_TIMEOUT = httpx.Timeout(5.0, connect=3.0)

# Players ignore a command whose id isn't higher than the last one from the same controller:
# start from the time, so ids keep rising across restarts.
_command_ids = itertools.count(int(time.time()))


@dataclass
class Player:
    id: str
    name: str
    product: str | None = None
    platform: str | None = None
    version: str | None = None
    # Who lists it: plex.tv (the account's devices) and/or the server (in touch with it now).
    on_account: bool = False
    on_server: bool = False
    online: bool = False
    # Where it can be reached directly (plex.tv's addresses, then the server's).
    addresses: list[str] = field(default_factory=list)
    token: str | None = None  # its own access token, when plex.tv gives one
    playing: dict[str, Any] | None = None


@dataclass
class Sent:
    ok: bool
    route: Route
    detail: str


def _from_resource(resource: Resource) -> Player:
    # Its local addresses first; never relays (they don't carry player commands).
    connections = sorted(resource.connections, key=lambda c: not c.local)
    return Player(
        id=resource.client_identifier,
        name=resource.name,
        product=resource.product,
        platform=resource.platform,
        version=resource.product_version,
        on_account=True,
        online=resource.presence,
        addresses=[c.uri for c in connections if not c.relay],
        token=resource.access_token,
    )


def merge(
    resources: list[Resource], clients: list[dict[str, Any]], sessions: list[dict[str, Any]]
) -> list[Player]:
    """The players, by id: from plex.tv and the server's clients, with what each is playing."""
    found: dict[str, Player] = {}
    for resource in resources:
        if resource.is_player:
            found[resource.client_identifier] = _from_resource(resource)
    for client in clients:
        player_id = str(client.get("machineIdentifier") or "")
        if not player_id:
            continue
        player = found.setdefault(
            player_id,
            Player(
                id=player_id,
                name=str(client.get("name") or client.get("title") or player_id),
                product=client.get("product"),
                platform=client.get("platform"),
                version=client.get("version"),
            ),
        )
        player.on_server = player.online = True
        if client.get("address") and client.get("port"):
            scheme = "https" if str(client.get("protocol", "")).endswith("s") else "http"
            player.addresses.append(f"{scheme}://{client['address']}:{client['port']}")
    for session in sessions:
        about = session.get("Player") or {}
        listed = found.get(str(about.get("machineIdentifier") or ""))
        if listed is None or session.get("type") != "track":
            continue
        listed.online = True
        listed.playing = {
            "title": session.get("title"),
            "artist": session.get("originalTitle") or session.get("grandparentTitle"),
            "album": session.get("parentTitle"),
            "state": about.get("state"),
            "rating_key": int(session["ratingKey"]) if session.get("ratingKey") else None,
        }
    # Those playing something first, then those online.
    return sorted(found.values(), key=lambda p: (p.playing is None, not p.online, p.name.lower()))


def _why(exc: Exception) -> str:
    if isinstance(exc, PlexError) and exc.status:
        return f"HTTP {exc.status}"
    if isinstance(exc, httpx.TimeoutException):
        return "no answer (timed out)"
    if isinstance(exc, httpx.HTTPError):
        return "couldn't connect"
    return str(exc)


async def send(
    settings: Settings,
    http: httpx.AsyncClient,
    server: PlexServerClient,
    player: Player,
    route: Route,
    command: str,
    params: dict[str, Any] | None = None,
    account_token: str | None = None,
) -> Sent:
    """Send a player a command, by one route. Never raises: says what happened."""
    params = {**(params or {}), "type": "music", "commandID": next(_command_ids)}
    if route == "server":
        try:
            await server.player_command(player.id, command, params)
        except (PlexError, httpx.HTTPError) as exc:
            return Sent(False, route, f"The server didn't pass it on: {_why(exc)}.")
        return Sent(True, route, "The server passed it on.")
    if not player.addresses:
        return Sent(False, route, "Plex lists no address for this player.")
    headers = plex_headers(settings, player.token or account_token)
    headers["X-Plex-Target-Client-Identifier"] = player.id
    tried = []
    for address in player.addresses:
        try:
            resp = await http.get(
                f"{address.rstrip('/')}/player/{command}",
                params=params,
                headers=headers,
                timeout=DIRECT_TIMEOUT,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            tried.append(f"{urlsplit(address).netloc}: {_why(exc)}")
            continue
        if resp.is_success:
            return Sent(True, route, f"The player took it ({urlsplit(address).netloc}).")
        tried.append(f"{urlsplit(address).netloc}: HTTP {resp.status_code}")
    return Sent(False, route, "The player didn't take it: " + "; ".join(tried) + ".")


def play_media_params(
    server_uri: str, machine_id: str, server_token: str, queue_id: int, key: str
) -> dict[str, Any]:
    """What `playback/playMedia` needs: where the server is, a token for it, and the queue."""
    where = urlsplit(server_uri)
    return {
        "machineIdentifier": machine_id,
        "protocol": where.scheme,
        "address": where.hostname,
        "port": where.port or (443 if where.scheme == "https" else 32400),
        "token": server_token,
        "key": key,
        "offset": 0,
        "containerKey": f"/playQueues/{queue_id}?window=100&own=1",
    }
