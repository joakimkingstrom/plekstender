"""Artist artwork editor: candidates, previews, applying posters, per-artist status.

Changing an artist's image affects everyone using the server, so only the server's owner
may use these endpoints.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
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
    ArtworkJobOut,
    ArtworkStatus,
    ArtworkStatusIn,
    CandidateOut,
    CandidatesOut,
    FanartJobIn,
    FolderExample,
    FolderSettingsIn,
    FolderSettingsOut,
    FontGroup,
    ImportApplyIn,
    ImportEntryOut,
    ImportMatchOut,
    ImportPreviewOut,
    UploadOut,
)
from plekstender.artwork import images, packs
from plekstender.artwork import service as art
from plekstender.artwork.images import ImageError, Transform
from plekstender.db.instance import get_fonts
from plekstender.db.models import (
    Artist,
    ArtistArtwork,
    ArtworkJob,
    PlexServer,
    Track,
    User,
    UserServer,
)
from plekstender.plex.tv import PlexError
from plekstender.sync.parse import epoch
from plekstender.worker import (
    ACTIVE,
    enqueue_artwork_job,
    enqueue_folder_check,
    enqueue_folder_save,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/servers/{server_id}/artwork", tags=["artwork"])
fonts_router = APIRouter(prefix="/api/artwork", tags=["artwork"])

MAX_UPLOAD = 15 * 1024 * 1024
THUMB_SIZES = (160, 320, 600)


async def owner_server(db: AsyncSession, user: User, server_id: int) -> UserServer:
    us = await get_user_server(db, user, server_id)
    if not us.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the server's owner can do this.")
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
async def fonts(db: DbDep, _: UserDep) -> list[FontGroup]:
    """The fonts the text logo offers (an admin setting)."""
    return [FontGroup(name=name, fonts=names) for name, names in (await get_fonts(db)).items()]


async def check_text_font(db: AsyncSession, source: art.SourceSpec) -> None:
    """A text logo may only use a font the instance offers."""
    if source.kind != "text" or source.text is None:
        return
    offered = {name for names in (await get_fonts(db)).values() for name in names}
    if source.text.font not in offered:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"The font {source.text.font!r} isn't offered here."
        )


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
                folder=w.folder_path if w else None,
                folder_error=w.folder_error if w else None,
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
        # Image URLs contain "&" and "%": the references must be escaped in a link.
        thumb_query = urlencode({"t": thumb_ref, **({"f": token} if c.thumb_url else {})})
        out.append(
            CandidateOut(
                token=token,
                thumb=f"/api/servers/{server_id}/artwork/image?{thumb_query}",
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
    f: Annotated[
        str | None, Query(max_length=4096, description="Full image, if `t` fails.")
    ] = None,
) -> RawResponse:
    """A candidate's thumbnail, fetched by the server (only signed references). Search
    engines' thumbnail hosts sometimes refuse; then the full image is used instead."""
    us = await owner_server(db, user, server_id)
    snapped = min(THUMB_SIZES, key=lambda s: abs(s - size))
    client = server_client(us, cipher, settings, http)
    error: Exception | None = None
    for token in (t, f):
        if not token:
            continue
        try:
            ref = art.unsign_ref(settings, token)
            data = await art.ref_bytes(http, client, ref, snapped)
            body, content_type = images.thumbnail(data, snapped)
            break
        except (art.ArtworkError, ImageError) as exc:
            log.info("candidate image failed: %s", exc)
            error = exc
    else:
        message = str(error) if isinstance(error, art.ArtworkError) else "Not an image."
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, message)
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
    await check_text_font(db, body.source)
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
    await check_text_font(db, body.source)
    client = server_client(us, cipher, settings, http)
    try:
        applied = await art.apply(
            db,
            http,
            settings,
            client,
            artist,
            body.source,
            body.transform,
            user.id,
            in_worker=False,  # artist.jpg is written by the worker (it has the music folder)
        )
    except art.ArtworkError as exc:
        raise artwork_error(exc) from exc
    if applied.folder_queued:
        await enqueue_folder_save(artist.server_id, artist.rating_key)
    row = applied.artwork
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
        folder=applied.folder,
        folder_error=applied.folder_error,
        folder_queued=applied.folder_queued,
    )


# --- jobs ----------------------------------------------------------------------------------


def job_out(server_id: int, job: ArtworkJob) -> ArtworkJobOut:
    out = ArtworkJobOut.model_validate(job, from_attributes=True)
    if job.kind == "export" and job.status == "succeeded" and job.result_path:
        out.download = f"/api/servers/{server_id}/artwork/jobs/{job.id}/download"
    return out


@router.get("/jobs", response_model=list[ArtworkJobOut])
async def list_jobs(server_id: int, db: DbDep, user: UserDep) -> list[ArtworkJobOut]:
    """Recent artwork jobs on this server, newest first."""
    us = await owner_server(db, user, server_id)
    jobs = await db.scalars(
        select(ArtworkJob)
        .where(ArtworkJob.server_id == us.server_id)
        .order_by(ArtworkJob.id.desc())
        .limit(10)
    )
    return [job_out(server_id, j) for j in jobs]


async def start_job(
    db: AsyncSession, us: UserServer, kind: str, params: dict[str, object]
) -> ArtworkJob:
    busy = await db.scalar(
        select(ArtworkJob.id).where(
            ArtworkJob.server_id == us.server_id, ArtworkJob.status.in_(ACTIVE)
        )
    )
    if busy is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Another artwork job is running.")
    job = ArtworkJob(
        server_id=us.server_id,
        user_server_id=us.id,
        kind=kind,
        status="queued",
        params=params,
        progress={},
        stats={},
    )
    db.add(job)
    await db.commit()
    await enqueue_artwork_job(job)
    return job


@router.post("/jobs/fanart", response_model=ArtworkJobOut, status_code=status.HTTP_201_CREATED)
async def start_fanart_job(
    server_id: int, body: FanartJobIn, db: DbDep, user: UserDep
) -> ArtworkJobOut:
    """Apply the most-liked fanart.tv logo to the given artists (or all with a status)."""
    us = await owner_server(db, user, server_id)
    params: dict[str, object] = {"status": body.status, "transform": Transform().model_dump()}
    if body.rating_keys:
        params["rating_keys"] = body.rating_keys
    return job_out(server_id, await start_job(db, us, "fanart", params))


# --- saving to artist folders --------------------------------------------------------------


async def folder_settings(db: AsyncSession, user: User, us: UserServer) -> FolderSettingsOut:
    """The mapping, and the worker's latest check of it (only the worker sees the music)."""
    server = await db.get(PlexServer, us.server_id)
    assert server is not None
    sample_paths = [
        p
        for p in await db.scalars(
            select(Track.file_path)
            .where(Track.server_id == us.server_id, Track.file_path.is_not(None))
            .limit(500)
        )
        if p
    ]
    plex_root = os.path.commonpath(sample_paths) if sample_paths else None
    check = server.folder_check or {}
    example = check.get("example")
    checking = bool(check.get("requested_at")) and (
        not check.get("checked_at") or check["checked_at"] < check["requested_at"]
    )
    return FolderSettingsOut(
        enabled=server.folder_save,
        plex_prefix=server.plex_path_prefix,
        local_prefix=server.local_path_prefix,
        plex_root=plex_root or None,
        example=FolderExample(**example) if example and not checking else None,
        checking=checking,
        problem=None if checking else check.get("problem"),
    )


@router.get("/folder", response_model=FolderSettingsOut)
async def get_folder(server_id: int, db: DbDep, user: UserDep) -> FolderSettingsOut:
    us = await owner_server(db, user, server_id)
    return await folder_settings(db, user, us)


@router.put("/folder", response_model=FolderSettingsOut)
async def put_folder(
    server_id: int, body: FolderSettingsIn, db: DbDep, user: UserDep
) -> FolderSettingsOut:
    """The music folder mapping (Plex's path -> the worker's), and whether artist.jpg is
    saved into artist folders."""
    us = await owner_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    assert server is not None
    plex_prefix = (body.plex_prefix or "").strip().rstrip("/") or None
    local_prefix = (body.local_prefix or "").strip().rstrip("/") or None
    if body.enabled and (not plex_prefix or not local_prefix):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Enter both the Plex path and the local path.",
        )
    if bool(plex_prefix) != bool(local_prefix):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter both paths (or neither).")
    if (
        plex_prefix
        and local_prefix
        and not (plex_prefix.startswith("/") and local_prefix.startswith("/"))
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use absolute paths.")
    server.folder_save = body.enabled
    server.plex_path_prefix = plex_prefix
    server.local_path_prefix = local_prefix
    mapped = bool(plex_prefix and local_prefix)
    if mapped:
        # The worker (which has the music folder) checks the mapping.
        server.folder_check = {"requested_at": dt.datetime.now(dt.UTC).isoformat()}
    else:
        server.folder_check = {}
    await db.commit()
    if mapped:
        await enqueue_folder_check(server.id)
    return await folder_settings(db, user, us)


# --- logo packs: export and import ---------------------------------------------------------


@router.post("/jobs/export", response_model=ArtworkJobOut, status_code=status.HTTP_201_CREATED)
async def start_export(server_id: int, db: DbDep, user: UserDep) -> ArtworkJobOut:
    """Zip the images of every artist marked Done or Custom (with a manifest)."""
    us = await owner_server(db, user, server_id)
    return job_out(server_id, await start_job(db, us, "export", {}))


@router.get(
    "/jobs/{job_id}/download",
    response_class=FileResponse,
    responses={200: {"content": {"application/zip": {}}}},
)
async def download_export(server_id: int, job_id: int, db: DbDep, user: UserDep) -> FileResponse:
    us = await owner_server(db, user, server_id)
    job = await db.get(ArtworkJob, job_id)
    if job is None or job.server_id != us.server_id or job.kind != "export" or not job.result_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")
    path = Path(job.result_path)
    if not await asyncio.to_thread(path.exists):
        raise HTTPException(status.HTTP_410_GONE, "This export has expired; export again.")
    stamp = (job.finished_at or job.created_at).strftime("%Y-%m-%d")
    return FileResponse(
        path, media_type="application/zip", filename=f"plekstender-artwork-{stamp}.zip"
    )


def _import_image(server_id: int, import_id: str, index: int) -> str:
    return f"/api/servers/{server_id}/artwork/imports/{import_id}/image/{index}"


@router.post(
    "/imports",
    response_model=ImportPreviewOut,
    dependencies=[rate_limit("artwork-import", limit=10, window=60)],
    openapi_extra={"requestBody": {"content": {"application/zip": {}}, "required": True}},
)
async def upload_pack(
    server_id: int, request: Request, db: DbDep, user: UserDep, settings: SettingsDep
) -> ImportPreviewOut:
    """Upload a logo pack (the zip as the request body) and see how it matches."""
    us = await owner_server(db, user, server_id)
    folder = packs.imports_dir(settings)
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    import_id = __import__("uuid").uuid4().hex
    path = folder / f"{import_id}.zip"
    size = 0
    try:
        with path.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > packs.MAX_PACK:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE, "Packs can be up to 500 MB."
                    )
                out.write(chunk)
        entries = await asyncio.to_thread(packs.read_manifest, path)
    except packs.PackError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    matches = await packs.match_entries(db, us, entries)
    return ImportPreviewOut(
        import_id=import_id,
        entries=[
            ImportEntryOut(
                index=m.entry.index,
                artist_name=m.entry.artist_name,
                mbid=m.entry.mbid,
                image=_import_image(server_id, import_id, m.entry.index),
                match=(
                    ImportMatchOut(
                        rating_key=m.artist.rating_key,
                        title=m.artist.title,
                        thumb=m.artist.thumb,
                        status=m.status,
                    )
                    if m.artist
                    else None
                ),
                matched_by=m.matched_by,
            )
            for m in matches
        ],
        matched=sum(1 for m in matches if m.artist),
        total=len(matches),
    )


@router.get(
    "/imports/{import_id}/image/{index}",
    response_class=RawResponse,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}},
)
async def pack_image(
    server_id: int,
    import_id: str,
    index: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    size: int = 160,
) -> RawResponse:
    await owner_server(db, user, server_id)
    try:
        path = packs.pack_path(settings, import_id)
        entries = {e.index: e for e in await asyncio.to_thread(packs.read_manifest, path)}
        data = await asyncio.to_thread(packs.read_image, path, entries[index].file)
        body, content_type = images.thumbnail(data, min(THUMB_SIZES, key=lambda s: abs(s - size)))
    except (packs.PackError, KeyError, ImageError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found") from exc
    return RawResponse(
        body, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"}
    )


@router.post(
    "/imports/{import_id}/apply", response_model=ArtworkJobOut, status_code=status.HTTP_201_CREATED
)
async def apply_pack(
    server_id: int,
    import_id: str,
    body: ImportApplyIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
) -> ArtworkJobOut:
    """Apply the chosen pack images (entry index -> artist) in a background job."""
    us = await owner_server(db, user, server_id)
    try:
        packs.pack_path(settings, import_id)
    except packs.PackError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    params: dict[str, object] = {
        "import_id": import_id,
        "items": [i.model_dump() for i in body.items],
    }
    return job_out(server_id, await start_job(db, us, "import", params))
