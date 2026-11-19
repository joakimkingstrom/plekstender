"""Artwork operations: candidates per source, loading a chosen source, rendering, applying."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.artwork import images, sources, text
from plekstender.artwork.fetch import FetchError, fetch
from plekstender.artwork.folders import FolderError, save_to_folder
from plekstender.artwork.images import ImageError, Transform
from plekstender.artwork.sources import Candidate, SourceError
from plekstender.artwork.text import TextLogo
from plekstender.config import Settings
from plekstender.db.instance import get_fanart_api_key
from plekstender.db.models import Artist, ArtistArtwork, ImageSearchCache, PlexServer
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError
from plekstender.security.crypto import Signer

SourceKind = Literal["fanart", "plex", "search"]
SEARCH_TTL = dt.timedelta(days=30)
TOKEN_MAX_AGE = 24 * 3600
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")


class ArtworkError(Exception):
    """Something the user should be told (bad source, unreachable service...)."""


class SourceSpec(BaseModel):
    """The image to start from."""

    kind: Literal["candidate", "upload", "text", "current"]
    token: str | None = None  # candidate: from the candidates list
    upload_id: str | None = None  # upload: from POST /uploads
    text: TextLogo | None = None  # text: a generated logo

    @model_validator(mode="after")
    def _complete(self) -> SourceSpec:
        needed = {"candidate": self.token, "upload": self.upload_id, "text": self.text}
        if self.kind in needed and needed[self.kind] is None:
            raise ValueError(f"a {self.kind} source needs its details")
        if self.upload_id is not None and not UPLOAD_ID.match(self.upload_id):
            raise ValueError("invalid upload id")
        return self


def signer(settings: Settings) -> Signer:
    return Signer(settings.derive_key("artwork-image"))


def sign_ref(settings: Settings, ref: str) -> str:
    return signer(settings).sign(ref)


def unsign_ref(settings: Settings, token: str) -> str:
    ref = signer(settings).unsign(token, max_age=TOKEN_MAX_AGE)
    if ref is None:
        raise ArtworkError("This image link has expired; reload the candidates.")
    return ref


# --- candidates -------------------------------------------------------------------------


async def artist_mbid(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, artist: Artist
) -> str | None:
    """Plex's MusicBrainz ID for the artist, else one found on MusicBrainz by name."""
    if artist.mbid:
        return artist.mbid
    row = await db.get(ArtistArtwork, (artist.server_id, artist.rating_key))
    if row is not None and row.mbid:
        return row.mbid
    try:
        found = await sources.musicbrainz_artist_id(
            http, settings.musicbrainz_api_url, artist.title
        )
    except SourceError:
        return None
    if found:
        await _upsert(db, artist, mbid=found)
        await db.commit()
    return found


async def candidates(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    client: PlexServerClient,
    artist: Artist,
    source: SourceKind,
) -> tuple[list[Candidate], str | None]:
    """Candidate images from one source, and a note to show when there are none."""
    if source == "fanart":
        api_key, _ = await get_fanart_api_key(db, settings)
        if not api_key:
            return [], "fanart.tv isn't set up: an admin can add an API key in Settings."
        mbid = await artist_mbid(db, http, settings, artist)
        if not mbid:
            return [], "This artist has no MusicBrainz ID, so fanart.tv can't be searched."
        try:
            found = await sources.fanart_logos(http, settings.fanart_api_url, api_key, mbid)
        except SourceError as exc:
            raise ArtworkError(str(exc)) from exc
        return found, None if found else "fanart.tv has no logos for this artist yet."
    if source == "plex":
        try:
            posters = await client.posters(artist.rating_key)
        except PlexError as exc:
            raise ArtworkError(f"Plex didn't list the posters: {exc}") from exc
        out = [
            Candidate(
                url=f"plex:{p['key']}",
                label=("Current · " if p.get("selected") else "")
                + str(p.get("provider") or "Plex"),
                extra={"selected": bool(p.get("selected"))},
            )
            for p in posters
            if isinstance(p.get("key"), str) and p["key"]
        ]
        return out, None if out else "Plex has no other posters for this artist."
    if not settings.searxng_url:
        return [], "Image search isn't set up on this instance (SEARXNG_URL)."
    query = f"{artist.title} logo"
    return await cached_search(db, http, settings.searxng_url, query), None


async def cached_search(
    db: AsyncSession, http: httpx.AsyncClient, searxng_url: str, query: str
) -> list[Candidate]:
    key = query.strip().lower()
    cached = await db.get(ImageSearchCache, key)
    if cached is not None and cached.created_at > dt.datetime.now(dt.UTC) - SEARCH_TTL:
        return [Candidate(**c) for c in cached.results]
    try:
        found = await sources.search_images(http, searxng_url, query)
    except SourceError as exc:
        raise ArtworkError(str(exc)) from exc
    stmt = insert(ImageSearchCache).values(query=key, results=[c.__dict__ for c in found])
    stmt = stmt.on_conflict_do_update(
        index_elements=[ImageSearchCache.query],
        set_={"results": stmt.excluded.results, "created_at": dt.datetime.now(dt.UTC)},
    )
    await db.execute(stmt)
    await db.commit()
    return found


# --- loading a source ---------------------------------------------------------------------


async def ref_bytes(
    http: httpx.AsyncClient, client: PlexServerClient, ref: str, size: int = 1000
) -> bytes:
    """The image behind a signed reference: a public URL, or a Plex poster."""
    try:
        if ref.startswith("plex:"):
            data, _ = await client.photo(ref.removeprefix("plex:"), size)
            return data
        data, _ = await fetch(http, ref)
        return data
    except (FetchError, PlexError) as exc:
        raise ArtworkError(str(exc) or "The image couldn't be downloaded.") from exc


def uploads_dir(settings: Settings) -> Path:
    return settings.data_dir / "uploads"


def save_upload(settings: Settings, data: bytes) -> str:
    """Store an uploaded image (decoded and re-encoded as PNG). Returns its id."""
    try:
        img = images.open_image(data)
    except ImageError as exc:
        raise ArtworkError("That file isn't an image we can read (PNG, JPEG, WebP, GIF).") from exc
    upload_id = uuid.uuid4().hex
    folder = uploads_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{upload_id}.png").write_bytes(images.to_png(img.convert("RGBA")))
    return upload_id


def remove_old_uploads(settings: Settings, older_than: dt.timedelta) -> int:
    folder = uploads_dir(settings)
    if not folder.exists():
        return 0
    cutoff = dt.datetime.now(dt.UTC).timestamp() - older_than.total_seconds()
    removed = 0
    for path in folder.glob("*.png"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


async def load_source(
    http: httpx.AsyncClient,
    settings: Settings,
    client: PlexServerClient,
    artist: Artist,
    spec: SourceSpec,
) -> Image.Image:
    if spec.kind == "text":
        assert spec.text is not None
        try:
            path = await text.font_path(http, settings.data_dir / "fonts", spec.text.font)
            return text.render_text(spec.text, path)
        except text.FontError as exc:
            raise ArtworkError(str(exc)) from exc
    if spec.kind == "upload":
        path = uploads_dir(settings) / f"{spec.upload_id}.png"
        if not path.exists():
            raise ArtworkError("The upload has expired; upload the image again.")
        data = path.read_bytes()
    elif spec.kind == "current":
        if not artist.thumb:
            raise ArtworkError("This artist has no image in Plex yet.")
        data = await ref_bytes(http, client, f"plex:{artist.thumb}")
    else:
        assert spec.token is not None
        data = await ref_bytes(http, client, unsign_ref(settings, spec.token))
    try:
        return images.open_image(data)
    except ImageError as exc:
        raise ArtworkError("That image couldn't be read.") from exc


async def render(
    http: httpx.AsyncClient,
    settings: Settings,
    client: PlexServerClient,
    artist: Artist,
    spec: SourceSpec,
    transform: Transform,
) -> bytes:
    img = await load_source(http, settings, client, artist, spec)
    return images.to_jpeg(images.apply_transform(img, transform))


# --- status and applying ------------------------------------------------------------------


async def _upsert(db: AsyncSession, artist: Artist, **values: object) -> None:
    stmt = insert(ArtistArtwork).values(
        server_id=artist.server_id, rating_key=artist.rating_key, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ArtistArtwork.server_id, ArtistArtwork.rating_key],
        set_={**values, "updated_at": dt.datetime.now(dt.UTC)},
    )
    await db.execute(stmt)


async def set_status(db: AsyncSession, artist: Artist, status: str, user_id: int) -> None:
    await _upsert(db, artist, status=status, updated_by=user_id)
    await db.commit()


def source_name(spec: SourceSpec, settings: Settings) -> tuple[str, str | None]:
    """(source, url) recorded for an applied image."""
    if spec.kind == "text":
        return "text", None
    if spec.kind == "upload":
        return "upload", None
    if spec.kind == "current":
        return "plex", None
    ref = unsign_ref(settings, spec.token or "")
    if ref.startswith("plex:"):
        return "plex", None
    return ("fanart" if "fanart.tv" in ref else "search"), ref


async def apply(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    client: PlexServerClient,
    artist: Artist,
    spec: SourceSpec,
    transform: Transform,
    user_id: int,
) -> Applied:
    """Render the image and make it the artist's poster in Plex (and, if set up, save it
    as artist.jpg in the artist's folder; see `Applied.folder` / `folder_error`)."""
    jpeg = await render(http, settings, client, artist, spec, transform)
    try:
        await client.upload_poster(artist.rating_key, jpeg)
    except PlexError as exc:
        raise ArtworkError(f"Plex didn't accept the image: {exc}") from exc
    folder, folder_error = await _save_folder(db, artist, jpeg)
    source, url = source_name(spec, settings)
    settings_json: dict[str, object] = {"transform": transform.model_dump()}
    if spec.text is not None:
        settings_json["text"] = spec.text.model_dump()
    now = dt.datetime.now(dt.UTC)
    await _upsert(
        db,
        artist,
        status="custom" if spec.kind == "text" else "done",
        source=source,
        source_url=url,
        settings=settings_json,
        image_sha256=hashlib.sha256(jpeg).hexdigest(),
        applied_at=now,
        updated_by=user_id,
    )
    await db.commit()
    row = await db.scalar(
        select(ArtistArtwork)
        .where(
            ArtistArtwork.server_id == artist.server_id,
            ArtistArtwork.rating_key == artist.rating_key,
        )
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return Applied(row, folder, folder_error)


@dataclass
class Applied:
    artwork: ArtistArtwork
    folder: str | None = None  # where artist.jpg was written
    folder_error: str | None = None  # why it wasn't, when folder saving is on


async def _save_folder(
    db: AsyncSession, artist: Artist, jpeg: bytes
) -> tuple[str | None, str | None]:
    server = await db.get(PlexServer, artist.server_id)
    if server is None or not server.folder_save:
        return None, None
    try:
        return str(await save_to_folder(db, server, artist, jpeg)), None
    except FolderError as exc:
        return None, str(exc)
