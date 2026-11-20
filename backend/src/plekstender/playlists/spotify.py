"""Reading a public Spotify playlist (its link), to import it like a playlist file.

Uses Spotify's Web API with the admin's app (client ID and secret, "client credentials":
no Spotify account of the person's is involved). Spotify doesn't let new apps read its
own editorial or algorithmic playlists ("Discover Weekly", "Today's Top Hits"…); playlists
people made themselves are fine.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time

import httpx

from plekstender.playlists.imports import MAX_SONGS, Entry

ACCOUNTS_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
PAGE = 100
_PLAYLIST_ID = re.compile(r"(?:playlist[/:])([A-Za-z0-9]{16,40})")


class SpotifyError(Exception):
    pass


def playlist_id(link: str) -> str:
    """The ID in an open.spotify.com/playlist/… link or a spotify:playlist:… URI."""
    m = _PLAYLIST_ID.search(link.strip())
    if not m:
        raise SpotifyError("That isn't a Spotify playlist link (open.spotify.com/playlist/…).")
    return m[1]


_token: tuple[str, str, float] | None = None  # (client id, token, expires at)
_lock = asyncio.Lock()


async def access_token(
    http: httpx.AsyncClient, client_id: str, client_secret: str, accounts_url: str = ACCOUNTS_URL
) -> str:
    """An app token (kept until shortly before it runs out)."""
    global _token
    async with _lock:
        if _token and _token[0] == client_id and _token[2] > time.monotonic() + 60:
            return _token[1]
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        try:
            resp = await http.post(
                accounts_url,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {basic}"},
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise SpotifyError("Spotify couldn't be reached.") from exc
        if resp.status_code in (400, 401):
            raise SpotifyError("Spotify didn't accept the app's client ID and secret.")
        if resp.status_code >= 400:
            raise SpotifyError(f"Spotify answered {resp.status_code}.")
        data = resp.json()
        token = str(data["access_token"])
        _token = (client_id, token, time.monotonic() + float(data.get("expires_in", 3600)))
        return token


async def read_playlist(
    http: httpx.AsyncClient, token: str, pid: str, api_url: str = API_URL
) -> tuple[str | None, list[Entry]]:
    """The playlist's name and songs (local files and podcasts left out)."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        head = await http.get(
            f"{api_url}/playlists/{pid}", params={"fields": "name"}, headers=headers, timeout=20
        )
    except httpx.HTTPError as exc:
        raise SpotifyError("Spotify couldn't be reached.") from exc
    if head.status_code == 404:
        raise SpotifyError(
            "Spotify didn't find that playlist. It may be private, or one of Spotify's own "
            "(editorial or algorithmic) playlists, which apps can't read any more."
        )
    if head.status_code >= 400:
        raise SpotifyError(f"Spotify answered {head.status_code}.")
    name = head.json().get("name")
    entries: list[Entry] = []
    url: str | None = f"{api_url}/playlists/{pid}/tracks"
    params: dict[str, str | int] | None = {
        "limit": PAGE,
        "fields": "next,items(is_local,track(type,name,duration_ms,artists(name),album(name)))",
    }
    while url and len(entries) < MAX_SONGS:
        try:
            resp = await http.get(url, params=params, headers=headers, timeout=20)
        except httpx.HTTPError as exc:
            raise SpotifyError("Spotify couldn't be reached.") from exc
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", "2"))
            await asyncio.sleep(min(wait, 10))
            continue
        if resp.status_code >= 400:
            raise SpotifyError(f"Spotify answered {resp.status_code}.")
        page = resp.json()
        for item in page.get("items") or []:
            track = (item or {}).get("track") or {}
            if item.get("is_local") or track.get("type") != "track" or not track.get("name"):
                continue
            artists = [a["name"] for a in track.get("artists") or [] if a.get("name")]
            album = (track.get("album") or {}).get("name")
            entries.append(Entry(track["name"], artists, album or None, track.get("duration_ms")))
        url, params = page.get("next"), None  # the next link carries the paging and fields
    return (str(name) if name else None), entries[:MAX_SONGS]
