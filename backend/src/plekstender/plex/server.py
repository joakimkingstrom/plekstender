"""Client for a Plex Media Server, and selection of a reachable connection."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field

from plekstender.config import Settings
from plekstender.plex.tv import Connection, PlexAuthError, PlexError, Resource, plex_headers

log = logging.getLogger(__name__)

PROBE_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
# /library/parts/{id}/{changed-at}/file.ext (as Plex lists a track's media part).
PART_KEY = re.compile(r"^/library/parts/\d+/[^?#]*$")
API_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class LibrarySection(BaseModel):
    key: str
    title: str
    type: str
    agent: str | None = None


@dataclass(frozen=True)
class ChosenConnection:
    uri: str
    kind: str  # local | remote | relay


def connection_kind(conn: Connection) -> str:
    if conn.relay:
        return "relay"
    return "local" if conn.local else "remote"


def connection_priority(conn: Connection) -> tuple[int, int, int]:
    """Lower sorts first: local before remote before relay; IPv4 and HTTPS preferred."""
    kind_rank = {"local": 0, "remote": 1, "relay": 2}[connection_kind(conn)]
    return (kind_rank, int(conn.ipv6), int(conn.protocol != "https"))


class PlexServerClient:
    """Talks to one server over one connection, with one access token.

    Redirects are never followed, so a server can't bounce requests to other hosts.
    """

    def __init__(
        self, settings: Settings, http: httpx.AsyncClient, base_uri: str, token: str
    ) -> None:
        self._settings = settings
        self._http = http
        self._base = base_uri.rstrip("/")
        self._token = token

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        request_timeout: httpx.Timeout = API_TIMEOUT,
        accept_json: bool = True,
        method: str = "GET",
        content: bytes | None = None,
    ) -> httpx.Response:
        headers = plex_headers(self._settings, self._token)
        if not accept_json:
            headers["Accept"] = "image/*"
        resp = await self._http.request(
            method,
            f"{self._base}{path}",
            params=params,
            headers=headers,
            content=content,
            timeout=request_timeout,
            follow_redirects=False,
        )
        if resp.status_code == 401:
            raise PlexAuthError("server rejected the token", 401)
        if resp.is_error or resp.is_redirect:
            raise PlexError(f"server returned HTTP {resp.status_code}", resp.status_code)
        return resp

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        request_timeout: httpx.Timeout = API_TIMEOUT,
    ) -> Any:
        resp = await self._request(path, params=params, request_timeout=request_timeout)
        try:
            return resp.json()
        except ValueError as exc:
            raise PlexError("server returned invalid JSON") from exc

    async def identity(self, *, request_timeout: httpx.Timeout = API_TIMEOUT) -> str:
        data = await self._get("/identity", request_timeout=request_timeout)
        machine_id = data.get("MediaContainer", {}).get("machineIdentifier")
        if not isinstance(machine_id, str):
            raise PlexError("server identity response had no machineIdentifier")
        return machine_id

    async def library_sections(self) -> list[LibrarySection]:
        data = await self._get("/library/sections")
        dirs = data.get("MediaContainer", {}).get("Directory", [])
        return [LibrarySection.model_validate(d) for d in dirs]

    async def music_sections(self) -> list[LibrarySection]:
        return [s for s in await self.library_sections() if s.type == "artist"]

    async def list_items(
        self,
        section_key: str,
        plex_type: int,
        *,
        start: int,
        size: int,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of a library listing. Returns (items, total matching items).

        `filters` uses Plex's syntax, e.g. {"updatedAt>>": 1700000000} for "updated after".
        Sorted by addedAt so new items can't shift earlier pages during a sync.
        """
        params: dict[str, Any] = {
            "type": plex_type,
            "includeGuids": 1,
            "sort": "addedAt:asc",
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": size,
            **(filters or {}),
        }
        data = await self._get(f"/library/sections/{section_key}/all", params=params)
        mc = data.get("MediaContainer", {})
        items = mc.get("Metadata", [])
        total = int(mc.get("totalSize", mc.get("size", len(items))))
        return items, total

    async def history(
        self, section_key: str, *, start: int, size: int, viewed_since: int | None
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of a music library's play history, oldest first.

        Plex ignores `type=` here, so plays are selected by library section. With
        `viewed_since`, only plays from that time on (inclusive) are returned. The token
        owner's account decides whose plays are visible: the server owner sees everyone's.
        """
        params: dict[str, Any] = {
            "librarySectionID": section_key,
            "sort": "viewedAt:asc",
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": size,
        }
        if viewed_since is not None:
            params["viewedAt>"] = viewed_since
        data = await self._get("/status/sessions/history/all", params=params)
        mc = data.get("MediaContainer", {})
        items = mc.get("Metadata", [])
        total = int(mc.get("totalSize", mc.get("size", len(items))))
        return items, total

    async def metadata(self, rating_keys: list[int]) -> list[dict[str, Any]]:
        """Full metadata (incl. Genre/Mood/Style/Similar) for several items in one request."""
        if not rating_keys:
            return []
        keys = ",".join(str(k) for k in rating_keys)
        data = await self._get(f"/library/metadata/{keys}", params={"includeGuids": 1})
        items: list[dict[str, Any]] = data.get("MediaContainer", {}).get("Metadata", [])
        return items

    async def sessions(self) -> list[dict[str, Any]]:
        """What's playing now (all accounts on the owner's token, else the user's own)."""
        data = await self._get("/status/sessions")
        items: list[dict[str, Any]] = data.get("MediaContainer", {}).get("Metadata", [])
        return items

    async def posters(self, rating_key: int) -> list[dict[str, Any]]:
        """The posters Plex offers for an item (agent images and uploads)."""
        data = await self._get(f"/library/metadata/{rating_key}/posters")
        items: list[dict[str, Any]] = data.get("MediaContainer", {}).get("Metadata", [])
        return items

    async def upload_poster(self, rating_key: int, image: bytes) -> None:
        """Upload an image as the item's poster; Plex selects it."""
        await self._request(
            f"/library/metadata/{rating_key}/posters",
            method="POST",
            content=image,
            request_timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def rate(self, rating_key: int, rating: float | None) -> None:
        """Set the token owner's rating (0-10, half stars = 1), or clear it with None."""
        await self._request(
            "/:/rate",
            params={
                "key": rating_key,
                "identifier": "com.plexapp.plugins.library",
                "rating": -1 if rating is None else rating,
            },
            method="PUT",
        )

    async def photo(self, path: str, size: int) -> tuple[bytes, str]:
        """A resized image from Plex's photo transcoder. Returns (bytes, content type)."""
        resp = await self._request(
            "/photo/:/transcode",
            params={
                "url": path,
                "width": size,
                "height": size,
                "minSize": 1,
                "upscale": 1,
            },
            accept_json=False,
        )
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        if not content_type.startswith("image/"):
            raise PlexError("photo transcoder returned a non-image response")
        return resp.content, content_type

    async def refresh_folder(self, section_key: str, path: str | None) -> None:
        """Ask Plex to rescan one folder of a library (after its files' tags changed), or
        the whole library section without a path."""
        await self._request(
            f"/library/sections/{section_key}/refresh",
            params={"path": path} if path else None,
        )

    # --- media files -----------------------------------------------------------------

    def stream_part(self, part_key: str) -> Any:
        """The file behind a track's media part, as a streaming response (an async context
        manager). Reading a file directly is not a Plex playback: no session, no play."""
        if not PART_KEY.match(part_key) or ".." in part_key:
            raise PlexError("not a media part path")
        headers = plex_headers(self._settings, self._token)
        headers["Accept"] = "*/*"
        return self._http.stream(
            "GET",
            f"{self._base}{part_key}",
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=False,
        )

    # --- playlists (always under the token owner's account) ---------------------------

    def library_uri(self, machine_id: str, rating_keys: list[int]) -> str:
        keys = ",".join(str(k) for k in rating_keys)
        return f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{keys}"

    async def create_playlist(self, title: str, machine_id: str, rating_keys: list[int]) -> int:
        """A new (regular, not smart) audio playlist holding `rating_keys`. Returns its key."""
        resp = await self._request(
            "/playlists",
            method="POST",
            params={
                "type": "audio",
                "title": title,
                "smart": 0,
                "uri": self.library_uri(machine_id, rating_keys),
            },
        )
        try:
            items = resp.json().get("MediaContainer", {}).get("Metadata", [])
            return int(items[0]["ratingKey"])
        except (ValueError, LookupError, TypeError) as exc:
            raise PlexError("Plex didn't return the new playlist") from exc

    async def add_to_playlist(self, key: int, machine_id: str, rating_keys: list[int]) -> None:
        await self._request(
            f"/playlists/{key}/items",
            method="PUT",
            params={"uri": self.library_uri(machine_id, rating_keys)},
        )

    async def audio_playlists(self) -> list[dict[str, Any]]:
        """The token owner's audio playlists (regular and smart)."""
        data = await self._get("/playlists", params={"playlistType": "audio"})
        items: list[dict[str, Any]] = data.get("MediaContainer", {}).get("Metadata", [])
        return items

    async def playlist_rating_keys(self, key: int) -> list[int]:
        data = await self._get(f"/playlists/{key}/items")
        items = data.get("MediaContainer", {}).get("Metadata", [])
        return [int(i["ratingKey"]) for i in items if "ratingKey" in i]

    async def playlist_item_ids(self, key: int) -> list[int]:
        """The playlistItemIDs of a playlist's entries (404 when it's been deleted)."""
        data = await self._get(f"/playlists/{key}/items")
        items = data.get("MediaContainer", {}).get("Metadata", [])
        return [int(i["playlistItemID"]) for i in items if "playlistItemID" in i]

    async def clear_playlist(self, key: int) -> None:
        """Empty a playlist, keeping it (and its key, cover and place in people's apps)."""
        try:
            await self._request(f"/playlists/{key}/items", method="DELETE")
        except PlexAuthError:
            raise
        except PlexError as exc:
            if exc.status == 404:
                raise
            # Older servers: one entry at a time.
            for item_id in await self.playlist_item_ids(key):
                await self._request(f"/playlists/{key}/items/{item_id}", method="DELETE")

    async def rename_playlist(self, key: int, title: str, summary: str | None) -> None:
        await self._request(
            f"/playlists/{key}", method="PUT", params={"title": title, "summary": summary or ""}
        )

    async def delete_playlist(self, key: int) -> None:
        await self._request(f"/playlists/{key}", method="DELETE")

    async def nearest(self, rating_key: int, limit: int = 30) -> list[dict[str, Any]]:
        """Sonically similar items (needs Plex Pass sonic analysis; otherwise empty or 404)."""
        data = await self._get(
            f"/library/metadata/{rating_key}/nearest",
            params={"limit": limit, "maxDistance": 0.25},
        )
        items: list[dict[str, Any]] = data.get("MediaContainer", {}).get("Metadata", [])
        return items


class ProbeResult(BaseModel):
    uri: str
    kind: str
    ok: bool
    error: str | None = Field(default=None)


async def choose_connection(
    settings: Settings, http: httpx.AsyncClient, resource: Resource
) -> tuple[ChosenConnection | None, list[ProbeResult]]:
    """Probe all of a server's connections concurrently and pick the best working one.

    A connection only counts if `/identity` answers with the expected machine identifier,
    which also stops plex.tv-advertised addresses from pointing us at unrelated hosts.
    """
    if not resource.access_token:
        return None, []
    token = resource.access_token
    conns = sorted(resource.connections, key=connection_priority)

    async def probe(conn: Connection) -> ProbeResult:
        client = PlexServerClient(settings, http, conn.uri, token)
        kind = connection_kind(conn)
        try:
            machine_id = await client.identity(request_timeout=PROBE_TIMEOUT)
        except (httpx.HTTPError, PlexError) as exc:
            return ProbeResult(uri=conn.uri, kind=kind, ok=False, error=type(exc).__name__)
        if machine_id != resource.client_identifier:
            return ProbeResult(uri=conn.uri, kind=kind, ok=False, error="identity mismatch")
        return ProbeResult(uri=conn.uri, kind=kind, ok=True)

    results = await asyncio.gather(*(probe(c) for c in conns))
    for result in results:  # already in priority order
        if result.ok:
            return ChosenConnection(uri=result.uri, kind=result.kind), list(results)
    log.info("no reachable connection for server %s", resource.client_identifier)
    return None, list(results)
