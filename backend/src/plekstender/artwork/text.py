"""Text logos in Google Fonts (ported from plex-artist-logos)."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator

from plekstender.artwork.images import hex_rgb

log = logging.getLogger(__name__)

# The default list (curated in plex-artist-logos), grouped as there. Admins can change the
# instance's list in Settings; see plekstender.db.instance.get_fonts.
FONTS: dict[str, list[str]] = {
    "Sans serif": ["Anton", "Raleway"],
    "Serif": ["Cinzel", "Merriweather"],
    "Display": [
        "Allerta Stencil", "Audiowide", "Caesar Dressing", "Coiny", "Fascinate Inline",
        "Faster One", "Frijole", "Germania One", "Gravitas One", "Honk", "Lobster",
        "Metal Mania", "Monoton", "Nosifer", "Orbitron", "Pirata One", "Protest Guerrilla",
        "Rubik Burned", "Rubik Doodle Shadow", "Rubik Glitch", "Rubik Spray Paint",
        "Russo One", "Rye", "Tourney", "UnifrakturMaguntia", "Workbench",
    ],
    "Handwriting": [
        "Alex Brush", "Allura", "Dancing Script", "Gochi Hand", "Great Vibes",
        "Indie Flower", "Italianno", "Mr Dafoe", "Pacifico", "Permanent Marker", "Rock Salt",
    ],
    "Monospace": ["Press Start 2P", "Special Elite", "Bitcount Grid Double Ink"],
}  # fmt: skip
GROUPS = tuple(FONTS)
# Google Fonts family names: letters, digits and spaces.
FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,63}$")
GOOGLE_CSS = "https://fonts.googleapis.com/css2"
FONT_SIZE = 400
LINE_GAP = 50

_locks: dict[str, asyncio.Lock] = {}


class FontError(RuntimeError):
    pass


class TextLogo(BaseModel):
    text: str = Field(min_length=1, max_length=120)
    font: str
    rows: int = Field(default=1, ge=1, le=4)
    color: str = "#ffffff"
    case: Literal["none", "upper", "lower"] = "none"

    @field_validator("font")
    @classmethod
    def _font_name(cls, value: str) -> str:
        # Whether the instance offers it is checked by the API (it's a setting).
        if not FONT_NAME.match(value):
            raise ValueError(f"invalid font name {value!r}")
        return value

    @field_validator("color")
    @classmethod
    def _colour(cls, value: str) -> str:
        hex_rgb(value)
        return value


def _file_name(font: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", font) + ".ttf"


def _store(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)


async def _font_url(http: httpx.AsyncClient, font: str, weight: int = 400) -> str:
    """The URL of the font's TTF (regular, or `weight` for families without one), from the
    Google Fonts CSS API."""
    family = font if weight == 400 else f"{font}:wght@{weight}"
    try:
        # Without a browser User-Agent, Google serves TrueType (which FreeType reads).
        css = await http.get(GOOGLE_CSS, params={"family": family}, timeout=20)
    except httpx.HTTPError as exc:
        raise FontError("Google Fonts couldn't be reached; try again.") from exc
    if css.status_code == 400:
        raise FontError(f"Google Fonts has no font called {font!r}.")
    if css.is_error:
        raise FontError(f"Google Fonts is unavailable (HTTP {css.status_code}).")
    urls = re.findall(r"url\((https://[^)]+)\)", css.text)
    if not urls:
        raise FontError(f"Google Fonts returned no file for {font!r}")
    return str(urls[-1])


async def check_font(http: httpx.AsyncClient, font: str) -> None:
    """Raise FontError unless Google Fonts has this family."""
    if not FONT_NAME.match(font):
        raise FontError("Font names are letters, digits and spaces, as on fonts.google.com.")
    await _font_url(http, font)


async def font_path(http: httpx.AsyncClient, fonts_dir: Path, font: str, weight: int = 400) -> Path:
    """The font's TTF, downloaded from Google Fonts the first time it's used."""
    if not FONT_NAME.match(font):
        raise FontError(f"invalid font name {font!r}")
    path = fonts_dir / _file_name(font)
    if await asyncio.to_thread(path.exists):
        return path
    async with _locks.setdefault(font, asyncio.Lock()):
        if await asyncio.to_thread(path.exists):
            return path
        try:
            url = await _font_url(http, font, weight)
        except FontError:
            # A few families have no regular weight: the catalogue says which they have.
            from plekstender.artwork.catalogue import find

            known = await find(http, fonts_dir, font)
            if known is None or known.weight == weight:
                raise
            url = await _font_url(http, font, known.weight)
        try:
            data = await http.get(url, timeout=30)
            data.raise_for_status()
        except httpx.HTTPError as exc:
            raise FontError(f"Could not download the font {font!r}.") from exc
        await asyncio.to_thread(_store, path, data.content)
        log.info("downloaded font %s", font)
        return path


def render_text(spec: TextLogo, font_file: Path) -> Image.Image:
    """The text on a transparent background, in up to `rows` centred lines."""
    text = spec.text.upper() if spec.case == "upper" else spec.text
    text = text.lower() if spec.case == "lower" else text
    words = text.split() or [text]
    per_line = -(-len(words) // spec.rows)
    lines = [" ".join(words[i : i + per_line]) for i in range(0, len(words), per_line)]
    try:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype(
            str(font_file), FONT_SIZE
        )
    except OSError as exc:
        raise FontError(f"The font {spec.font!r} couldn't be read.") from exc

    colour = hex_rgb(spec.color)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    rendered = []
    for line in lines:
        left, top, right, bottom = probe.textbbox((0, 0), line, font=font)
        img = Image.new("RGBA", (max(1, int(right - left)), max(1, int(bottom - top))))
        ImageDraw.Draw(img).text((-left, -top), line, font=font, fill=(*colour, 255))
        rendered.append(img)
    width = max(i.width for i in rendered)
    height = sum(i.height for i in rendered) + LINE_GAP * (len(rendered) - 1)
    combined = Image.new("RGBA", (width, height))
    y = 0
    for img in rendered:
        combined.paste(img, ((width - img.width) // 2, y), img)
        y += img.height + LINE_GAP
    return combined


PREVIEW_SIZE = 72
PREVIEW_MAX_WIDTH = 1400


def render_preview(font_file: Path, text: str, colour: str) -> bytes:
    """A line of text in the font (a transparent PNG, cropped to the text), for previews."""
    try:
        font = ImageFont.truetype(str(font_file), PREVIEW_SIZE)
    except OSError as exc:
        raise FontError("The font couldn't be read.") from exc
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    width, height = max(1, int(right - left)), max(1, int(bottom - top))
    pad = 6
    img = Image.new("RGBA", (width + 2 * pad, height + 2 * pad))
    ImageDraw.Draw(img).text((pad - left, pad - top), text, font=font, fill=(*hex_rgb(colour), 255))
    if img.width > PREVIEW_MAX_WIDTH:
        img = img.crop((0, 0, PREVIEW_MAX_WIDTH, img.height))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
