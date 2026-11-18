"""Per-route rate limiting as a FastAPI dependency."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from roadiefm.api.middleware import RateLimiter


def _client_key(request: Request) -> str:
    # uvicorn resolves X-Forwarded-For into request.client when FORWARDED_ALLOW_IPS trusts
    # the proxy, so this is the real client address behind Traefik.
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, limit: int, window: float) -> Any:
    async def dependency(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        if not limiter.hit(bucket, _client_key(request), limit=limit, window=window):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")

    return Depends(dependency)
