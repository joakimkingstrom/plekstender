"""Tidal's developer API (openapi.tidal.com/v2) with the admin's free app: its client ID and
secret ("client credentials", no Tidal account of the person's is involved). Used for album
covers (plekstender.artwork.covers) and for reading public playlists to import them.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from plekstender.artwork.fetch import USER_AGENT
from plekstender.config import Settings

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
TRACKS_AT_ONCE = 20  # /tracks takes up to 20 IDs
_PLAYLIST_ID = re.compile(
    r"playlist[/:]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")


class TidalError(Exception):
    """Something the user should be told."""


_tokens: dict[str, tuple[str, float]] = {}  # client id → (token, expires at)


async def access_token(http: httpx.AsyncClient, auth_url: str, client: tuple[str, str]) -> str:
    """An app token (kept until shortly before it runs out)."""
    cached = _tokens.get(client[0])
    if cached and cached[1] > time.monotonic() + 60:
        return cached[0]
    try:
        resp = await http.post(
            auth_url,
            data={"grant_type": "client_credentials"},
            auth=client,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise TidalError("Tidal couldn't be reached.") from exc
    if resp.status_code in (400, 401, 403):
        raise TidalError("Tidal didn't accept the client ID and secret.")
    if resp.status_code != 200:
        raise TidalError(f"Tidal is unavailable (HTTP {resp.status_code}).")
    data = resp.json()
    token = str(data.get("access_token") or "")
    if not token:
        raise TidalError("Tidal didn't give a token.")
    _tokens[client[0]] = (token, time.monotonic() + float(data.get("expires_in") or 3600))
    return token


async def check(http: httpx.AsyncClient, settings: Settings, client: tuple[str, str]) -> None:
    """Raises TidalError if Tidal refuses the client ID and secret."""
    _tokens.pop(client[0], None)
    await access_token(http, settings.tidal_auth_url, client)


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
    }


async def get(
    http: httpx.AsyncClient, url: str, token: str, params: Any = None, *, missing: str | None = None
) -> dict[str, Any]:
    """A JSON:API document. `missing`: what to say when Tidal answers 404."""
    for _ in range(3):
        try:
            resp = await http.get(url, params=params, headers=headers(token), timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise TidalError("Tidal couldn't be reached.") from exc
        if resp.status_code == 429:  # too many at once: wait as asked (briefly)
            await asyncio.sleep(min(float(resp.headers.get("Retry-After") or 2), 10))
            continue
        if resp.status_code == 404 and missing:
            raise TidalError(missing)
        if resp.status_code >= 400:
            raise TidalError(f"Tidal is unavailable (HTTP {resp.status_code}).")
        data = resp.json()
        return data if isinstance(data, dict) else {}
    raise TidalError("Tidal is busy; try again in a minute.")


# --- playlists -----------------------------------------------------------------------------


def playlist_id(link: str) -> str:
    """The ID in a tidal.com/playlist/…, tidal.com/browse/playlist/… or
    listen.tidal.com/playlist/… link."""
    m = _PLAYLIST_ID.search(link.strip())
    if not m:
        raise TidalError("That isn't a Tidal playlist link (tidal.com/playlist/…).")
    return m[1].lower()


def duration_ms(value: Any) -> int | None:
    """ISO 8601 ("PT3M58S") to milliseconds."""
    m = _DURATION.match(str(value or ""))
    if not m or not any(m.groups()):
        return None
    hours, minutes, seconds = (float(g or 0) for g in m.groups())
    return int((hours * 3600 + minutes * 60 + seconds) * 1000)


def _next(base: str, links: dict[str, Any] | None) -> str | None:
    """The next page's URL (Tidal gives it relative to the API's root)."""
    nxt = (links or {}).get("next")
    if not nxt:
        return None
    return nxt if str(nxt).startswith("http") else f"{base}{nxt}"


def _related(included: dict[Any, Any], item: dict[str, Any], rel: str) -> list[dict[str, Any]]:
    """The included resources one of `item`'s relationships points at."""
    refs = ((item.get("relationships") or {}).get(rel) or {}).get("data") or []
    return [
        included[(r.get("type"), r.get("id"))]
        for r in refs
        if (r.get("type"), r.get("id")) in included
    ]


async def read_playlist(
    http: httpx.AsyncClient, settings: Settings, client: tuple[str, str], link: str, limit: int
) -> tuple[str | None, list[tuple[str, list[str], str | None, int | None]]]:
    """A public (or unlisted) playlist's name and songs, in order, as (title, artists,
    album, duration in ms). Videos are left out."""
    pid = playlist_id(link)
    token = await access_token(http, settings.tidal_auth_url, client)
    base = settings.tidal_api_url.rstrip("/")
    gone = "Tidal didn't find that playlist. It may be private (only public ones can be read)."
    head = await get(http, f"{base}/playlists/{pid}", token, missing=gone)
    name = ((head.get("data") or {}).get("attributes") or {}).get("name")

    ids: list[str] = []
    url: str | None = f"{base}/playlists/{pid}/relationships/items"
    params: Any = None
    while url and len(ids) < limit:
        page = await get(http, url, token, params, missing=gone)
        ids += [str(i["id"]) for i in page.get("data") or [] if i.get("type") == "tracks"]
        url, params = _next(base, page.get("links")), None  # the next link carries the cursor
    ids = ids[:limit]

    # Titles, artists and albums: up to 20 tracks a time.
    tracks: dict[str, tuple[str, list[str], str | None, int | None]] = {}
    for start in range(0, len(ids), TRACKS_AT_ONCE):
        batch = list(dict.fromkeys(ids[start : start + TRACKS_AT_ONCE]))
        doc = await get(
            http,
            f"{base}/tracks",
            token,
            [("filter[id]", i) for i in batch] + [("include", "artists"), ("include", "albums")],
        )
        included = {(i.get("type"), i.get("id")): i for i in doc.get("included") or []}

        for item in doc.get("data") or []:
            attrs = item.get("attributes") or {}
            title = attrs.get("title")
            if not title:
                continue
            if attrs.get("version"):  # "Remastered 2009", shown with the title in Tidal's apps
                title = f"{title} ({attrs['version']})"
            artists = [
                str(a["attributes"]["name"])
                for a in _related(included, item, "artists")
                if (a.get("attributes") or {}).get("name")
            ]
            albums = [
                str(a["attributes"]["title"])
                for a in _related(included, item, "albums")
                if (a.get("attributes") or {}).get("title")
            ]
            tracks[str(item.get("id"))] = (
                str(title),
                artists,
                albums[0] if albums else None,
                duration_ms(attrs.get("duration")),
            )
    return (str(name) if name else None), [tracks[i] for i in ids if i in tracks]
