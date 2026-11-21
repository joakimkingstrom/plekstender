"""Where image candidates come from: fanart.tv, MusicBrainz (to find an MBID), SearXNG."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from plekstender.artwork.fetch import USER_AGENT
from plekstender.sync.parse import normalise_artist


class SourceError(Exception):
    pass


@dataclass
class Candidate:
    """An image that could become the artist's poster."""

    url: str
    thumb_url: str | None = None
    label: str | None = None
    likes: int | None = None
    width: int | None = None
    height: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --- fanart.tv ----------------------------------------------------------------------------


async def fanart_logos(
    http: httpx.AsyncClient, base_url: str, api_key: str, mbid: str
) -> list[Candidate]:
    """An artist's HD and standard logos, most liked first (HD before SD on ties)."""
    try:
        resp = await http.get(
            f"{base_url.rstrip('/')}/music/{mbid}",
            params={"api_key": api_key},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise SourceError("Could not reach fanart.tv.") from exc
    if resp.status_code == 404:
        return []
    if resp.status_code in (401, 403):
        raise SourceError("fanart.tv rejected the API key.")
    if resp.status_code != 200:
        raise SourceError(f"fanart.tv is unavailable (HTTP {resp.status_code}).")
    data = resp.json()
    logos: list[tuple[int, int, Candidate]] = []
    for kind, hd in (("hdmusiclogo", 1), ("musiclogo", 0)):
        for item in data.get(kind) or []:
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                continue
            likes = int(item.get("likes") or 0)
            logos.append(
                (
                    likes,
                    hd,
                    Candidate(
                        url=url,
                        # fanart.tv serves small previews next to every image.
                        thumb_url=url.replace("/fanart/", "/preview/", 1),
                        label=f"{'HD' if hd else 'SD'} logo · {likes} like{'s' * (likes != 1)}",
                        likes=likes,
                        extra={"hd": bool(hd), "lang": item.get("lang")},
                    ),
                )
            )
    logos.sort(key=lambda entry: (-entry[0], -entry[1]))
    return [c for _, _, c in logos]


# --- MusicBrainz ----------------------------------------------------------------------------

_mb_lock = asyncio.Lock()
_mb_last = 0.0
MB_INTERVAL = 1.1  # MusicBrainz allows one request per second


async def musicbrainz_search(
    http: httpx.AsyncClient, base_url: str, entity: str, query: str, limit: int = 5
) -> dict[str, Any]:
    """A MusicBrainz search, at most one request a second (as it asks)."""
    global _mb_last
    async with _mb_lock:
        wait = MB_INTERVAL - (time.monotonic() - _mb_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            resp = await http.get(
                f"{base_url.rstrip('/')}/{entity}",
                params={"query": query, "fmt": "json", "limit": limit},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise SourceError("Could not reach MusicBrainz.") from exc
        finally:
            _mb_last = time.monotonic()
    if resp.status_code != 200:
        raise SourceError(f"MusicBrainz is unavailable (HTTP {resp.status_code}).")
    data = resp.json()
    return data if isinstance(data, dict) else {}


def lucene(text: str) -> str:
    """Text for a quoted MusicBrainz search term."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


async def musicbrainz_artist_id(http: httpx.AsyncClient, base_url: str, name: str) -> str | None:
    """The MBID of the artist with exactly this (normalised) name, if MusicBrainz is sure."""
    data = await musicbrainz_search(http, base_url, "artist", f'artist:"{lucene(name)}"')
    wanted = normalise_artist(name)
    for artist in data.get("artists") or []:
        if (
            int(artist.get("score") or 0) >= 90
            and normalise_artist(artist.get("name", "")) == wanted
        ):
            mbid = artist.get("id")
            return str(mbid) if mbid else None
    return None


# --- image search (SearXNG) ---------------------------------------------------------------

# Logo sites first; merchandise and ticket sites last (see the Phase 0 spike results).
GOOD_HINTS = ("logo", "png", "svg", "transparent", "wordmark")
BAD_DOMAINS = (
    "teepublic.", "redbubble.", "etsy.", "amazon.", "ebay.", "aliexpress.", "zazzle.",
    "ticketmaster.", "livenation.", "seatgeek.", "stubhub.", "pinterest.", "walmart.",
)  # fmt: skip


COVER_HINTS = ("cover", "album", "front", "artwork")


def _size(result: dict[str, Any]) -> tuple[int, int] | None:
    if match := re.match(r"(\d+)\s*x\s*(\d+)", str(result.get("resolution") or "")):
        return int(match.group(1)), int(match.group(2))
    return None


def _score(result: dict[str, Any], kind: str = "logo") -> int:
    url = str(result.get("img_src") or "")
    text = f"{result.get('title') or ''} {url}".lower()
    if kind == "cover":
        # Covers: square and big first.
        score = sum(2 for hint in COVER_HINTS if hint in text)
        if size := _size(result):
            w, h = size
            score += 3 if abs(w - h) <= max(w, h) * 0.03 else -2
            score += 3 if min(w, h) >= 1000 else 1 if min(w, h) >= 600 else -2
    else:
        score = sum(2 for hint in GOOD_HINTS if hint in text)
        if url.lower().split("?")[0].endswith((".png", ".svg", ".webp")):
            score += 3
    host = (urlsplit(str(result.get("url") or url)).hostname or "").lower()
    if any(bad in host for bad in BAD_DOMAINS):
        score -= 10
    return score


async def search_images(
    http: httpx.AsyncClient, searxng_url: str, query: str, limit: int = 40, kind: str = "logo"
) -> list[Candidate]:
    try:
        resp = await http.get(
            f"{searxng_url.rstrip('/')}/search",
            params={"q": query, "categories": "images", "format": "json", "safesearch": 1},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise SourceError("Image search isn't reachable.") from exc
    if resp.status_code != 200:
        raise SourceError(f"Image search failed (HTTP {resp.status_code}).")
    seen: set[str] = set()
    results = []
    for result in resp.json().get("results") or []:
        url = result.get("img_src")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        results.append(result)
    results.sort(key=lambda r: _score(r, kind), reverse=True)
    out = []
    for result in results[:limit]:
        width, height = _size(result) or (None, None)
        thumb = result.get("thumbnail_src")
        out.append(
            Candidate(
                url=result["img_src"],
                thumb_url=thumb if isinstance(thumb, str) and thumb.startswith("http") else None,
                label=(urlsplit(str(result.get("url") or result["img_src"])).hostname or None),
                width=width,
                height=height,
                extra={"title": result.get("title"), "engine": result.get("engine")},
            )
        )
    return out
