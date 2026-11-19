"""The Google Fonts catalogue: every family, its exact name, category and popularity.

Fetched from Google Fonts' public metadata (no key) at most once a week and kept under
DATA_DIR/fonts, so admins can browse fonts, add them without minding capitals, and have
them filed under the right group automatically.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from plekstender import __version__

log = logging.getLogger(__name__)

URL = "https://fonts.google.com/metadata/fonts"
MAX_AGE = dt.timedelta(days=7)
FILE = "catalogue.json"
# Google's categories -> the text logo's font groups.
GROUPS = {
    "Sans Serif": "Sans serif",
    "Serif": "Serif",
    "Display": "Display",
    "Handwriting": "Handwriting",
    "Monospace": "Monospace",
}

_lock = asyncio.Lock()
_cache: tuple[float, list[Family]] | None = None


@dataclass(frozen=True)
class Family:
    name: str
    group: str
    popularity: int  # rank: 1 is the most used
    weight: int  # the regular weight, or the nearest the family has


def _parse(raw: str) -> list[Family]:
    data = json.loads(raw[raw.index("{") :])  # Google prefixes ")]}'" sometimes
    families = []
    for item in data.get("familyMetadataList", []):
        name, category = item.get("family"), item.get("category")
        if not isinstance(name, str) or category not in GROUPS:
            continue
        if "latin" not in (item.get("subsets") or []):
            continue  # artist names are (mostly) in Latin letters
        weights = sorted(
            int(w) for w in (item.get("fonts") or {}) if w.isdigit()
        )  # "400", "700" (not italics)
        weight = 400 if 400 in weights or not weights else min(weights, key=lambda w: abs(w - 400))
        families.append(
            Family(name, GROUPS[category], int(item.get("popularity") or 99_999), weight)
        )
    families.sort(key=lambda f: f.popularity)
    return families


async def catalogue(http: httpx.AsyncClient, fonts_dir: Path) -> list[Family]:
    """The catalogue (refreshed weekly; an old copy is used when Google can't be reached)."""
    global _cache
    path = fonts_dir / FILE
    async with _lock:
        mtime = await asyncio.to_thread(lambda: path.stat().st_mtime if path.exists() else 0.0)
        fresh = dt.datetime.now(dt.UTC).timestamp() - mtime < MAX_AGE.total_seconds()
        if _cache is not None and _cache[0] == mtime and fresh:
            return _cache[1]
        if not fresh:
            try:
                resp = await http.get(
                    URL,
                    timeout=30,
                    headers={
                        "User-Agent": f"Plekstender/{__version__} (self-hosted music library tool)"
                    },
                )
                resp.raise_for_status()
                _parse(resp.text)  # refuse a broken file before keeping it
                await asyncio.to_thread(fonts_dir.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(path.write_text, resp.text, "utf-8")
                mtime = await asyncio.to_thread(lambda: path.stat().st_mtime)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                log.warning("Google Fonts catalogue unavailable: %s", exc)
                if not await asyncio.to_thread(path.exists):
                    return []
        families = _parse(await asyncio.to_thread(path.read_text, "utf-8"))
        _cache = (mtime, families)
        return families


async def find(http: httpx.AsyncClient, fonts_dir: Path, name: str) -> Family | None:
    """A family by name, whatever the capitals ("roboto" -> "Roboto")."""
    wanted = " ".join(name.split()).casefold()
    return next((f for f in await catalogue(http, fonts_dir) if f.name.casefold() == wanted), None)
