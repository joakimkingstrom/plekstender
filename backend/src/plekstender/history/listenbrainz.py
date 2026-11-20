"""Scrobbling Plex plays to ListenBrainz."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import __version__
from plekstender.config import Settings
from plekstender.db.models import Listen, ListenBrainzLink
from plekstender.security.crypto import TokenCipher

log = logging.getLogger(__name__)

TOKEN_PURPOSE = "user.listenbrainz_token"  # noqa: S105 - encryption purpose label, not a secret
BATCH = 100  # ListenBrainz allows up to 1000 per request
LISTEN_MINIMUM_TS = 1_033_430_400  # ListenBrainz rejects older timestamps


class ListenBrainzError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ListenBrainzClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Token {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(
                method, f"{self._base}{path}", headers=self._headers, timeout=30, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ListenBrainzError("Could not reach ListenBrainz.") from exc

    async def validate(self) -> str | None:
        """The token's ListenBrainz username, or None if the token isn't valid."""
        resp = await self._request("GET", "/1/validate-token")
        if resp.status_code != 200:
            raise ListenBrainzError(
                f"ListenBrainz is unavailable (HTTP {resp.status_code}).", status=resp.status_code
            )
        data = resp.json()
        return str(data["user_name"]) if data.get("valid") and data.get("user_name") else None

    async def send(self, listens: list[Listen]) -> None:
        await self.submit([listen_payload(listen) for listen in listens])

    async def submit(self, listens: list[dict[str, Any]]) -> None:
        listen_type = "single" if len(listens) == 1 else "import"
        resp = await self._request(
            "POST", "/1/submit-listens", json={"listen_type": listen_type, "payload": listens}
        )
        if resp.status_code == 200:
            return
        if resp.status_code == 401:
            raise ListenBrainzError("ListenBrainz rejected the token.", status=401)
        if resp.status_code == 400:
            try:
                detail = str(resp.json().get("error") or "")
            except ValueError:
                detail = ""
            raise ListenBrainzError(f"ListenBrainz refused a listen: {detail}", status=400)
        if resp.status_code == 429:
            wait = resp.headers.get("X-RateLimit-Reset-In", "?")
            raise ListenBrainzError(
                f"ListenBrainz asked to slow down (retry in {wait} s).", status=429
            )
        raise ListenBrainzError(
            f"ListenBrainz is unavailable (HTTP {resp.status_code}).", status=resp.status_code
        )


def listen_payload(listen: Listen) -> dict[str, Any]:
    info: dict[str, Any] = {
        "submission_client": "Plekstender",
        "submission_client_version": __version__,
    }
    if listen.source == "plex":
        info = {"media_player": "Plex", **info}
    if listen.duration_ms:
        info["duration_ms"] = listen.duration_ms
    for key in ("recording_mbid", "release_mbid"):
        if value := getattr(listen, key):
            info[key] = value
    if listen.artist_mbid:
        info["artist_mbids"] = [listen.artist_mbid]
    metadata: dict[str, Any] = {
        "artist_name": listen.artist_name,
        "track_name": listen.track_name,
        "additional_info": info,
    }
    if listen.album_name:
        metadata["release_name"] = listen.album_name
    return {"listened_at": int(listen.listened_at.timestamp()), "track_metadata": metadata}


class SendState(Protocol):
    """Where sending to a service stands (a ListenBrainz link or another scrobble target)."""

    user_id: int
    enabled_at: dt.datetime
    submitted_through_id: int
    sent_count: int
    last_submitted_at: dt.datetime | None
    last_error: str | None
    paused: bool


class Sender(Protocol):
    async def send(self, listens: list[Listen]) -> None: ...


def pending_filter(link: SendState) -> tuple[Any, ...]:
    """Listens still to send: the user's own Plex plays since scrobbling was turned on."""
    return (
        Listen.user_id == link.user_id,
        Listen.source == "plex",
        Listen.duplicate_of.is_(None),
        Listen.id > link.submitted_through_id,
        Listen.listened_at >= link.enabled_at,
        Listen.listened_at >= dt.datetime.fromtimestamp(LISTEN_MINIMUM_TS, dt.UTC),
    )


async def count_pending(db: AsyncSession, link: SendState) -> int:
    return await db.scalar(select(func.count()).where(*pending_filter(link))) or 0


@dataclass
class SubmitResult:
    sent: int = 0
    skipped: int = 0
    error: str | None = None


async def submit_pending(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, user_id: int
) -> SubmitResult:
    """Send a user's unsent Plex plays to ListenBrainz, oldest first, in batches.

    A listen ListenBrainz refuses (HTTP 400) is skipped so it can't block the rest. A
    rejected token pauses scrobbling; other failures are retried on the next run.
    """
    link = await db.get(ListenBrainzLink, user_id)
    if link is None or link.paused:
        return SubmitResult()
    cipher = TokenCipher(settings.derive_key("token-encryption"))
    client = ListenBrainzClient(
        http, settings.listenbrainz_api_url, cipher.decrypt(link.token_enc, purpose=TOKEN_PURPOSE)
    )
    return await send_pending(db, link, client, "ListenBrainz")


async def send_pending(
    db: AsyncSession, state: SendState, client: Sender, service: str
) -> SubmitResult:
    """Send a user's unsent Plex plays to a service, oldest first, in batches.

    A listen the service refuses (status 400) is skipped so it can't block the rest. A
    rejected token (401) pauses sending; other failures are retried on the next run.
    """
    result = SubmitResult()

    def handled(listen: Listen, sent: bool) -> None:
        state.submitted_through_id = listen.id
        if sent:
            state.sent_count += 1
            result.sent += 1
            state.last_submitted_at = dt.datetime.now(dt.UTC)
        else:
            result.skipped += 1

    while True:
        batch = list(
            await db.scalars(
                select(Listen).where(*pending_filter(state)).order_by(Listen.id).limit(BATCH)
            )
        )
        if not batch:
            break
        try:
            try:
                await client.send(batch)
                for listen in batch:
                    handled(listen, sent=True)
            except ListenBrainzError as exc:
                if exc.status != 400:
                    raise
                # Find the listen(s) it refuses by sending them one at a time.
                for listen in batch:
                    try:
                        await client.send([listen])
                        handled(listen, sent=True)
                    except ListenBrainzError as single:
                        if single.status != 400:
                            raise
                        log.warning("%s refused listen %s: %s", service, listen.id, single)
                        handled(listen, sent=False)
        except ListenBrainzError as exc:
            state.last_error = str(exc)
            if exc.status == 401:
                state.paused = True
                state.last_error = (
                    f"{service} rejected the token, so scrobbling is paused. "
                    "Add it again with a new token to resume."
                    if service != "ListenBrainz"
                    else "ListenBrainz rejected the token, so scrobbling is paused. "
                    "Paste a new token to resume."
                )
            result.error = state.last_error
            await db.commit()
            return result
        state.last_error = None
        await db.commit()
    return result
