"""Client for plex.tv: PIN sign-in, account info and the user's server list."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field

from plekstender import __version__
from plekstender.config import Settings

TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class PlexError(Exception):
    """Plex returned an error or an unexpected response."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class PlexAuthError(PlexError):
    """The token was rejected (401)."""


class Pin(BaseModel):
    id: int
    code: str
    auth_token: str | None = Field(default=None, alias="authToken")
    expires_in: int | None = Field(default=None, alias="expiresIn")


class PlexAccount(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    uuid: str
    username: str
    email: str | None = None
    thumb: str | None = None


class Connection(BaseModel):
    protocol: str
    address: str
    port: int
    uri: str
    local: bool = False
    relay: bool = False
    ipv6: bool = Field(default=False, alias="IPv6")


class Resource(BaseModel):
    name: str
    product: str | None = None
    product_version: str | None = Field(default=None, alias="productVersion")
    client_identifier: str = Field(alias="clientIdentifier")
    provides: str = ""
    owned: bool = False
    access_token: str | None = Field(default=None, alias="accessToken")
    presence: bool = False
    public_address: str | None = Field(default=None, alias="publicAddress")
    connections: list[Connection] = []

    @property
    def is_server(self) -> bool:
        return "server" in self.provides.split(",")


def plex_headers(settings: Settings, token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": settings.plex_product,
        "X-Plex-Version": __version__,
        "X-Plex-Client-Identifier": settings.plex_client_identifier,
        "X-Plex-Device-Name": settings.plex_product,
        "X-Plex-Platform": "Web",
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


def _check(resp: httpx.Response) -> Any:
    if resp.status_code == 401:
        raise PlexAuthError("Plex rejected the token")
    if resp.is_error:
        raise PlexError(f"plex.tv returned HTTP {resp.status_code}", resp.status_code)
    try:
        return resp.json()
    except ValueError as exc:
        raise PlexError("plex.tv returned invalid JSON") from exc


class PlexTvClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    async def create_pin(self) -> Pin:
        resp = await self._http.post(
            f"{self._settings.plex_tv_url}/api/v2/pins",
            params={"strong": "true"},
            headers=plex_headers(self._settings),
            timeout=TIMEOUT,
        )
        return Pin.model_validate(_check(resp))

    async def check_pin(self, pin_id: int) -> Pin | None:
        """Returns the PIN (with auth_token once approved), or None if it has expired."""
        resp = await self._http.get(
            f"{self._settings.plex_tv_url}/api/v2/pins/{pin_id}",
            headers=plex_headers(self._settings),
            timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        return Pin.model_validate(_check(resp))

    def auth_url(self, pin: Pin, forward_url: str) -> str:
        params = {
            "clientID": self._settings.plex_client_identifier,
            "code": pin.code,
            "forwardUrl": forward_url,
            "context[device][product]": self._settings.plex_product,
        }
        return f"{self._settings.plex_app_url}/auth#?{urlencode(params)}"

    async def get_account(self, token: str) -> PlexAccount:
        resp = await self._http.get(
            f"{self._settings.plex_tv_url}/api/v2/user",
            headers=plex_headers(self._settings, token),
            timeout=TIMEOUT,
        )
        return PlexAccount.model_validate(_check(resp))

    async def get_servers(self, token: str) -> list[Resource]:
        resp = await self._http.get(
            f"{self._settings.plex_clients_url}/api/v2/resources",
            params={"includeHttps": "1", "includeRelay": "1", "includeIPv6": "1"},
            headers=plex_headers(self._settings, token),
            timeout=TIMEOUT,
        )
        data = _check(resp)
        if not isinstance(data, list):
            raise PlexError("unexpected resources response")
        resources = [Resource.model_validate(item) for item in data]
        return [r for r in resources if r.is_server]
