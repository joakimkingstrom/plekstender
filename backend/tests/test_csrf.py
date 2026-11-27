"""CSRF protection, and a clear message when APP_URL's http/https doesn't match the page's."""

from __future__ import annotations

import logging

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from plekstender.api.middleware import CSRFMiddleware


def client(app_url: str) -> httpx.AsyncClient:
    async def ok(request: object) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/api/thing", ok, methods=["POST"])])
    wrapped = CSRFMiddleware(app, allowed_origin=app_url)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="http://test")


async def test_requests_from_the_app_itself_only() -> None:
    async with client("https://plekstender.example") as http:
        same = {"X-Plekstender": "1", "Origin": "https://plekstender.example"}
        assert (await http.post("/api/thing", headers=same)).status_code == 200
        assert (await http.post("/api/thing")).json() == {"detail": "Missing CSRF header"}
        other = {"X-Plekstender": "1", "Origin": "https://evil.example"}
        resp = await http.post("/api/thing", headers=other)
        assert resp.status_code == 403
        assert resp.json() == {"detail": "Cross-origin request blocked"}


async def test_app_url_with_the_wrong_scheme_says_how_to_fix_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Behind a proxy that serves https, with APP_URL=http://...: sign-in was refused with a
    bare 403. Now the page and the log say what to change (the log only once)."""
    page = "https://plekstender-staging.example"
    async with client("http://plekstender-staging.example") as http:
        with caplog.at_level(logging.WARNING, logger="plekstender.api.middleware"):
            for _ in range(2):
                resp = await http.post("/api/thing", headers={"X-Plekstender": "1", "Origin": page})
                assert resp.status_code == 403
    assert resp.json()["detail"] == (
        "Plekstender's APP_URL starts with http://, but this page is at "
        f"{page}. Set APP_URL to {page} and restart."
    )
    [warning] = caplog.records
    assert "APP_URL is http://plekstender-staging.example" in warning.getMessage()
