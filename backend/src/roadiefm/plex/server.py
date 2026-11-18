"""Client for a Plex Media Server, and selection of a reachable connection."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field

from roadiefm.config import Settings
from roadiefm.plex.tv import Connection, PlexAuthError, PlexError, Resource, plex_headers

log = logging.getLogger(__name__)

PROBE_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
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
    ) -> httpx.Response:
        headers = plex_headers(self._settings, self._token)
        if not accept_json:
            headers["Accept"] = "image/*"
        resp = await self._http.get(
            f"{self._base}{path}",
            params=params,
            headers=headers,
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

    async def metadata(self, rating_keys: list[int]) -> list[dict[str, Any]]:
        """Full metadata (incl. Genre/Mood/Style/Similar) for several items in one request."""
        if not rating_keys:
            return []
        keys = ",".join(str(k) for k in rating_keys)
        data = await self._get(f"/library/metadata/{keys}", params={"includeGuids": 1})
        items: list[dict[str, Any]] = data.get("MediaContainer", {}).get("Metadata", [])
        return items

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
