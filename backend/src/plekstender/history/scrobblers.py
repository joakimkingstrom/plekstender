"""Scrobbling to more services than ListenBrainz: any ListenBrainz-compatible server (Maloja,
Koito, Multi-Scrobbler…) and Libre.fm (or another GNU FM server, Last.fm's 2.0 API).

Each is a `ScrobbleTarget` that sends the user's new Plex plays like the ListenBrainz link
does (the same loop: batches, refused plays skipped, a rejected token pauses it).

Libre.fm signs in with the username and password once (`auth.getMobileSession`, with
`authToken = md5(lower(username) + md5(password))`); only the session key it gives back is
kept. GNU FM accepts any 32-character API key and signature; they're still made properly.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import __version__
from plekstender.config import Settings
from plekstender.db.models import Listen, ScrobbleTarget
from plekstender.history.listenbrainz import (
    ListenBrainzClient,
    ListenBrainzError,
    SubmitResult,
    send_pending,
)
from plekstender.security.crypto import TokenCipher

log = logging.getLogger(__name__)

PURPOSE = "user.scrobble_target"
LIBREFM_URL = "https://libre.fm/2.0/"
# Plekstender's own Last.fm-style application key and secret (GNU FM accepts any).
API_KEY = hashlib.md5(b"plekstender").hexdigest()  # noqa: S324 - an identifier, not security
API_SECRET = hashlib.md5(b"plekstender-secret").hexdigest()  # noqa: S324
LIBREFM_BATCH = 50

SERVICES: dict[str, tuple[str, str]] = {
    # service: (kind, label)
    "maloja": ("listenbrainz", "Maloja"),
    "koito": ("listenbrainz", "Koito"),
    "multi-scrobbler": ("listenbrainz", "Multi-Scrobbler"),
    "listenbrainz-other": ("listenbrainz", "ListenBrainz-compatible server"),
    "librefm": ("librefm", "Libre.fm"),
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
    except ListenBrainzError as exc:
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


def _sign(params: dict[str, str]) -> str:
    """Last.fm's signature: md5 of the sorted name+value pairs and the secret."""
    text = "".join(f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback"))
    return hashlib.md5((text + API_SECRET).encode()).hexdigest()  # noqa: S324 - the protocol's


class LibreFmClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, session_key: str | None = None):
        self._http = http
        self._base = base_url
        self._sk = session_key

    async def _call(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        body = {"method": method, "api_key": API_KEY, **params}
        if self._sk:
            body["sk"] = self._sk
        body["api_sig"] = _sign(body)
        body["format"] = "json"
        try:
            resp = await self._http.post(
                self._base,
                data=body,
                headers={"User-Agent": f"Plekstender/{__version__}"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise ListenBrainzError("Libre.fm couldn't be reached.") from exc
        if resp.status_code >= 500:
            raise ListenBrainzError(
                f"Libre.fm is unavailable (HTTP {resp.status_code}).", status=503
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ListenBrainzError("Libre.fm sent an answer that isn't JSON.", status=503) from exc
        if isinstance(data, dict) and "error" in data:
            code, message = data.get("error"), str(data.get("message") or "")
            # 4: wrong password, 9: session key no longer valid, 14/15: unauthorised token.
            if code in (4, 9, 14, 15):
                raise ListenBrainzError(f"Libre.fm: {message or 'not signed in'}", status=401)
            if code in (11, 16, 29):  # service offline, temporary error, rate limited
                raise ListenBrainzError(f"Libre.fm: {message or 'try again later'}", status=503)
            raise ListenBrainzError(f"Libre.fm refused it: {message}", status=400)
        if not isinstance(data, dict):
            raise ListenBrainzError("Libre.fm sent an unexpected answer.", status=503)
        return data

    async def session(self, username: str, password: str) -> tuple[str, str]:
        """Sign in once: (session key, the account's name)."""
        password_md5 = hashlib.md5(password.encode()).hexdigest()  # noqa: S324 - the protocol's
        token = hashlib.md5((username.lower() + password_md5).encode()).hexdigest()  # noqa: S324
        data = await self._call("auth.getMobileSession", {"username": username, "authToken": token})
        found = data.get("session")
        session: dict[str, Any] = found if isinstance(found, dict) else {}
        key = session.get("key")
        if not key:
            raise ListenBrainzError("Libre.fm didn't give a session.", status=401)
        return str(key), str(session.get("name") or username)

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


def _cipher(settings: Settings) -> TokenCipher:
    return TokenCipher(settings.derive_key("token-encryption"))


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
        client: Any = (
            LibreFmClient(http, target.api_url, secret)
            if target.kind == "librefm"
            else ListenBrainzClient(http, target.api_url, secret)
        )
        results[target.id] = await send_pending(db, target, client, service_label(target.service))
    return results


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
