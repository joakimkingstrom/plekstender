"""Scrobbling to more services than ListenBrainz: any ListenBrainz-compatible server (Maloja,
Koito, Multi-Scrobbler…), Libre.fm (or another GNU FM server) and Last.fm (their 2.0 API).

Each is a `ScrobbleTarget` that sends the user's new Plex plays like the ListenBrainz link
does (the same loop: batches, refused plays skipped, a rejected token pauses it).

Libre.fm signs in with the username and password once (`auth.getMobileSession`, with
`authToken = md5(lower(username) + md5(password))`); only the session key it gives back is
kept. GNU FM accepts any 32-character API key and signature; they're still made properly.

Last.fm uses the instance's API key and shared secret (an admin's), and its web sign-in: the
person approves Plekstender on last.fm, which sends them back with a token that's swapped
for a session key (`auth.getSession`). Their password never passes through Plekstender.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import logging
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import __version__
from plekstender.artwork import fetch
from plekstender.config import Settings
from plekstender.db.instance import get_lastfm_api_key, get_lastfm_shared_secret
from plekstender.db.models import Listen, ScrobbleTarget
from plekstender.history.lastfm import KEY_REJECTED
from plekstender.history.listenbrainz import (
    ListenBrainzClient,
    ScrobbleServiceError,
    SubmitResult,
    send_pending,
)
from plekstender.security.crypto import TokenCipher, token_cipher

log = logging.getLogger(__name__)

PURPOSE = "user.scrobble_target"
LIBREFM_URL = "https://libre.fm/2.0/"
# Plekstender's own Last.fm-style application key and secret (GNU FM accepts any).
# MD5 is what the Audioscrobbler protocol uses (identifiers and request signatures), not a
# security choice of Plekstender's.
API_KEY = hashlib.md5(b"plekstender", usedforsecurity=False).hexdigest()
API_SECRET = hashlib.md5(b"plekstender-secret", usedforsecurity=False).hexdigest()
LIBREFM_BATCH = 50
LASTFM_AUTH_URL = "https://www.last.fm/api/auth/"
CONNECT_WINDOW = 3600  # seconds to approve Plekstender on last.fm
LASTFM_UNAVAILABLE = (
    "Last.fm scrobbling isn't set up on this server at the moment (an admin needs to add "
    "the API key and its shared secret). Plays wait until it is."
)

SERVICES: dict[str, tuple[str, str]] = {
    # service: (kind, label)
    "maloja": ("listenbrainz", "Maloja"),
    "koito": ("listenbrainz", "Koito"),
    "multi-scrobbler": ("listenbrainz", "Multi-Scrobbler"),
    "listenbrainz-other": ("listenbrainz", "ListenBrainz-compatible server"),
    "librefm": ("librefm", "Libre.fm"),
    "lastfm": ("lastfm", "Last.fm"),
}


class ScrobblerError(Exception):
    pass


def service_label(service: str) -> str:
    return SERVICES.get(service, ("", service))[1]


def listenbrainz_base(url: str) -> str:
    """The address the `/1/...` calls go under: what the server's docs give (e.g.
    https://maloja.example/apis/listenbrainz), without a trailing "/1" or slash."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ScrobblerError("Enter the server's address, starting with https:// (or http://).")
    for tail in ("/1/submit-listens", "/1"):
        if url.endswith(tail):
            url = url[: -len(tail)]
    return url


# Never a scrobble server: Plekstender's own services in its Compose stack, and this machine.
OWN_HOSTS = frozenset({"localhost", "db", "db-init", "frontend", "worker", "searxng", "traefik"})


def _never_a_server(address: str) -> bool:
    """This machine, link-local (which includes cloud metadata services) and other
    addresses nobody runs a scrobble server on. Private (home network) ones are fine."""
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved
    )


async def check_server_address(url: str) -> None:
    """Refuse addresses that can't be a scrobble server (see _never_a_server), so the server
    can't be pointed at itself or its own services. A name that doesn't resolve is left for
    the connection to report."""
    host = (urlsplit(url).hostname or "").lower()
    refused = ScrobblerError(
        "That address can't be a scrobble server: it's this machine or one of Plekstender's "
        "own services."
    )
    if host in OWN_HOSTS or host.endswith(".localhost"):
        raise refused
    try:
        addresses = await fetch.resolver(host)
    except OSError, UnicodeError:
        return
    if any(_never_a_server(a) for a in addresses):
        raise refused


def librefm_base(url: str | None) -> str:
    url = (url or LIBREFM_URL).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ScrobblerError("Enter the server's address, starting with https://.")
    return url if url.endswith("/") else url + "/"


async def check_listenbrainz(http: httpx.AsyncClient, base: str, token: str) -> str | None:
    """The account name the server says the token is for (None: it can't say, as some
    compatible servers don't answer `validate-token`). Raises if the token is refused."""
    client = ListenBrainzClient(http, base, token)
    try:
        resp = await client._request("GET", "/1/validate-token")
    except ScrobbleServiceError as exc:
        raise ScrobblerError("The server couldn't be reached.") from exc
    if resp.status_code in (404, 405, 501):
        return None
    if resp.status_code == 401:
        raise ScrobblerError("The server didn't accept the token.")
    if resp.status_code >= 400:
        raise ScrobblerError(f"The server answered {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("valid") is False:
        raise ScrobblerError("The server didn't accept the token.")
    name = data.get("user_name") if isinstance(data, dict) else None
    return str(name) if name else None


def _sign(params: dict[str, str], secret: str) -> str:
    """Last.fm's signature: md5 of the sorted name+value pairs and the secret."""
    text = "".join(f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback"))
    return hashlib.md5((text + secret).encode(), usedforsecurity=False).hexdigest()


class AudioscrobblerClient:
    """Last.fm's 2.0 API, as Last.fm itself and Libre.fm (GNU FM) answer it."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        session_key: str | None = None,
        *,
        api_key: str = API_KEY,
        api_secret: str = API_SECRET,
        name: str = "Libre.fm",
    ):
        self._http = http
        self._base = base_url
        self._sk = session_key
        self._key = api_key
        self._secret = api_secret
        self._name = name

    async def _call(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        name = self._name
        body = {"method": method, "api_key": self._key, **params}
        if self._sk:
            body["sk"] = self._sk
        body["api_sig"] = _sign(body, self._secret)
        body["format"] = "json"
        try:
            resp = await self._http.post(
                self._base,
                data=body,
                headers={"User-Agent": f"Plekstender/{__version__}"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise ScrobbleServiceError(f"{name} couldn't be reached.") from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            raise ScrobbleServiceError(
                f"{name} is unavailable (HTTP {resp.status_code}).", status=503
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ScrobbleServiceError(
                f"{name} sent an answer that isn't JSON.", status=503
            ) from exc
        if isinstance(data, dict) and "error" in data:
            code, message = data.get("error"), str(data.get("message") or "")
            # 4: wrong password, 9: session key no longer valid, 14/15: unauthorised token.
            if code in (4, 9, 14, 15):
                raise ScrobbleServiceError(f"{name}: {message or 'not signed in'}", status=401)
            # 10/26: the API key is refused or suspended, 13: the signature (the shared
            # secret) is wrong. Nothing to do with the plays or the person's session, so
            # they wait (not skipped, not paused) until an admin fixes it.
            if code in (10, 26):
                raise ScrobbleServiceError(
                    KEY_REJECTED if self._name == "Last.fm" else f"{name}: {message}", status=403
                )
            if code == 13:
                raise ScrobbleServiceError(
                    f"{name} didn't accept the signature: check the API key's shared secret.",
                    status=403,
                )
            if code in (11, 16, 29):  # service offline, temporary error, rate limited
                raise ScrobbleServiceError(f"{name}: {message or 'try again later'}", status=503)
            raise ScrobbleServiceError(f"{name} refused it: {message}", status=400)
        if not isinstance(data, dict):
            raise ScrobbleServiceError(f"{name} sent an unexpected answer.", status=503)
        return data

    @staticmethod
    def _session(data: dict[str, Any], fallback: str) -> tuple[str, str]:
        found = data.get("session")
        session: dict[str, Any] = found if isinstance(found, dict) else {}
        key = session.get("key")
        if not key:
            raise ScrobbleServiceError("No session was given.", status=401)
        return str(key), str(session.get("name") or fallback)

    async def session(self, username: str, password: str) -> tuple[str, str]:
        """Libre.fm: sign in once with the password: (session key, the account's name)."""
        password_md5 = hashlib.md5(password.encode(), usedforsecurity=False).hexdigest()
        token = hashlib.md5(
            (username.lower() + password_md5).encode(), usedforsecurity=False
        ).hexdigest()
        data = await self._call("auth.getMobileSession", {"username": username, "authToken": token})
        return self._session(data, username)

    async def web_session(self, token: str) -> tuple[str, str]:
        """Last.fm: the session for a token the person approved on last.fm (web sign-in)."""
        return self._session(await self._call("auth.getSession", {"token": token}), "")

    async def check(self) -> None:
        """Raises if the API key or its shared secret is refused (a signed call)."""
        await self._call("auth.getToken", {})

    async def send(self, listens: list[Listen]) -> None:
        for start in range(0, len(listens), LIBREFM_BATCH):
            params: dict[str, str] = {}
            for i, listen in enumerate(listens[start : start + LIBREFM_BATCH]):
                params[f"artist[{i}]"] = listen.artist_name
                params[f"track[{i}]"] = listen.track_name
                params[f"timestamp[{i}]"] = str(int(listen.listened_at.timestamp()))
                if listen.album_name:
                    params[f"album[{i}]"] = listen.album_name
                if listen.duration_ms:
                    params[f"duration[{i}]"] = str(listen.duration_ms // 1000)
                if listen.recording_mbid:
                    params[f"mbid[{i}]"] = listen.recording_mbid
            await self._call("track.scrobble", params)


def lastfm_auth_url(api_key: str, callback: str) -> str:
    """Where a person approves Plekstender on last.fm; it then comes back to `callback`
    with `token` added."""
    return f"{LASTFM_AUTH_URL}?{urlencode({'api_key': api_key, 'cb': callback})}"


def connect_state(settings: Settings, user_id: int, at: int | None = None) -> str:
    """Ties a Last.fm sign-in to the person who started it (and expires), so nobody can
    have someone else's Plekstender scrobble to their Last.fm account."""
    at = int(now().timestamp()) if at is None else at
    mac = hmac.new(settings.derive_key("lastfm-connect"), f"{user_id}.{at}".encode(), "sha256")
    return f"{at}.{mac.hexdigest()[:32]}"


def check_state(settings: Settings, user_id: int, state: str) -> bool:
    at, _, _ = state.partition(".")
    if not at.isdigit() or now().timestamp() - int(at) > CONNECT_WINDOW:
        return False
    return hmac.compare_digest(connect_state(settings, user_id, int(at)), state)


def _cipher(settings: Settings) -> TokenCipher:
    return token_cipher(settings)


def encrypt(settings: Settings, secret: str) -> str:
    return _cipher(settings).encrypt(secret, purpose=PURPOSE)


async def submit_targets(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, user_id: int
) -> dict[int, SubmitResult]:
    """Send the user's unsent plays to each of their (unpaused) scrobble targets."""
    results: dict[int, SubmitResult] = {}
    targets = list(
        await db.scalars(
            select(ScrobbleTarget).where(
                ScrobbleTarget.user_id == user_id, ScrobbleTarget.paused.is_(False)
            )
        )
    )
    for target in targets:
        secret = _cipher(settings).decrypt(target.token_enc, purpose=PURPOSE)
        client: Any
        if target.kind == "lastfm":
            api_key, _ = await get_lastfm_api_key(db, settings)
            api_secret = await get_lastfm_shared_secret(db, settings)
            if not api_key or not api_secret:
                # Waits (not paused) until an admin sets the key and secret again.
                target.last_error = LASTFM_UNAVAILABLE
                results[target.id] = SubmitResult(error=LASTFM_UNAVAILABLE)
                await db.commit()
                continue
            client = AudioscrobblerClient(
                http, target.api_url, secret, api_key=api_key, api_secret=api_secret, name="Last.fm"
            )
        elif target.kind == "librefm":
            client = AudioscrobblerClient(http, target.api_url, secret)
        else:
            client = ListenBrainzClient(http, target.api_url, secret)
        results[target.id] = await send_pending(db, target, client, service_label(target.service))
    return results


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
