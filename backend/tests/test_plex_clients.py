from __future__ import annotations

import httpx
import pytest
import respx

from roadiefm.config import get_settings
from roadiefm.plex.server import choose_connection
from roadiefm.plex.tv import PlexTvClient, Resource

MACHINE = "abc123"


def resource(**kw: object) -> Resource:
    data: dict[str, object] = {
        "name": "Home",
        "clientIdentifier": MACHINE,
        "provides": "server",
        "owned": True,
        "accessToken": "server-token",
        "presence": True,
        "connections": [
            {
                "protocol": "https",
                "address": "10.0.0.2",
                "port": 32400,
                "uri": "https://10-0-0-2.x.plex.direct:32400",
                "local": True,
            },
            {
                "protocol": "https",
                "address": "1.2.3.4",
                "port": 32400,
                "uri": "https://1-2-3-4.x.plex.direct:32400",
                "local": False,
            },
            {
                "protocol": "https",
                "address": "relay",
                "port": 8443,
                "uri": "https://relay.x.plex.direct:8443",
                "relay": True,
            },
        ],
    }
    return Resource.model_validate(data | kw)


def identity(machine: str = MACHINE) -> httpx.Response:
    return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": machine}})


@respx.mock
async def test_prefers_local_connection() -> None:
    respx.get("https://10-0-0-2.x.plex.direct:32400/identity").mock(return_value=identity())
    respx.get("https://1-2-3-4.x.plex.direct:32400/identity").mock(return_value=identity())
    respx.get("https://relay.x.plex.direct:8443/identity").mock(return_value=identity())
    async with httpx.AsyncClient() as http:
        chosen, probes = await choose_connection(get_settings(), http, resource())
    assert chosen is not None
    assert chosen.kind == "local"
    assert all(p.ok for p in probes)


@respx.mock
async def test_falls_back_when_local_unreachable() -> None:
    respx.get("https://10-0-0-2.x.plex.direct:32400/identity").mock(
        side_effect=httpx.ConnectTimeout("timeout")
    )
    respx.get("https://1-2-3-4.x.plex.direct:32400/identity").mock(return_value=identity())
    respx.get("https://relay.x.plex.direct:8443/identity").mock(return_value=identity())
    async with httpx.AsyncClient() as http:
        chosen, _ = await choose_connection(get_settings(), http, resource())
    assert chosen is not None
    assert chosen.kind == "remote"


@respx.mock
async def test_rejects_wrong_identity_and_redirects() -> None:
    # A connection answering as a different server, or redirecting elsewhere, never counts.
    respx.get("https://10-0-0-2.x.plex.direct:32400/identity").mock(
        return_value=identity("someone-else")
    )
    respx.get("https://1-2-3-4.x.plex.direct:32400/identity").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    respx.get("https://relay.x.plex.direct:8443/identity").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as http:
        chosen, probes = await choose_connection(get_settings(), http, resource())
    assert chosen is None
    assert [p.error for p in probes] == ["identity mismatch", "PlexError", "PlexError"]


@respx.mock
async def test_get_servers_filters_non_servers() -> None:
    settings = get_settings()
    route = respx.get(f"{settings.plex_clients_url}/api/v2/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "Home", "clientIdentifier": "s1", "provides": "server"},
                {"name": "Phone", "clientIdentifier": "p1", "provides": "client,player"},
            ],
        )
    )
    async with httpx.AsyncClient() as http:
        servers = await PlexTvClient(settings, http).get_servers("user-token")
    assert [s.client_identifier for s in servers] == ["s1"]
    request = route.calls.last.request
    assert request.headers["X-Plex-Token"] == "user-token"
    assert request.headers["X-Plex-Client-Identifier"] == settings.plex_client_identifier


def test_auth_url_contains_forward_url() -> None:
    from roadiefm.plex.tv import Pin

    client = PlexTvClient(get_settings(), httpx.AsyncClient())
    url = client.auth_url(Pin(id=1, code="CODE"), "http://testserver/login/callback")
    assert url.startswith("https://app.plex.tv/auth#?")
    assert "code=CODE" in url
    assert "forwardUrl=http%3A%2F%2Ftestserver%2Flogin%2Fcallback" in url


@pytest.mark.parametrize("status", [401])
@respx.mock
async def test_account_auth_error(status: int) -> None:
    from roadiefm.plex.tv import PlexAuthError

    settings = get_settings()
    respx.get(f"{settings.plex_tv_url}/api/v2/user").mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient() as http:
        with pytest.raises(PlexAuthError):
            await PlexTvClient(settings, http).get_account("bad")
