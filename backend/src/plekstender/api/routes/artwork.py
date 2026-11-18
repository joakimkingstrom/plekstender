"""Artist artwork editor: candidates, previews, applying posters, per-artist status.

Changing an artist's image affects everyone using the server, so only the server's owner
may use these endpoints.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.library import _like, visible
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import (
    ArtworkArtistOut,
    ArtworkArtistPage,
    ArtworkStatus,
    ArtworkStatusIn,
    CandidateOut,
    CandidatesOut,
    FontGroup,
    UploadOut,
)
from plekstender.artwork import images
from plekstender.artwork import service as art
from plekstender.artwork.images import ImageError, Transform
from plekstender.artwork.text import FONTS
from plekstender.db.models import Artist, ArtistArtwork, User, UserServer
from plekstender.plex.tv import PlexError
from plekstender.sync.parse import epoch

router = APIRouter(prefix="/api/servers/{server_id}/artwork", tags=["artwork"])
fonts_router = APIRouter(prefix="/api/artwork", tags=["artwork"])

MAX_UPLOAD = 15 * 1024 * 1024
THUMB_SIZES = (160, 320, 600)


async def owner_server(db: AsyncSession, user: User, server_id: int) -> UserServer:
    us = await get_user_server(db, user, server_id)
    if not us.is_owner:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the server's owner can change artist images."
        )
    return us


async def get_artist(db: AsyncSession, user: User, us: UserServer, rating_key: int) -> Artist:
    state, cond, where = visible(Artist, user.id, us)
    artist = await db.scalar(
        select(Artist).join(state, cond).where(*where, Artist.rating_key == rating_key)
    )
    if artist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artist not found")
    return artist


def artwork_error(exc: art.ArtworkError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@fonts_router.get("/fonts", response_model=list[FontGroup])
async def fonts(_: UserDep) -> list[FontGroup]:
    return [FontGroup(name=name, fonts=sorted(names)) for name, names in FONTS.items()]


@router.get("/artists", response_model=ArtworkArtistPage)
async def list_artists(
    server_id: int,
    db: DbDep,
    user: UserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[ArtworkStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> ArtworkArtistPage:
    us = await owner_server(db, user, server_id)
    state, cond, where = visible(Artist, user.id, us)
    work = ArtistArtwork
    joined = (
        select(Artist, work)
        .join(state, cond)
        .outerjoin(
            work,
            (work.server_id == Artist.server_id) & (work.rating_key == Artist.rating_key),
        )
        .where(*where)
    )
    current = func.coalesce(work.status, "none")
    counts = dict(
        (
            await db.execute(
                select(current, func.count())
                .select_from(Artist)
                .join(state, cond)
                .outerjoin(
                    work,
                    (work.server_id == Artist.server_id) & (work.rating_key == Artist.rating_key),
                )
                .where(*where)
                .group_by(current)
            )
        ).all()
    )
    if q:
        joined = joined.where(Artist.title.ilike(_like(q), escape="\\"))
    if status_filter:
        joined = joined.where(current == status_filter)
    total = await db.scalar(select(func.count()).select_from(joined.subquery())) or 0
    rows = await db.execute(
        joined.order_by(
            func.lower(func.coalesce(Artist.title_sort, Artist.title)), Artist.rating_key
        )
        .offset(offset)
        .limit(limit)
    )
    return ArtworkArtistPage(
        items=[
            ArtworkArtistOut(
                rating_key=a.rating_key,
                title=a.title,
                thumb=a.thumb,
                status=(w.status if w else "none"),
                source=w.source if w else None,
                applied_at=w.applied_at if w else None,
                has_mbid=bool(a.mbid or (w and w.mbid)),
            )
            for a, w in rows.all()
        ],
        total=total,
        counts={k: int(v) for k, v in counts.items()},
    )


@router.put("/artists/{rating_key}/status", status_code=status.HTTP_204_NO_CONTENT)
async def put_status(
    server_id: int, rating_key: int, body: ArtworkStatusIn, db: DbDep, user: UserDep
) -> Response:
    us = await owner_server(db, user, server_id)
    artist = await get_artist(db, user, us, rating_key)
    await art.set_status(db, artist, body.status, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/artists/{rating_key}/candidates", response_model=CandidatesOut)
async def get_candidates(
    server_id: int,
    rating_key: int,
    source: Literal["fanart", "plex", "search"],
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> CandidatesOut:
    us = await owner_server(db, user, server_id)
    artist = await get_artist(db, user, us, rating_key)
    client = server_client(us, cipher, settings, http)
    try:
        found, note = await art.candidates(db, http, settings, client, artist, source)
    except art.ArtworkError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    out = []
    for c in found:
        token = art.sign_ref(settings, c.url)
        thumb_ref = art.sign_ref(settings, c.thumb_url) if c.thumb_url else token
        out.append(
            CandidateOut(
                token=token,
                thumb=f"/api/servers/{server_id}/artwork/image?t={thumb_ref}",
                label=c.label,
                likes=c.likes,
                width=c.width,
                height=c.height,
            )
        )
    return CandidatesOut(candidates=out, note=note)


@router.get(
    "/image",
    response_class=RawResponse,
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}},
)
async def candidate_image(
    server_id: int,
    t: Annotated[str, Query(max_length=4096)],
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
    size: int = 320,
) -> RawResponse:
    """A candidate's thumbnail, fetched by the server (only signed references)."""
    us = await owner_server(db, user, server_id)
    snapped = min(THUMB_SIZES, key=lambda s: abs(s - size))
    try:
        ref = art.unsign_ref(settings, t)
        data = await art.ref_bytes(http, server_client(us, cipher, settings, http), ref, snapped)
        body, content_type = images.thumbnail(data, snapped)
    except art.ArtworkError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except ImageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Not an image.") from exc
    return RawResponse(
        body, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"}
    )


@router.post(
    "/uploads",
    response_model=UploadOut,
    dependencies=[rate_limit("artwork-upload", limit=30, window=60)],
    openapi_extra={"requestBody": {"content": {"application/octet-stream": {}}, "required": True}},
)
async def upload(
    server_id: int, request: Request, db: DbDep, user: UserDep, settings: SettingsDep
) -> UploadOut:
    """Upload an image file (the raw bytes as the request body)."""
    await owner_server(db, user, server_id)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Images can be up to 15 MB.")
    try:
        return UploadOut(upload_id=art.save_upload(settings, bytes(body)))
    except art.ArtworkError as exc:
        raise artwork_error(exc) from exc


class RenderIn(BaseModel):
    source: art.SourceSpec
    transform: Transform = Transform()


@router.post(
    "/artists/{rating_key}/preview",
    response_class=RawResponse,
    responses={200: {"content": {"image/jpeg": {}}}},
    dependencies=[rate_limit("artwork-preview", limit=120, window=60)],
)
async def preview(
    server_id: int,
    rating_key: int,
    body: RenderIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> RawResponse:
    """The poster as it would be applied (1000 x 1000 JPEG)."""
    us = await owner_server(db, user, server_id)
    artist = await get_artist(db, user, us, rating_key)
    try:
        jpeg = await art.render(
            http,
            settings,
            server_client(us, cipher, settings, http),
            artist,
            body.source,
            body.transform,
        )
    except art.ArtworkError as exc:
        raise artwork_error(exc) from exc
    return RawResponse(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/artists/{rating_key}/apply", response_model=ArtworkArtistOut)
async def apply(
    server_id: int,
    rating_key: int,
    body: RenderIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> ArtworkArtistOut:
    """Make the rendered image the artist's poster in Plex."""
    us = await owner_server(db, user, server_id)
    artist = await get_artist(db, user, us, rating_key)
    client = server_client(us, cipher, settings, http)
    try:
        row = await art.apply(
            db, http, settings, client, artist, body.source, body.transform, user.id
        )
    except art.ArtworkError as exc:
        raise artwork_error(exc) from exc
    # Pick up the new poster's path so the app shows it straight away.
    try:
        [meta] = await client.metadata([rating_key])
        artist.thumb = meta.get("thumb") or artist.thumb
        artist.plex_updated_at = epoch(meta.get("updatedAt")) or artist.plex_updated_at
        await db.commit()
    except PlexError, ValueError:
        pass
    return ArtworkArtistOut(
        rating_key=artist.rating_key,
        title=artist.title,
        thumb=artist.thumb,
        status=row.status,
        source=row.source,
        applied_at=row.applied_at,
        has_mbid=bool(artist.mbid or row.mbid),
    )
