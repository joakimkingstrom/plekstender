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

import asyncio
import ipaddress
import itertools
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree

from plekstender.config import Settings
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError, Resource, plex_headers

log = logging.getLogger(__name__)

Route = Literal["server", "direct"]
COMMANDS = {
    "play": "playback/play",
    "pause": "playback/pause",
    "next": "playback/skipNext",
    "previous": "playback/skipPrevious",
    "stop": "playback/stop",
    # With a value: where to go (ms), shuffle on/off, repeat (0 off, 1 the song, 2 all).
    "seek": "playback/seekTo",
    "shuffle": "playback/setParameters",
    "repeat": "playback/setParameters",
    # A song in the queue (its key, and its play queue item id).
    "skip_to": "playback/skipTo",
}
DIRECT_TIMEOUT = httpx.Timeout(5.0, connect=2.5)

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


# The address that last answered, per player: tried first next time.
_answered: dict[str, str] = {}


@dataclass
class Answer:
    resp: httpx.Response | None
    tried: list[str]
    # Nothing answered at all (timeouts, refused connections): a firewall, most likely.
    silent: bool


# 10-10-1-4.<hash>.plex.direct: the address is in the name. Read it from there rather than
# asking DNS (home routers often refuse plex.direct names, or take seconds to say so).
_PLEX_DIRECT = re.compile(r"^(\d{1,3}(?:-\d{1,3}){3})\.[0-9a-f]+\.plex\.direct$")
NAME_LOOKUP = 2.0  # seconds, for any other host name


def plain_address(uri: str) -> str:
    """A player's address with a plex.direct name turned into its IP address (over http:
    the certificate is for the name, and players answer plain http on their port)."""
    parts = urlsplit(uri)
    found = _PLEX_DIRECT.match(parts.hostname or "")
    if not found:
        return uri.rstrip("/")
    return f"http://{found.group(1).replace('-', '.')}:{parts.port or 32500}"


async def _usable(addresses: list[str]) -> list[str]:
    """The addresses Plekstender may send to, each once: never this machine or its own
    services (a player's addresses come from plex.tv, which anyone's devices can write
    to). IP addresses are checked as they are; other names are looked up (briefly)."""
    from plekstender.history.scrobblers import (
        OWN_HOSTS,
        ScrobblerError,
        _never_a_server,
        check_server_address,
    )

    usable: list[str] = []
    for address in dict.fromkeys(plain_address(a) for a in addresses):
        host = (urlsplit(address).hostname or "").lower()
        try:
            ipaddress.ip_address(host.split("%")[0])
        except ValueError:
            if host in OWN_HOSTS or host.endswith(".localhost"):
                continue
            try:
                await asyncio.wait_for(check_server_address(address), NAME_LOOKUP)
            except ScrobblerError, TimeoutError:
                continue
        else:
            if _never_a_server(host):
                continue
        usable.append(address)
    return usable


# When a player last went unanswered in the log, so a widget asking every few seconds
# doesn't fill it: once every few minutes per player, and once when it answers again.
LOG_EVERY = 300.0
_logged: dict[str, float] = {}


def _note(player: Player, tried: list[str]) -> None:
    now = time.monotonic()
    if now - _logged.get(player.id, -LOG_EVERY) >= LOG_EVERY:
        _logged[player.id] = now
        log.warning(
            "player %s (%s, %s) didn't answer: %s",
            player.name, player.product or "?", player.id,
            "; ".join(tried) or "no address Plekstender may use",
        )  # fmt: skip


async def _direct(
    settings: Settings, http: httpx.AsyncClient, player: Player, path: str,
    params: dict[str, Any], account_token: str | None,
) -> Answer:  # fmt: skip
    """GET a path on the player itself, at each of its addresses until one answers."""
    headers = plex_headers(settings, player.token or account_token)
    headers["X-Plex-Target-Client-Identifier"] = player.id
    addresses = await _usable(player.addresses)
    if (last := _answered.get(player.id)) in addresses:
        addresses = [last] + [a for a in addresses if a != last]
    tried: list[str] = []
    answered = False
    for address in addresses:
        try:
            resp = await http.get(
                f"{address}{path}",
                params=params,
                headers=headers,
                timeout=DIRECT_TIMEOUT,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            tried.append(f"{urlsplit(address).netloc}: {_why(exc)}")
            continue
        answered = True
        if resp.is_success:
            _answered[player.id] = address
            if _logged.pop(player.id, None) is not None:
                log.info("player %s answers again at %s", player.name, urlsplit(address).netloc)
            return Answer(resp, tried, False)
        tried.append(f"{urlsplit(address).netloc}: HTTP {resp.status_code}")
    _note(player, tried)
    return Answer(None, tried, bool(tried) and not answered)


def _silent(player: Player, tried: list[str]) -> str:
    return (
        f"No answer from {player.name} ({'; '.join(tried)}). Plexamp listens for remote "
        "control on port 32500: check that a firewall between Plekstender and the player "
        "lets it through, and that the player is on."
    )


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
    answer = await _direct(settings, http, player, f"/player/{command}", params, account_token)
    if answer.resp is not None:
        where = urlsplit(_answered[player.id]).netloc
        return Sent(True, route, f"The player took it ({where}).")
    if answer.silent:
        return Sent(False, route, _silent(player, answer.tried))
    if not answer.tried:
        return Sent(False, route, "Plex lists no address Plekstender may send to.")
    return Sent(False, route, "The player didn't take it: " + "; ".join(answer.tried) + ".")


# --- what a player is doing (its timeline) ----------------------------------------------------


@dataclass
class State:
    reachable: bool
    detail: str | None = None
    state: str | None = None  # playing, paused, buffering, stopped
    position_ms: int | None = None
    duration_ms: int | None = None
    rating_key: int | None = None
    key: str | None = None
    queue_id: int | None = None
    queue_item_id: int | None = None
    shuffle: bool = False
    repeat: int = 0  # 0 off, 1 the song, 2 all
    controllable: list[str] = field(default_factory=list)
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    thumb: str | None = None


def number(value: Any) -> int | None:
    """An int from Plex's strings and numbers (None when it isn't one)."""
    try:
        return int(float(value))
    except TypeError, ValueError:
        return None


def parse_timeline(body: bytes, content_type: str) -> State:
    """The music timeline a player answers `/player/timeline/poll` with (XML, or JSON)."""
    timelines: list[dict[str, Any]] = []
    if "json" in content_type:
        try:
            container = json.loads(body).get("MediaContainer", {})
        except ValueError, AttributeError:
            container = {}
        timelines = list(container.get("Timeline") or [])
    else:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError, ValueError:
            root = None
        for element in root.iter("Timeline") if root is not None else []:
            found: dict[str, Any] = dict(element.attrib)
            song = element.find("Track")
            if song is not None:
                found["Track"] = dict(song.attrib)
            timelines.append(found)
    music = next((t for t in timelines if t.get("type") == "music"), None)
    if music is None:
        return State(reachable=True, state="stopped")
    track: dict[str, Any] = music.get("Track") or music.get("Metadata") or {}
    if isinstance(track, list):
        track = track[0] if track else {}
    return State(
        reachable=True,
        state=music.get("state") or "stopped",
        position_ms=number(music.get("time")),
        duration_ms=number(music.get("duration")) or number(track.get("duration")),
        rating_key=number(music.get("ratingKey")) or number(track.get("ratingKey")),
        key=music.get("key") or track.get("key"),
        queue_id=number(music.get("playQueueID")),
        queue_item_id=number(music.get("playQueueItemID")),
        shuffle=str(music.get("shuffle", "0")) == "1",
        repeat=number(music.get("repeat")) or 0,
        controllable=[c for c in str(music.get("controllable") or "").split(",") if c],
        title=track.get("title"),
        artist=track.get("originalTitle") or track.get("grandparentTitle"),
        album=track.get("parentTitle"),
        thumb=track.get("parentThumb") or track.get("thumb"),
    )


async def timeline(
    settings: Settings, http: httpx.AsyncClient, server: PlexServerClient, player: Player,
    route: Route, account_token: str | None,
) -> State:  # fmt: skip
    """What the player is playing now, as it says itself: asked through the server, or
    straight."""
    params = {"wait": 0, "includeMetadata": 1, "commandID": next(_command_ids)}
    if route == "server":
        try:
            resp = await server.player_command(player.id, "timeline/poll", params)
        except (PlexError, httpx.HTTPError) as exc:
            return State(reachable=False, detail=f"The server couldn't ask it: {_why(exc)}.")
        return parse_timeline(resp.content, resp.headers.get("content-type", ""))
    answer = await _direct(settings, http, player, "/player/timeline/poll", params, account_token)
    if answer.resp is None:
        detail = (
            _silent(player, answer.tried)
            if answer.silent
            else "The player didn't answer: " + "; ".join(answer.tried) + "."
            if answer.tried
            else "Plex lists no address Plekstender may reach it at."
        )
        return State(reachable=False, detail=detail)
    return parse_timeline(answer.resp.content, answer.resp.headers.get("content-type", ""))


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
