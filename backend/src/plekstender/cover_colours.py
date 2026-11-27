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
# How colours are worked out: covers worked out by an older method are looked at again.
# 2: clusters added up by family, colourfulness as chroma (white covers with dark text
# were black, cream ones red); and how light the whole cover is.
METHOD = 2
CLUSTERS = 8
# Chroma (0-1) from which a cluster is clearly colourful, and how big a share of the
# biggest neutral family it needs to win over it.
COLOURFUL = 0.25
COLOURFUL_SHARE = 0.6


@dataclass(frozen=True)
class Colour:
    hex: str
    hue: float  # 0-360
    saturation: float  # 0-1 (HLS)
    lightness: float  # 0-1
    brightness: float = 0.5  # 0-1: how light the whole cover is (relative luminance)


@dataclass(frozen=True)
class _Cluster:
    count: int
    rgb: tuple[int, int, int]
    hue: float
    saturation: float
    lightness: float

    @property
    def family(self) -> str:
        return family(self.hue, self.saturation, self.lightness)

    @property
    def chroma(self) -> float:
        return chroma(self.saturation, self.lightness)


def main_colour(image_bytes: bytes) -> Colour:
    """A cover's main colour: its pixels in a few clusters, added up by colour family (a
    white sleeve in three shades of white is white, even if its black text is the biggest
    single cluster). A clearly colourful family nearly as big as a black, grey or white one
    wins (a red logo on a black sleeve stays black; half orange, half grey reads as orange).
    The colour is the family's biggest cluster."""
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB").resize((32, 32))
    quantized = image.quantize(colors=CLUSTERS, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    clusters = []
    found: list[tuple[int, int]] = [(int(n), int(i)) for n, i in quantized.getcolors() or []]  # type: ignore[arg-type]
    for count, index in sorted(found, reverse=True):
        r, g, b = palette[index * 3 : index * 3 + 3]
        h, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        clusters.append(_Cluster(count, (r, g, b), h * 360, sat, light))
    if not clusters:
        raise ValueError("an empty image")
    shares: dict[str, int] = {}
    for c in clusters:
        shares[c.family] = shares.get(c.family, 0) + c.count
    winner = max(shares, key=lambda f: shares[f])
    if winner in NEUTRAL:
        colourful: dict[str, int] = {}
        for c in clusters:
            if c.family not in NEUTRAL and c.chroma >= COLOURFUL:
                colourful[c.family] = colourful.get(c.family, 0) + c.count
        if colourful:
            best = max(colourful, key=lambda f: colourful[f])
            if colourful[best] >= shares[winner] * COLOURFUL_SHARE:
                winner = best
    main = next(c for c in clusters if c.family == winner)
    total = sum(c.count for c in clusters)
    brightness = sum(c.count * _luminance(c.rgb) for c in clusters) / total
    r, g, b = main.rgb
    return Colour(
        f"#{r:02x}{g:02x}{b:02x}",
        round(main.hue, 1),
        round(main.saturation, 3),
        round(main.lightness, 3),
        round(brightness, 3),
    )


def _luminance(rgb: tuple[int, int, int]) -> float:
    """Relative luminance (WCAG), 0-1."""

    def linear(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def chroma(saturation: float, lightness: float) -> float:
    """How colourful (0-1), from HLS: saturation alone overstates it near white and black
    (a cream at 88% lightness can be "40% saturated" and barely coloured)."""
    return (1 - abs(2 * lightness - 1)) * saturation


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
NEUTRAL = ("white", "grey", "black")
GREY = 0.13  # chroma below this: no colour to speak of
PALE = 0.16  # very light and below this: white (cream, off-white)


def family(hue: float, saturation: float, lightness: float) -> str:
    """Which family a colour is in: black, grey or white when there's little colour to it
    (by chroma: see `chroma`)."""
    c = chroma(saturation, lightness)
    if lightness < 0.12 or (c < GREY and lightness < 0.3):
        return "black"
    if lightness > 0.92 or (c < GREY and lightness > 0.75) or (c < PALE and lightness > 0.82):
        return "white"
    if c < GREY:
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
                (AlbumColour.thumb.is_(None))
                | (AlbumColour.thumb != Album.thumb)
                | (AlbumColour.method < METHOD),
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
                "brightness": c.brightness if c else None,
                "method": METHOD,
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
                        for c in (
                            "thumb",
                            "hex",
                            "hue",
                            "saturation",
                            "lightness",
                            "brightness",
                            "method",
                            "checked_at",
                        )
                    },
                )
            )
        await db.commit()
        done += len(todo)
    return done
