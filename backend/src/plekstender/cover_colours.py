"""Album covers' main colours (Library → Stats), worked out by the worker from small
thumbnails Plex makes, and grouped into colour families for the spectrum."""

from __future__ import annotations

import asyncio
import colorsys
import datetime as dt
import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import Album, AlbumColour, UserServer
from plekstender.plex.server import PlexServerClient, client_for
from plekstender.plex.tv import PlexError

log = logging.getLogger(__name__)

THUMB = 64  # px, from Plex's photo transcoder
BATCH = 2000  # covers per run (a few minutes on a local server)
AT_ONCE = 6


@dataclass(frozen=True)
class Colour:
    hex: str
    hue: float  # 0-360
    saturation: float  # 0-1
    lightness: float  # 0-1


def main_colour(image_bytes: bytes) -> Colour:
    """A cover's main colour: the biggest of a few colour clusters, unless a clearly
    colourful one is nearly as big (a red logo on a black sleeve stays black; a cover that's
    half orange, half grey reads as orange)."""
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB").resize((32, 32))
    quantized = image.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    clusters = []
    found: list[tuple[int, int]] = [(int(n), int(i)) for n, i in quantized.getcolors() or []]  # type: ignore[arg-type]
    for count, index in sorted(found, reverse=True):
        r, g, b = palette[index * 3 : index * 3 + 3]
        h, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        clusters.append((count, (r, g, b), h * 360, sat, light))
    if not clusters:
        raise ValueError("an empty image")
    biggest = clusters[0]
    colourful = [c for c in clusters if c[3] >= 0.35 and 0.15 <= c[4] <= 0.85]
    if biggest[3] < 0.35 and colourful and colourful[0][0] >= biggest[0] * 0.6:
        biggest = colourful[0]
    _count, (r, g, b), hue, sat, light = biggest
    return Colour(f"#{r:02x}{g:02x}{b:02x}", round(hue, 1), round(sat, 3), round(light, 3))


# Colour families, by hue (degrees), for colours that aren't near black, grey or white.
FAMILIES: Sequence[tuple[str, float, float]] = (
    ("red", 345, 15),
    ("orange", 15, 40),
    ("yellow", 40, 70),
    ("green", 70, 165),
    ("teal", 165, 195),
    ("blue", 195, 255),
    ("purple", 255, 290),
    ("pink", 290, 345),
)
ORDER = [name for name, _, _ in FAMILIES] + ["white", "grey", "black"]


def family(hue: float, saturation: float, lightness: float) -> str:
    """Which family a colour is in: black, grey or white when there's little colour to it."""
    if lightness < 0.12 or (saturation < 0.15 and lightness < 0.3):
        return "black"
    if lightness > 0.92 or (saturation < 0.15 and lightness > 0.75):
        return "white"
    if saturation < 0.15:
        return "grey"
    for name, start, end in FAMILIES:
        if (start <= hue < end) if start < end else (hue >= start or hue < end):
            return name
    return "red"


async def _colour_of(client: PlexServerClient, thumb: str) -> Colour | None:
    try:
        content, _type = await client.photo(thumb, THUMB)
    except PlexError, httpx.HTTPError:
        return None
    try:
        return await asyncio.to_thread(main_colour, content)
    except Exception:  # Pillow raises many kinds for broken images
        return None


async def colour_covers(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, limit: int = BATCH
) -> int:
    """Work out the colours of covers not looked at yet (or changed), server by server with
    the owner's connection. Returns how many were looked at."""
    owners = list(await db.scalars(select(UserServer).where(UserServer.is_owner)))
    done = 0
    for us in owners:
        if done >= limit:
            break
        rows = await db.execute(
            select(Album.rating_key, Album.thumb)
            .outerjoin(
                AlbumColour,
                (AlbumColour.server_id == Album.server_id)
                & (AlbumColour.rating_key == Album.rating_key),
            )
            .where(
                Album.server_id == us.server_id,
                Album.section_key.in_(us.music_section_keys),
                Album.thumb.is_not(None),
                (AlbumColour.thumb.is_(None)) | (AlbumColour.thumb != Album.thumb),
            )
            .order_by(Album.added_at.desc().nulls_last())
            .limit(limit - done)
        )
        todo = [(key, thumb) for key, thumb in rows.all() if thumb]
        if not todo:
            continue
        try:
            client = client_for(settings, http, us)
        except Exception as exc:  # the token can't be read: reconnecting fixes it
            log.info("Cover colours skipped for server %s: %s", us.server_id, exc)
            continue
        gate = asyncio.Semaphore(AT_ONCE)

        async def one(
            thumb: str, client: PlexServerClient = client, gate: asyncio.Semaphore = gate
        ) -> Colour | None:
            async with gate:
                return await _colour_of(client, thumb)

        colours = await asyncio.gather(*(one(thumb) for _, thumb in todo))
        now = dt.datetime.now(dt.UTC)
        values = [
            {
                "server_id": us.server_id,
                "rating_key": key,
                "thumb": thumb,
                "hex": c.hex if c else None,
                "hue": c.hue if c else None,
                "saturation": c.saturation if c else None,
                "lightness": c.lightness if c else None,
                "checked_at": now,
            }
            for (key, thumb), c in zip(todo, colours, strict=True)
        ]
        for start in range(0, len(values), 1000):
            stmt = insert(AlbumColour).values(values[start : start + 1000])
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["server_id", "rating_key"],
                    set_={
                        c: stmt.excluded[c]
                        for c in ("thumb", "hex", "hue", "saturation", "lightness", "checked_at")
                    },
                )
            )
        await db.commit()
        done += len(todo)
    return done
