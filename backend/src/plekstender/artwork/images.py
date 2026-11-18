"""Image decoding and the logo transform pipeline (ported from plex-artist-logos).

Every image is decoded with Pillow from bytes (never trusted by extension), limited in
size, and re-encoded, so only plain pixels ever leave this module.
"""

from __future__ import annotations

import io
import re

from PIL import Image, ImageChops, ImageEnhance, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator

CANVAS = 1000  # artist posters are square
FIT_SHARE = 0.8  # "default size": the logo fills 80% of the canvas
MAX_PIXELS = 40_000_000
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP"}
HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageError(ValueError):
    pass


def hex_rgb(value: str) -> tuple[int, int, int]:
    match = HEX.match(value)
    if not match:
        raise ValueError(f"not a colour: {value!r}")
    h = match.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class Transform(BaseModel):
    """How a source image becomes an artist poster."""

    fit: bool = Field(default=True, description="Fit the logo into 80% of the square.")
    zoom: float = Field(default=1.0, ge=0.25, le=4.0, description="Scale when not fitting.")
    contrast: float = Field(default=1.0, ge=0.0, le=3.0)
    invert: bool = False
    greyscale: bool = False
    white: bool = Field(default=False, description="Paint every visible pixel white.")
    tint: str | None = Field(default=None, description="#RRGGBB multiplied over the result.")
    background: str = Field(default="#000000", description="Canvas colour behind the logo.")

    @field_validator("tint", "background")
    @classmethod
    def _colour(cls, value: str | None) -> str | None:
        if value is not None:
            hex_rgb(value)
        return value


def open_image(data: bytes) -> Image.Image:
    """Decode untrusted image bytes (raises ImageError for anything that isn't one)."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED_FORMATS:
            raise ImageError(f"unsupported image format {img.format}")
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise ImageError("not a readable image") from exc
    if img.width * img.height > MAX_PIXELS:
        raise ImageError("image is too large")
    return img


def apply_transform(img: Image.Image, t: Transform) -> Image.Image:
    """Crop to the visible logo, adjust it, and centre it on a square canvas."""
    img = img.convert("RGBA")
    if bbox := img.getbbox():
        img = img.crop(bbox)
    if t.white:
        alpha = img.getchannel("A")
        img = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img.putalpha(alpha)
    if t.greyscale:
        alpha = img.getchannel("A")
        img = img.convert("L").convert("RGBA")
        img.putalpha(alpha)
    if t.invert:
        r, g, b, a = img.split()
        inverted = ImageOps.invert(Image.merge("RGB", (r, g, b)))
        img = Image.merge("RGBA", (*inverted.split(), a))
    if t.contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(t.contrast)

    if img.width and img.height:
        if t.fit:
            target = CANVAS * FIT_SHARE
            ratio = min(target / img.width, target / img.height)
        else:
            ratio = t.zoom
            # Never larger than the canvas.
            ratio = min(ratio, CANVAS / img.width, CANVAS / img.height)
        size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
        img = img.resize(size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (CANVAS, CANVAS), hex_rgb(t.background))
    canvas.paste(img, ((CANVAS - img.width) // 2, (CANVAS - img.height) // 2), img)
    if t.tint:
        multiplied = ImageChops.multiply(canvas, Image.new("RGB", canvas.size, hex_rgb(t.tint)))
        canvas = Image.blend(canvas, multiplied, alpha=0.6)
    return canvas


def to_jpeg(img: Image.Image, quality: int = 92) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def to_png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def thumbnail(data: bytes, size: int) -> tuple[bytes, str]:
    """A small preview of untrusted image bytes, keeping transparency (PNG) where present."""
    img = open_image(data)
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    if img.mode in ("RGBA", "LA", "P"):
        return to_png(img.convert("RGBA")), "image/png"
    return to_jpeg(img, quality=85), "image/jpeg"
