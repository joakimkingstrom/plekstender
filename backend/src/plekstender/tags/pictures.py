"""Cover pictures: embedded in music files (FLAC, MP3, MP4/M4A, Ogg Vorbis/Opus) and kept
by Plekstender (worker side).

- `embedded` / `embed` / `unembed` read and replace a file's front cover.
- `prepare` makes a picture players can show: JPEG (baseline, not progressive: some car
  stereos and older players can't show those), at most `max_px` on its longest side, and
  small enough (quality lowered step by step).
- The store keeps pictures by their SHA-1 under DATA_DIR/pictures (every picture that's
  replaced, for undo) and small thumbnails under DATA_DIR/thumbs (for pages).
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from PIL import Image, UnidentifiedImageError

FRONT = 3  # the picture type for a front cover (ID3 / FLAC)
MAX_PX = 1000
MAX_BYTES = 500_000
THUMB_PX = 300


class PictureError(Exception):
    pass


@dataclass
class Info:
    sha1: str
    mime: str
    bytes: int
    width: int | None
    height: int | None


def sha1(data: bytes) -> str:
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def info(data: bytes, mime: str | None = None) -> Info:
    width = height = None
    try:
        with Image.open(io.BytesIO(data)) as image:  # reads the header only
            width, height = image.size
            mime = mime or Image.MIME.get(image.format or "", None)
    except UnidentifiedImageError, OSError, ValueError:
        pass
    return Info(sha1(data), mime or "image/jpeg", len(data), width, height)


# --- in music files ---------------------------------------------------------------------------


def _front(pictures: list[Any]) -> Any | None:
    """The front cover, else the first picture."""
    if not pictures:
        return None
    return next((p for p in pictures if p.type == FRONT), pictures[0])


def embedded(path: Path) -> tuple[bytes, str] | None:
    """The file's front cover (bytes and MIME type), if it has one."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".flac":
            pic = _front(FLAC(path).pictures)
            return (pic.data, pic.mime) if pic else None
        if suffix == ".mp3":
            try:
                frame = _front(ID3(path).getall("APIC"))
            except ID3NoHeaderError:
                return None
            return (frame.data, frame.mime) if frame else None
        if suffix in {".m4a", ".mp4", ".aac", ".alac"}:
            tags: Any = MP4(path).tags
            covers = (tags or {}).get("covr") or []
            if not covers:
                return None
            cover = covers[0]
            mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            return bytes(cover), mime
        if suffix in {".ogg", ".oga", ".opus"}:
            ogg: Any = mutagen.File(path)
            blocks = (ogg.tags or {}).get("metadata_block_picture") or []
            pics = []
            for block in blocks:
                try:
                    pics.append(Picture(base64.b64decode(block)))
                except ValueError, TypeError:
                    continue
            pic = _front(pics)
            return (pic.data, pic.mime) if pic else None
    except Exception as exc:  # mutagen raises many kinds for broken files
        raise PictureError(f"Couldn't read the file: {exc}") from exc
    return None


EMBEDDABLE = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".oga", ".opus"}
IMAGE_NAMES = {"cover", "folder", "front", "album", "artist"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def can_embed(path: Path) -> bool:
    return path.suffix.lower() in EMBEDDABLE


def image_kind(name: str) -> str | None:
    """ "cover" for cover.jpg, folder.jpg..., "artist" for artist.jpg; None otherwise."""
    stem, _, ext = name.lower().rpartition(".")
    if f".{ext}" not in IMAGE_EXTENSIONS or stem not in IMAGE_NAMES:
        return None
    return "artist" if stem == "artist" else "cover"


def cover_of(path: Path, data_dir: Path) -> dict[str, Any] | None:
    """A file's embedded front cover, described (and thumbnailed for pages)."""
    try:
        found = embedded(path)
    except PictureError:
        return None
    if found is None:
        return None
    data, mime = found
    described = info(data, mime)
    thumbnail(data_dir, data, described.sha1)
    return {
        "sha1": described.sha1,
        "mime": described.mime,
        "bytes": described.bytes,
        "width": described.width,
        "height": described.height,
    }


def embed(path: Path, data: bytes | None, mime: str = "image/jpeg") -> None:
    """Make `data` the file's only front cover (other pictures, like a back cover, stay).
    None removes the front cover."""
    suffix = path.suffix.lower()
    if not can_embed(path):
        raise PictureError("Plekstender can't put a cover in this kind of file.")
    width = height = 0
    if data is not None:
        found = info(data, mime)
        width, height = found.width or 0, found.height or 0
    try:
        if suffix == ".flac":
            flac: Any = FLAC(path)
            others = [p for p in flac.pictures if p.type != FRONT]
            flac.clear_pictures()
            for pic in others:
                flac.add_picture(pic)
            if data is not None:
                pic = Picture()
                pic.type, pic.mime, pic.desc, pic.data = FRONT, mime, "", data
                pic.width, pic.height, pic.depth = width, height, 24
                flac.add_picture(pic)
            flac.save()
        elif suffix == ".mp3":
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                id3 = ID3()
            version = id3.version[1] if id3.version else 4
            keep = [f for f in id3.getall("APIC") if f.type != FRONT]
            id3.delall("APIC")
            for frame in keep:
                id3.add(frame)
            if data is not None:
                id3.add(APIC(encoding=3, mime=mime, type=FRONT, desc="", data=data))
            if version == 3:
                id3.update_to_v23()
                id3.save(path, v2_version=3)
            else:
                id3.save(path)
        elif suffix in {".m4a", ".mp4", ".aac", ".alac"}:
            mp4: Any = MP4(path)
            if mp4.tags is None:
                mp4.add_tags()
            if data is None:
                mp4.tags.pop("covr", None)
            else:
                kind = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
                mp4.tags["covr"] = [MP4Cover(data, imageformat=kind)]
            mp4.save()
        else:
            vorbis: Any = OggOpus(path) if suffix == ".opus" else OggVorbis(path)
            blocks = []
            for block in (vorbis.tags or {}).get("metadata_block_picture") or []:
                try:
                    if Picture(base64.b64decode(block)).type != FRONT:
                        blocks.append(block)
                except ValueError, TypeError:
                    continue
            if data is not None:
                pic = Picture()
                pic.type, pic.mime, pic.desc, pic.data = FRONT, mime, "", data
                pic.width, pic.height, pic.depth = width, height, 24
                blocks.append(base64.b64encode(pic.write()).decode("ascii"))
            if blocks:
                vorbis["metadata_block_picture"] = blocks
            elif "metadata_block_picture" in (vorbis.tags or {}):
                del vorbis["metadata_block_picture"]
            vorbis.save()
    except PictureError:
        raise
    except Exception as exc:
        raise PictureError(f"Couldn't write the cover: {exc}") from exc


# --- making a picture players can show --------------------------------------------------------


def prepare(data: bytes, max_px: int = MAX_PX, max_bytes: int = MAX_BYTES) -> bytes:
    """A baseline JPEG at most `max_px` on its longest side and (if it can be) `max_bytes`."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise PictureError("That isn't a picture Plekstender can read.") from exc
    image.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    out = b""
    for quality in (90, 85, 80, 75, 70, 60):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality, progressive=False, optimize=True)
        out = buffer.getvalue()
        if len(out) <= max_bytes:
            break
    return out


def needs_shrinking(
    found: dict[str, Any] | None, max_px: int = MAX_PX, max_bytes: int = MAX_BYTES
) -> bool:
    if not found:
        return False
    longest = max(found.get("width") or 0, found.get("height") or 0)
    return longest > max_px or int(found.get("bytes") or 0) > max_bytes * 2


# --- the store --------------------------------------------------------------------------------


def keep(data_dir: Path, data: bytes) -> str:
    """Keep a picture (for undo); its SHA-1."""
    digest = sha1(data)
    path = data_dir / "pictures" / digest[:2] / digest
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(data)
        temporary.replace(path)
    return digest


def kept(data_dir: Path, digest: str) -> bytes:
    if len(digest) != 40 or not all(c in "0123456789abcdef" for c in digest):
        raise PictureError("Not a picture ID.")
    path = data_dir / "pictures" / digest[:2] / digest
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PictureError("That picture isn't kept any more.") from exc


def thumbnail(data_dir: Path, data: bytes, digest: str | None = None) -> str:
    """A small JPEG of a picture for pages (made once per picture); its SHA-1 (the full
    picture's)."""
    digest = digest or sha1(data)
    path = data_dir / "thumbs" / digest[:2] / f"{digest}.jpg"
    if not path.exists():
        try:
            small = prepare(data, THUMB_PX, 60_000)
        except PictureError:
            return digest
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(small)
        temporary.replace(path)
    return digest


def thumbnail_path(data_dir: Path, digest: str) -> Path | None:
    if len(digest) != 40 or not all(c in "0123456789abcdef" for c in digest):
        return None
    path = data_dir / "thumbs" / digest[:2] / f"{digest}.jpg"
    return path if path.exists() else None
