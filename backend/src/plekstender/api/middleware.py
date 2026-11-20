"""Pure ASGI middleware: CSRF protection, security headers and simple rate limiting."""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-plekstender"


async def _reject(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class CSRFMiddleware:
    """State-changing API requests must carry a custom header and a same-origin Origin.

    Browsers can't add custom headers to cross-site requests without a CORS preflight
    (which this app never allows), so the header alone blocks CSRF; the Origin check is
    defence in depth. Session cookies are also SameSite=Lax.
    """

    def __init__(self, app: ASGIApp, *, allowed_origin: str) -> None:
        self.app = app
        self.allowed_origin = allowed_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in SAFE_METHODS
            and scope["path"].startswith("/api/")
        ):
            headers = Headers(scope=scope)
            if headers.get(CSRF_HEADER) != "1":
                await _reject(send, 403, "Missing CSRF header")
                return
            origin = headers.get("origin")
            if origin is not None and origin != self.allowed_origin:
                await _reject(send, 403, "Cross-origin request blocked")
                return
        await self.app(scope, receive, send)


CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https://plex.tv https://*.plex.tv",
        "font-src 'self'",
        # Map tiles and fonts for the gig map; MapLibre runs its workers from blobs.
        "connect-src 'self' https://tiles.openfreemap.org",
        "worker-src 'self' blob:",
        "child-src blob:",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                if headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Content-Security-Policy", CSP)
                if scope["path"].startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RateLimiter:
    """In-memory sliding-window limiter, keyed by client IP and bucket name.

    Good enough for a single web process; per-route limits are applied with `hit()`.
    """

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def hit(self, bucket: str, key: str, *, limit: int, window: float) -> bool:
        """Records a hit; returns False if the limit is exceeded."""
        now = time.monotonic()
        q = self._hits[(bucket, key)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self._hits) > 50_000:  # crude memory bound
            self._hits.clear()
        return True
