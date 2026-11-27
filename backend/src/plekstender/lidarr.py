"""Lidarr (https://lidarr.audio): adding albums and artists to someone's Lidarr, from Library →
Releases and Discover.

An admin connects it (Settings → Services → Lidarr): its address and API key (kept
encrypted), the root folder, quality and metadata profiles new artists get, and who may use
it: admins only, or everyone. Lidarr is usually on the same network as Plekstender, so its
address isn't held to the outbound fetcher's rules (it's an admin's choice, like Plex's).

Lidarr's API (v1) is asked with the key in the X-Api-Key header. Albums and artists are
found by MusicBrainz ID ("lidarr:<mbid>" lookups), which is what Releases and Discover have.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.instance import get_instance_value, put_instance_value
from plekstender.security.crypto import DecryptionError, TokenCipher, token_cipher

KEY = "lidarr"
PURPOSE = "instance.lidarr_api_key"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
# Lidarr asks every indexer while the request waits: that can take a while.
SEARCH_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

Access = Literal["admins", "everyone"]
ArtistMonitor = Literal["none", "future", "latest", "all"]


class LidarrError(Exception):
    """Lidarr couldn't be reached, refused, or answered something unexpected."""


class LidarrSettings(BaseModel):
    """What an admin set up (the API key is stored apart, encrypted)."""

    url: str
    root_folder: str | None = None
    quality_profile_id: int | None = None
    metadata_profile_id: int | None = None
    access: Access = "admins"


@dataclass
class Lidarr:
    """A connected Lidarr: its settings and a client for it."""

    settings: LidarrSettings
    client: LidarrClient


def _cipher(settings: Settings) -> TokenCipher:
    return token_cipher(settings)


def clean_url(url: str) -> str:
    """The address as given, without a trailing slash (a base path like /lidarr stays)."""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise LidarrError("Lidarr's address starts with http:// or https://.")
    return url


async def load(db: AsyncSession, settings: Settings) -> tuple[LidarrSettings, str] | None:
    """The saved settings and API key, or None when Lidarr isn't connected."""
    stored = await get_instance_value(db, KEY) or {}
    if not stored.get("url") or not stored.get("api_key_enc"):
        return None
    try:
        api_key = _cipher(settings).decrypt(stored["api_key_enc"], purpose=PURPOSE)
    except DecryptionError:
        return None
    return LidarrSettings.model_validate(stored), api_key


async def save(
    db: AsyncSession, settings: Settings, value: LidarrSettings | None, api_key: str | None
) -> None:
    """Save the settings (and the API key, when given: else the saved one stays); None
    disconnects."""
    if value is None:
        await put_instance_value(db, KEY, {})
        return
    stored = await get_instance_value(db, KEY) or {}
    encrypted = (
        _cipher(settings).encrypt(api_key, purpose=PURPOSE)
        if api_key
        else stored.get("api_key_enc")
    )
    await put_instance_value(db, KEY, {**value.model_dump(), "api_key_enc": encrypted})


async def connected(db: AsyncSession, settings: Settings, http: httpx.AsyncClient) -> Lidarr | None:
    found = await load(db, settings)
    if found is None:
        return None
    value, api_key = found
    return Lidarr(value, LidarrClient(http, value.url, api_key))


def may_use(value: LidarrSettings, is_admin: bool) -> bool:
    return is_admin or value.access == "everyone"


class LidarrClient:
    def __init__(self, http: httpx.AsyncClient, url: str, api_key: str) -> None:
        self.http = http
        self.url = clean_url(url)
        self.api_key = api_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        request_timeout: httpx.Timeout = TIMEOUT,
    ) -> Any:
        try:
            resp = await self.http.request(
                method,
                f"{self.url}/api/v1{path}",
                params=params,
                json=json,
                headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
                timeout=request_timeout,
            )
        except httpx.TimeoutException as exc:
            raise LidarrError("Lidarr took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise LidarrError(f"Couldn't reach Lidarr at {self.url}.") from exc
        if resp.status_code == 401:
            raise LidarrError("Lidarr didn't accept the API key.")
        if resp.status_code >= 400:
            raise LidarrError(_problem(resp))
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise LidarrError(f"That doesn't look like Lidarr at {self.url}.") from exc

    # --- set-up ------------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        found = await self._request("GET", "/system/status")
        if not isinstance(found, dict) or "version" not in found:
            raise LidarrError(f"That doesn't look like Lidarr at {self.url}.")
        return found

    async def root_folders(self) -> list[dict[str, Any]]:
        return list(await self._request("GET", "/rootfolder") or [])

    async def quality_profiles(self) -> list[dict[str, Any]]:
        return list(await self._request("GET", "/qualityprofile") or [])

    async def metadata_profiles(self) -> list[dict[str, Any]]:
        return list(await self._request("GET", "/metadataprofile") or [])

    # --- albums --------------------------------------------------------------------------

    async def album(self, mbid: str) -> dict[str, Any] | None:
        """The album in Lidarr with this release group MBID, if it's there."""
        found = await self._request("GET", "/album", params={"foreignAlbumId": mbid})
        return next((a for a in found or [] if a.get("foreignAlbumId") == mbid), None)

    async def lookup_album(self, mbid: str) -> dict[str, Any] | None:
        """The album as Lidarr's metadata server knows it (to add it)."""
        found = await self._request("GET", "/album/lookup", params={"term": f"lidarr:{mbid}"})
        return next((a for a in found or [] if a.get("foreignAlbumId") == mbid), None)

    async def add_album(
        self, found: dict[str, Any], value: LidarrSettings, *, search: bool
    ) -> dict[str, Any]:
        """Add a looked-up album, monitored (and its artist, unmonitored, if it's new to
        Lidarr: only this album is wanted)."""
        artist = dict(found.get("artist") or {})
        body = {
            **found,
            "monitored": True,
            "anyReleaseOk": True,
            "artist": {
                **artist,
                **_new_artist(value),
                "monitored": True,
                "monitorNewItems": "none",
                "addOptions": {"monitor": "none", "searchForMissingAlbums": False},
            },
            "addOptions": {"searchForNewAlbum": search},
        }
        added: dict[str, Any] = await self._request("POST", "/album", json=body)
        return added

    async def monitor(self, album_id: int) -> None:
        await self._request(
            "PUT", "/album/monitor", json={"albumIds": [album_id], "monitored": True}
        )

    async def search(self, album_id: int) -> None:
        """Ask Lidarr to look for the album on its indexers (and grab the best)."""
        await self._request(
            "POST", "/command", json={"name": "AlbumSearch", "albumIds": [album_id]}
        )

    async def releases(self, album_id: int) -> list[dict[str, Any]]:
        """What Lidarr's indexers have for the album (slow: it asks them all)."""
        found = await self._request(
            "GET", "/release", params={"albumId": album_id}, request_timeout=SEARCH_TIMEOUT
        )
        return list(found or [])

    async def grab(self, guid: str, indexer_id: int) -> None:
        """Download one of the releases found."""
        await self._request(
            "POST",
            "/release",
            json={"guid": guid, "indexerId": indexer_id},
            request_timeout=SEARCH_TIMEOUT,
        )

    # --- artists -------------------------------------------------------------------------

    async def lookup_artists(self, term: str) -> list[dict[str, Any]]:
        return list(await self._request("GET", "/artist/lookup", params={"term": term}) or [])

    async def artist(self, mbid: str) -> dict[str, Any] | None:
        found = await self._request("GET", "/artist", params={"mbId": mbid})
        return next((a for a in found or [] if a.get("foreignArtistId") == mbid), None)

    async def add_artist(
        self, found: dict[str, Any], value: LidarrSettings, *, monitor: ArtistMonitor, search: bool
    ) -> dict[str, Any]:
        body = {
            **found,
            **_new_artist(value),
            "monitored": monitor != "none",
            # Later albums: monitored unless only the existing ones were asked for.
            "monitorNewItems": "none" if monitor == "none" else "all",
            "addOptions": {"monitor": monitor, "searchForMissingAlbums": search},
        }
        added: dict[str, Any] = await self._request("POST", "/artist", json=body)
        return added


def _new_artist(value: LidarrSettings) -> dict[str, Any]:
    if not (value.root_folder and value.quality_profile_id and value.metadata_profile_id):
        raise LidarrError(
            "Lidarr isn't fully set up: an admin needs to choose the root folder and profiles "
            "(Settings → Services → Lidarr)."
        )
    return {
        "rootFolderPath": value.root_folder,
        "qualityProfileId": value.quality_profile_id,
        "metadataProfileId": value.metadata_profile_id,
    }


def _problem(resp: httpx.Response) -> str:
    """Lidarr's own explanation (validation errors come as a list), shortened."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, list):
        messages = [
            str(e.get("errorMessage"))
            for e in body
            if isinstance(e, dict) and e.get("errorMessage")
        ]
        if messages:
            return "Lidarr: " + "; ".join(messages)[:300]
    if isinstance(body, dict) and (message := body.get("message") or body.get("error")):
        return f"Lidarr: {str(message)[:300]}"
    return f"Lidarr answered {resp.status_code}."


# --- what Plekstender shows ---------------------------------------------------------------


def album_state(
    album: dict[str, Any] | None,
) -> Literal["absent", "monitored", "unmonitored", "downloaded"]:
    if album is None:
        return "absent"
    stats = album.get("statistics") or {}
    if (stats.get("percentOfTracks") or 0) >= 100 or (
        stats.get("trackFileCount") and stats.get("trackFileCount") == stats.get("totalTrackCount")
    ):
        return "downloaded"
    return "monitored" if album.get("monitored") else "unmonitored"


def release_age_days(release: dict[str, Any], now: dt.datetime | None = None) -> int | None:
    if (age := release.get("age")) is not None:
        return int(age)
    published = release.get("publishDate")
    if not published:
        return None
    try:
        when = dt.datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, ((now or dt.datetime.now(dt.UTC)) - when).days)
