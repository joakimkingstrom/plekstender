"""Fetching images from the internet without becoming a way into the local network.

Only http(s) on the standard ports; every hop's host must resolve to public addresses
only; redirects are followed by hand (and re-checked); size and time are capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

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


async def check_url(url: str) -> None:
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


async def fetch(
    http: httpx.AsyncClient, url: str, *, max_bytes: int = MAX_BYTES
) -> tuple[bytes, str]:
    """GET a public URL. Returns (body, content type)."""
    for _ in range(MAX_REDIRECTS + 1):
        await check_url(url)
        try:
            async with http.stream(
                "GET",
                url,
                follow_redirects=False,
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
            ) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("Redirect without a location.")
                    url = urljoin(url, location)
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"The image couldn't be downloaded (HTTP {resp.status_code}).")
                if int(resp.headers.get("content-length") or 0) > max_bytes:
                    raise FetchError("The image is too large.")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise FetchError("The image is too large.")
                return bytes(body), resp.headers.get("content-type", "")
        except httpx.HTTPError as exc:
            raise FetchError("The image couldn't be downloaded.") from exc
    raise FetchError("Too many redirects.")
