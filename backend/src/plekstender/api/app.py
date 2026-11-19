"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

from plekstender import __version__
from plekstender.api.middleware import CSRFMiddleware, RateLimiter, SecurityHeadersMiddleware
from plekstender.api.routes import (
    admin,
    artwork,
    auth,
    export,
    health,
    library,
    listens,
    playlists,
    ratings,
    servers,
    stats,
    sync,
)
from plekstender.config import Settings, get_settings
from plekstender.db import get_engine
from plekstender.worker import app as jobs


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with (
            httpx.AsyncClient(
                headers={"User-Agent": f"Plekstender/{__version__}"},
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            ) as http,
            jobs.open_async(),
        ):
            app.state.http = http
            yield
        await get_engine().dispose()

    app = FastAPI(
        title="Plekstender",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.rate_limiter = RateLimiter()
    app.add_middleware(CSRFMiddleware, allowed_origin=settings.app_origin)
    app.add_middleware(SecurityHeadersMiddleware)

    for module in (
        health,
        auth,
        servers,
        sync,
        library,
        listens,
        stats,
        ratings,
        playlists,
        artwork,
        export,
        admin,
    ):
        app.include_router(module.router)
    app.include_router(artwork.fonts_router)

    if settings.static_dir:
        _mount_spa(app, settings.static_dir)
    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            # Vite puts content-hashed files under /assets; everything else may change.
            immutable = path.startswith("assets/")
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
