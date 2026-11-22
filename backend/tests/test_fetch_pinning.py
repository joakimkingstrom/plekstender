"""Fetching user-given addresses: the connection goes to the address that was checked,
not to whatever a second DNS lookup says (DNS rebinding)."""

from __future__ import annotations

from typing import Any

import httpcore
import pytest

from plekstender.artwork import fetch as fetch_mod


class Recorder(httpcore.AsyncNetworkBackend):
    """Notes where it was asked to connect, then refuses (no network in tests)."""

    def __init__(self) -> None:
        self.hosts: list[tuple[str, int]] = []

    async def connect_tcp(self, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        self.hosts.append((host, port))
        raise httpcore.ConnectError("recorded")

    async def sleep(self, seconds: float) -> None:
        return None


async def test_connects_to_the_checked_address(monkeypatch: pytest.MonkeyPatch) -> None:
    lookups: list[str] = []

    async def resolve(host: str) -> list[str]:
        lookups.append(host)
        return ["93.184.216.34"]  # public the first time; a rebinding server would change it

    recorder = Recorder()
    monkeypatch.setattr(fetch_mod, "resolver", resolve)
    monkeypatch.setattr(
        fetch_mod._ApprovedOnly, "__init__", lambda self: setattr(self, "_inner", recorder)
    )
    with pytest.raises(fetch_mod.FetchError):
        await fetch_mod.fetch(None, "https://rebind.example/cover.jpg")  # type: ignore[arg-type]
    assert lookups == ["rebind.example"]  # looked up once, by the check
    assert recorder.hosts == [("93.184.216.34", 443)]  # and connected to that address


async def test_unchecked_hosts_are_refused() -> None:
    backend = fetch_mod._ApprovedOnly()
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("internal.example", 80)
    token = fetch_mod._approved.set({"other.example": "93.184.216.34"})
    try:
        with pytest.raises(httpcore.ConnectError):
            await backend.connect_tcp("internal.example", 80)
    finally:
        fetch_mod._approved.reset(token)


async def test_private_addresses_are_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str) -> list[str]:
        return ["192.168.1.1"]

    monkeypatch.setattr(fetch_mod, "resolver", resolve)
    with pytest.raises(fetch_mod.FetchError, match="public internet"):
        await fetch_mod.fetch(None, "https://router.example/")  # type: ignore[arg-type]
