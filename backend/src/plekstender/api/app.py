"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

from plekstender import __version__
from plekstender.api.middleware import CSRFMiddleware, RateLimiter, SecurityHeadersMiddleware
from plekstender.api.routes import (
    achievements,
    admin,
    album_covers,
    artwork,
    auth,
    discover,
    export,
    file_artwork,
    files,
    game,
    gigs,
    health,
    identify,
    library,
    library_stats,
    listens,
    live,
    notifications,
    playlists,
    previews,
    ratings,
    renames,
    rewind,
    scrobblers,
    servers,
    stats,
    statuses,
    sync,
    tag_editing,
)
from plekstender.auth import setup
from plekstender.config import Settings, get_settings
from plekstender.db import get_engine, get_sessionmaker
from plekstender.worker import app as jobs

log = logging.getLogger(__name__)


async def _offer_setup_code(settings: Settings) -> None:
    """Nobody has signed in yet: make a setup code and write it to the log, for whoever
    claims the instance (see plekstender.auth.setup)."""
    try:
        async with get_sessionmaker()() as db:
            if not await setup.is_claimed(db):
                await setup.issue(db, settings.app_url)
    except Exception:  # the database may not be up yet; sign-in makes a code then
        log.info("No setup code made at start-up (database not ready?)", exc_info=True)


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
            await _offer_setup_code(settings)
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
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.secure_cookies)

    for module in (
        health,
        auth,
        servers,
        sync,
        library_stats,
        discover,
        library,
        listens,
        stats,
        rewind,
        statuses,
        ratings,
        playlists,
        previews,
        game,
        gigs,
        files,
        file_artwork,
        identify,
        tag_editing,
        renames,
        scrobblers,
        album_covers,  # before artwork: /artwork/albums isn't an artist
        artwork,
        export,
        live,
        notifications,
        achievements,
        admin,
    ):
        app.include_router(module.router)
    app.include_router(artwork.fonts_router)
    app.include_router(game.stats_router)
    app.include_router(game.sample_router)

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
