"""Minimal Last.fm API client for reading a user's scrobbles."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

import httpx

# https://www.last.fm/api/errorcodes
USER_NOT_FOUND = 6
INVALID_API_KEY = 10
LOGIN_REQUIRED = 17
SUSPENDED_API_KEY = 26
TRANSIENT = {8, 11, 16, 29}  # operation failed, offline, temporary error, rate limit

KEY_REJECTED = (
    "Last.fm rejected the API key. Since 1 December 2026, Last.fm API keys only work if "
    "their owner has Last.fm Pro."
)


class LastFmError(Exception):
    def __init__(self, message: str, *, code: int | None = None, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


@dataclass(frozen=True)
class LastFmUser:
    name: str
    playcount: int
    registered: dt.datetime | None


@dataclass(frozen=True)
class Scrobble:
    listened_at: dt.datetime
    artist: str
    album: str | None
    track: str
    artist_mbid: str | None
    release_mbid: str | None
    recording_mbid: str | None


@dataclass(frozen=True)
class RecentTracksPage:
    scrobbles: list[Scrobble]
    page: int
    total_pages: int
    total: int


def _text(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("#text")
    if not isinstance(value, str):
        return None
    return value.strip() or None


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def clean_mbid(value: Any) -> str | None:
    """A well-formed MusicBrainz ID (lower case), or None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if _UUID.match(value) else None


def _mbid(value: Any) -> str | None:
    """An MBID given directly (track) or as {"mbid": ...} (artist, album)."""
    if isinstance(value, dict):
        value = value.get("mbid")
    return clean_mbid(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def parse_scrobble(item: dict[str, Any]) -> Scrobble | None:
    """One `recenttracks.track` entry; None for "now playing" and incomplete entries."""
    if (item.get("@attr") or {}).get("nowplaying") == "true":
        return None
    uts = _int((item.get("date") or {}).get("uts"), -1)
    artist, track = _text(item.get("artist")), _text(item.get("name"))
    if uts <= 0 or not artist or not track:
        return None
    return Scrobble(
        listened_at=dt.datetime.fromtimestamp(uts, dt.UTC),
        artist=artist,
        album=_text(item.get("album")),
        track=track,
        artist_mbid=_mbid(item.get("artist")),
        release_mbid=_mbid(item.get("album")),
        recording_mbid=_mbid(item.get("mbid")),
    )


class LastFmClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str, base_url: str) -> None:
        self._http = http
        self._api_key = api_key
        self._base_url = base_url

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        query = {"method": method, "api_key": self._api_key, "format": "json", **params}
        try:
            resp = await self._http.get(self._base_url, params=query, timeout=30)
        except httpx.HTTPError as exc:
            raise LastFmError("Could not reach Last.fm.", transient=True) from exc
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and "error" in data:
            code = _int(data["error"], -1)
            if code in (INVALID_API_KEY, SUSPENDED_API_KEY):
                raise LastFmError(KEY_REJECTED, code=code)
            message = str(data.get("message") or "Last.fm returned an error.")
            raise LastFmError(message, code=code, transient=code in TRANSIENT)
        if resp.status_code in (401, 403):
            raise LastFmError(KEY_REJECTED, code=INVALID_API_KEY)
        if resp.status_code >= 500 or resp.status_code == 429:
            raise LastFmError(f"Last.fm is unavailable (HTTP {resp.status_code}).", transient=True)
        if resp.status_code != 200 or not isinstance(data, dict):
            raise LastFmError(f"Unexpected response from Last.fm (HTTP {resp.status_code}).")
        return data

    async def check_key(self) -> None:
        """Raises LastFmError if the API key doesn't work."""
        await self.call("chart.getTopArtists", limit=1)

    async def user_info(self, username: str) -> LastFmUser:
        try:
            data = await self.call("user.getInfo", user=username)
        except LastFmError as exc:
            if exc.code == USER_NOT_FOUND:
                raise LastFmError(
                    f"Last.fm has no user called {username!r}.", code=exc.code
                ) from exc
            raise
        user = data.get("user") or {}
        registered = _int((user.get("registered") or {}).get("unixtime"), 0)
        return LastFmUser(
            name=str(user.get("name") or username),
            playcount=_int(user.get("playcount")),
            registered=dt.datetime.fromtimestamp(registered, dt.UTC) if registered else None,
        )

    async def recent_tracks(
        self,
        username: str,
        *,
        page: int,
        limit: int,
        from_ts: int | None,
        to_ts: int,
    ) -> RecentTracksPage:
        params: dict[str, Any] = {
            "user": username,
            "page": page,
            "limit": limit,
            "to": to_ts,
            "extended": 0,
        }
        if from_ts is not None:
            params["from"] = from_ts
        try:
            data = await self.call("user.getRecentTracks", **params)
        except LastFmError as exc:
            if exc.code == LOGIN_REQUIRED:
                raise LastFmError(
                    'This Last.fm profile hides its recent listening. Turn off "Hide recent '
                    "listening information\" in Last.fm's privacy settings and try again.",
                    code=exc.code,
                ) from exc
            raise
        recent = data.get("recenttracks") or {}
        items = recent.get("track") or []
        if isinstance(items, dict):  # a single scrobble isn't wrapped in a list
            items = [items]
        attr = recent.get("@attr") or {}
        scrobbles = [s for s in (parse_scrobble(i) for i in items if isinstance(i, dict)) if s]
        return RecentTracksPage(
            scrobbles=scrobbles,
            page=_int(attr.get("page"), page),
            total_pages=_int(attr.get("totalPages")),
            total=_int(attr.get("total")),
        )
