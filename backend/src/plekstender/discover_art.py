"""Artwork for the artists suggested on Library → Discover: the top few get fanart.tv's most
liked background, banner and HD logo, kept under DATA_DIR/discover-art.

Suggested artists aren't in the library, so they have no MusicBrainz ID yet: each is looked
up by name (only an exact, confident match), then on fanart.tv. Done by the worker, hourly
and when Discover shows artists not looked up yet; each artist is looked at again after a
month (fanart.tv gains images), one without a match on MusicBrainz too.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.artist_suggestions import suggestions
from plekstender.artwork import images
from plekstender.artwork.fetch import USER_AGENT, FetchError, fetch
from plekstender.artwork.images import ImageError
from plekstender.artwork.sources import SourceError, musicbrainz_artist_id
from plekstender.config import Settings
from plekstender.db.instance import get_fanart_api_key
from plekstender.db.models import DiscoverArt, UserServer

log = logging.getLogger(__name__)

Kind = Literal["background", "banner", "logo"]
KINDS: tuple[Kind, ...] = ("background", "banner", "logo")
TOP = 10  # the suggestions that get artwork (the big cards and the banner rows)
LOOK_AGAIN = dt.timedelta(days=30)
PER_RUN = 20  # artists looked up in one go (MusicBrainz: one request a second)
# fanart.tv's image kinds for each (the first that has one), and the size kept (a box).
FANART: dict[Kind, tuple[tuple[str, ...], int]] = {
    "background": (("artistbackground",), 1280),
    "banner": (("musicbanner",), 1000),
    "logo": (("hdmusiclogo", "musiclogo"), 800),
}
MAX_BYTES = 15 * 1024 * 1024


def folder(settings: Settings) -> Path:
    return settings.data_dir / "discover-art"


def path(settings: Settings, mbid: str, kind: Kind) -> Path:
    return folder(settings) / f"{mbid}-{kind}.img"


def media_type(body: bytes) -> str:
    return "image/png" if body.startswith(b"\x89PNG") else "image/jpeg"


async def wanted(db: AsyncSession) -> dict[str, str]:
    """The top suggestions of everyone's libraries: artist key → name."""
    found: dict[str, str] = {}
    connections = await db.scalars(select(UserServer))
    for us in connections:
        if not us.music_section_keys:
            continue
        for s in (await suggestions(db, us.user_id, us))[:TOP]:
            found.setdefault(s.key, s.name)
    return found


async def _fanart(
    http: httpx.AsyncClient, settings: Settings, api_key: str, mbid: str
) -> dict[str, Any]:
    try:
        resp = await http.get(
            f"{settings.fanart_api_url.rstrip('/')}/music/{mbid}",
            params={"api_key": api_key},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise SourceError("Could not reach fanart.tv.") from exc
    if resp.status_code == 404:
        return {}
    if resp.status_code in (401, 403):
        raise SourceError("fanart.tv rejected the API key.")
    if resp.status_code != 200:
        raise SourceError(f"fanart.tv is unavailable (HTTP {resp.status_code}).")
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _best(data: dict[str, Any], kinds: tuple[str, ...]) -> str | None:
    """The most liked image of the first of these kinds that has any."""
    for kind in kinds:
        items = [
            i
            for i in data.get(kind) or []
            if isinstance(i, dict) and str(i.get("url", "")).startswith("https://")
        ]
        if items:
            return str(max(items, key=lambda i: int(i.get("likes") or 0))["url"])
    return None


def _save(target: Path, body: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    part.write_bytes(body)
    part.replace(target)


async def _look_up(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, api_key: str, key: str,
    name: str, known: DiscoverArt | None,
) -> None:  # fmt: skip
    values: dict[str, Any] = {"name": name, "error": None, "checked_at": dt.datetime.now(dt.UTC)}
    try:
        mbid = (known.mbid if known else None) or await musicbrainz_artist_id(
            http, settings.musicbrainz_api_url, name
        )
        values["mbid"] = mbid
        saved: dict[str, bool] = dict.fromkeys(KINDS, False)
        if mbid:
            data = await _fanart(http, settings, api_key, mbid)
            for kind, (fanart_kinds, box) in FANART.items():
                url = _best(data, fanart_kinds)
                if not url:
                    continue
                try:
                    raw, _ = await fetch(http, url, max_bytes=MAX_BYTES)
                    body, _ = await asyncio.to_thread(images.thumbnail, raw, box)
                except FetchError, ImageError:
                    continue
                await asyncio.to_thread(_save, path(settings, mbid, kind), body)
                saved[kind] = True
        values |= saved
    except SourceError as exc:
        values["error"] = str(exc)
    stmt = insert(DiscoverArt).values(artist_key=key, **values)
    await db.execute(
        stmt.on_conflict_do_update(index_elements=[DiscoverArt.artist_key], set_=values)
    )
    await db.commit()


async def fill(db: AsyncSession, http: httpx.AsyncClient, settings: Settings) -> int:
    """Look up the top suggestions not looked up lately (a batch at a time). Returns how
    many it looked at. Nothing without a fanart.tv API key."""
    api_key, _ = await get_fanart_api_key(db, settings)
    if not api_key:
        return 0
    want = await wanted(db)
    if not want:
        return 0
    known = {
        a.artist_key: a
        for a in await db.scalars(select(DiscoverArt).where(DiscoverArt.artist_key.in_(list(want))))
    }
    due = dt.datetime.now(dt.UTC) - LOOK_AGAIN
    todo = [
        key
        for key in want
        if key not in known or known[key].checked_at < due or known[key].error is not None
    ][:PER_RUN]
    for key in todo:
        await _look_up(db, http, settings, api_key, key, want[key], known.get(key))
    return len(todo)


async def missing(db: AsyncSession, keys: list[str]) -> bool:
    """Whether any of these artists hasn't been looked up yet."""
    if not keys:
        return False
    found = set(
        await db.scalars(select(DiscoverArt.artist_key).where(DiscoverArt.artist_key.in_(keys)))
    )
    return any(k not in found for k in keys)


async def for_keys(db: AsyncSession, keys: list[str]) -> dict[str, DiscoverArt]:
    if not keys:
        return {}
    return {
        a.artist_key: a
        for a in await db.scalars(select(DiscoverArt).where(DiscoverArt.artist_key.in_(keys)))
    }
