"""HSTS over https, and the warning when nobody has claimed the admin role."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from plekstender.api.middleware import SecurityHeadersMiddleware
from plekstender.config import get_settings


async def _ok(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse("ok")(scope, receive, send)


@pytest.mark.parametrize(("hsts", "expected"), [(True, "max-age=31536000"), (False, None)])
async def test_hsts_only_over_https(hsts: bool, expected: str | None) -> None:
    app = SecurityHeadersMiddleware(_ok, hsts=hsts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/")
    assert resp.headers.get("strict-transport-security") == expected
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.db
async def test_warns_while_nobody_has_signed_in(
    clean_db: None, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plekstender.api.app import create_app

    settings = get_settings()
    monkeypatch.setattr(settings, "initial_admin", None)
    application: FastAPI = create_app(settings)
    with caplog.at_level(logging.WARNING, logger="plekstender.api.app"):
        async with application.router.lifespan_context(application):
            pass
    assert any("becomes the admin" in r.getMessage() for r in caplog.records)

    caplog.clear()
    monkeypatch.setattr(settings, "initial_admin", "someone")
    with caplog.at_level(logging.WARNING, logger="plekstender.api.app"):
        async with application.router.lifespan_context(application):
            pass
    assert not any("becomes the admin" in r.getMessage() for r in caplog.records)
