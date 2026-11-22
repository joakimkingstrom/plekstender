"""Fetching images from the internet without becoming a way into the local network.

Only http(s) on the standard ports; every hop's host must resolve to public addresses
only; redirects are followed by hand (and re-checked); size and time are capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
from PIL import ImageFile

from plekstender import __version__

MAX_BYTES = 15 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = f"Plekstender/{__version__} (self-hosted music library tool)"

Resolver = Callable[[str], Awaitable[list[str]]]


class FetchError(Exception):
    pass


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


# Replaced in tests (fake hosts don't resolve).
resolver: Resolver = _resolve


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def check_url(url: str) -> str:
    """Refuse anything but http(s) on the standard ports to a host whose every address is
    public. Returns the address to connect to."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only http(s) URLs can be fetched.")
    if parts.port not in (None, 80, 443):
        raise FetchError("Only the standard web ports are allowed.")
    if parts.username or parts.password:
        raise FetchError("URLs with credentials aren't allowed.")
    try:
        addresses = await resolver(parts.hostname)
    except OSError as exc:
        raise FetchError(f"Couldn't find {parts.hostname}.") from exc
    if not addresses or not all(is_public(a) for a in addresses):
        raise FetchError("That address isn't on the public internet.")
    return addresses[0]


# The addresses check_url approved, by host, for the fetch under way in this task.
_approved: ContextVar[dict[str, str] | None] = ContextVar("approved_addresses", default=None)


class _ApprovedOnly(httpcore.AsyncNetworkBackend):
    """Connects only to the address check_url approved for the host. Without this the
    connection would look the host up again, and a DNS server answering differently the
    second time could point it at the local network ("DNS rebinding")."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        address = (_approved.get() or {}).get(host)
        if address is None:
            raise httpcore.ConnectError(f"{host} wasn't checked.")
        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Not a web address.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


_SSL = httpx.create_ssl_context()


class _ApprovedTransport(httpx.AsyncHTTPTransport):
    """httpx's transport, connecting through _ApprovedOnly (TLS is still checked against the
    host name, as the URL keeps it)."""

    def __init__(self) -> None:
        super().__init__(verify=_SSL, trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(ssl_context=_SSL, network_backend=_ApprovedOnly())


@asynccontextmanager
async def _open(http: httpx.AsyncClient, url: str) -> AsyncIterator[httpx.Response]:
    """A streamed GET of a public URL, redirects followed by hand (each hop checked, and
    connected to the address that was checked). `http` isn't used for the request itself:
    it goes through a client that can only reach approved addresses."""
    for _ in range(MAX_REDIRECTS + 1):
        address = await check_url(url)
        host = httpx.URL(url).raw_host.decode("ascii")
        approved = _approved.set({**(_approved.get() or {}), host: address})
        try:
            async with (
                httpx.AsyncClient(transport=_ApprovedTransport()) as client,
                client.stream(
                    "GET",
                    url,
                    follow_redirects=False,
                    timeout=TIMEOUT,
                    headers={
                        "User-Agent": USER_AGENT,
                        # Formats Pillow reads everywhere first (some hosts default to AVIF).
                        "Accept": "image/png,image/jpeg,image/webp,image/gif;q=0.9,image/*;q=0.5",
                    },
                ) as resp,
            ):
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("Redirect without a location.")
                    url = urljoin(url, location)
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"The image couldn't be downloaded (HTTP {resp.status_code}).")
                yield resp
                return
        except httpx.HTTPError as exc:
            raise FetchError("The image couldn't be downloaded.") from exc
        finally:
            _approved.reset(approved)
    raise FetchError("Too many redirects.")


async def fetch(
    http: httpx.AsyncClient, url: str, *, max_bytes: int = MAX_BYTES
) -> tuple[bytes, str]:
    """GET a public URL. Returns (body, content type)."""
    async with _open(http, url) as resp:
        if int(resp.headers.get("content-length") or 0) > max_bytes:
            raise FetchError("The image is too large.")
        body = bytearray()
        try:
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise FetchError("The image is too large.")
        except httpx.HTTPError as exc:
            raise FetchError("The image couldn't be downloaded.") from exc
        return bytes(body), resp.headers.get("content-type", "")


async def image_size(
    http: httpx.AsyncClient, url: str, *, max_bytes: int = 2 * 1024 * 1024
) -> tuple[int, int]:
    """A public image's width and height, read from the start of the file (the header is in
    its first few kilobytes), without downloading the rest."""
    parser = ImageFile.Parser()
    read = 0
    async with _open(http, url) as resp:
        try:
            async for chunk in resp.aiter_bytes():
                try:
                    parser.feed(chunk)
                except Exception as exc:  # Pillow raises many kinds for broken images
                    raise FetchError("That isn't an image Plekstender can read.") from exc
                if parser.image is not None:
                    return parser.image.size
                read += len(chunk)
                if read > max_bytes:
                    break
        except httpx.HTTPError as exc:
            raise FetchError("The image couldn't be downloaded.") from exc
    raise FetchError("That isn't an image Plekstender can read.")
