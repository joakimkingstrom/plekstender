"""Library → Artwork → Album covers (server owners): the albums whose covers most need
replacing (none, or low resolution), better covers from open sources and web search, and
saving one to Plex for everyone using the server."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel
from sqlalchemy import and_, case, false, func, or_, select

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.artwork import candidates_out, job_out, owner_server, start_job
from plekstender.api.routes.auth import load_preferences
from plekstender.api.routes.library import _like, visible
from plekstender.api.routes.servers import server_client
from plekstender.api.routes.statuses import searxng_healthy
from plekstender.api.schemas import (
    LOW_RESOLUTIONS,
    AlbumBestOut,
    AlbumCoverFilter,
    AlbumCoverKeepIn,
    AlbumCoverOut,
    AlbumCoverPage,
    AlbumCoverSort,
    ArtworkJobOut,
    BestCoversJobIn,
    CandidatesOut,
)
from plekstender.artwork import covers
from plekstender.artwork import service as art
from plekstender.artwork.covers import LOW, AlbumRef, CoverError, CoverSource
from plekstender.artwork.fetch import FetchError
from plekstender.artwork.sources import Candidate, SourceError
from plekstender.db.instance import (
    get_cover_sources_off,
    get_fanart_api_key,
    get_tidal_client,
)
from plekstender.db.models import Album, AlbumCover, Artist, User, UserServer
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError
from plekstender.worker import enqueue_cover_sizes

router = APIRouter(prefix="/api/servers/{server_id}/artwork/albums", tags=["artwork"])


async def _best_sources(db: DbDep, wanted: Sequence[str]) -> list[str]:
    """The sources a best match may come from: those wanted that an admin hasn't turned off."""
    off = await get_cover_sources_off(db)
    return [s for s in wanted if s not in off]


class CoverRenderIn(BaseModel):
    source: art.SourceSpec
    square: bool = False


def _columns(low: int = LOW) -> tuple[Any, Any, Any]:
    """The cover's state, as SQL: missing, low (under `low` px on its shorter side), ok or
    unchecked."""
    cover = AlbumCover
    stale = or_(cover.thumb.is_(None), cover.thumb != Album.thumb, cover.checked_at.is_(None))
    shorter = func.least(cover.width, cover.height)
    state = case(
        (Album.thumb.is_(None), "missing"),
        (stale, "unchecked"),
        (cover.width.is_(None), "missing"),  # there, but not an image anyone can read
        (shorter < low, "low"),
        else_="ok",
    )
    kept = func.coalesce(cover.kept, false())
    return state, kept, shorter


def _check_low(low_px: int) -> None:
    if low_px not in LOW_RESOLUTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "low_px is 600, 1000 or 1500.")


def _shown(show: str, q: str | None, state: Any, kept: Any) -> list[Any]:
    """The conditions for a filter and a search (as the list shows them)."""
    where: list[Any] = []
    if show == "needs":
        where += [state.in_(("missing", "low")), ~kept]
    elif show in ("missing", "low"):
        where += [state == show, ~kept]
    if q:
        like = _like(q)
        where.append(
            or_(Album.title.ilike(like, escape="\\"), Artist.title.ilike(like, escape="\\"))
        )
    return where


async def _album(
    db: DbDep, user: User, us: UserServer, rating_key: int
) -> tuple[Album, str | None]:
    state, cond, where = visible(Album, user.id, us)
    row = (
        await db.execute(
            select(Album, Artist.title)
            .join(state, cond)
            .outerjoin(
                Artist,
                and_(
                    Artist.server_id == Album.server_id,
                    Artist.rating_key == Album.artist_rating_key,
                ),
            )
            .where(*where, Album.rating_key == rating_key)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Album not found")
    return row[0], row[1]


@router.get("", response_model=AlbumCoverPage)
async def list_covers(
    server_id: int,
    db: DbDep,
    user: UserDep,
    show: AlbumCoverFilter = "needs",
    q: Annotated[str | None, Query(max_length=200)] = None,
    low_px: int = LOW,
    sort: AlbumCoverSort | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> AlbumCoverPage:
    """Albums and their covers: by default those that most need a better one (no cover
    first, then the smallest), leaving out ones marked as fine. Low resolution is under
    `low_px` (600, 1000 or 1500) on the shorter side; `sort` orders them otherwise."""
    _check_low(low_px)
    us = await owner_server(db, user, server_id)
    vis, cond, where = visible(Album, user.id, us)
    cover_state, kept, shorter = _columns(low_px)
    base = (
        select(Album, Artist.title, AlbumCover, cover_state.label("state"), kept.label("kept"))
        .join(vis, cond)
        .outerjoin(
            Artist,
            and_(Artist.server_id == Album.server_id, Artist.rating_key == Album.artist_rating_key),
        )
        .outerjoin(
            AlbumCover,
            and_(
                AlbumCover.server_id == Album.server_id, AlbumCover.rating_key == Album.rating_key
            ),
        )
        .where(*where)
    )
    tally = (
        await db.execute(
            select(cover_state, kept, func.count())
            .select_from(Album)
            .join(vis, cond)
            .outerjoin(
                AlbumCover,
                and_(
                    AlbumCover.server_id == Album.server_id,
                    AlbumCover.rating_key == Album.rating_key,
                ),
            )
            .where(*where)
            .group_by(cover_state, kept)
        )
    ).all()
    counts = {"missing": 0, "low": 0, "ok": 0, "unchecked": 0, "kept": 0}
    for state_name, is_kept, n in tally:
        counts["kept" if is_kept and state_name in ("missing", "low") else state_name] += n
    if counts["unchecked"]:
        await enqueue_cover_sizes()  # measure them now rather than at the next sweep

    query = base.where(*_shown(show, q, cover_state, kept))
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    by_name = (
        func.lower(func.coalesce(Artist.title_sort, Artist.title, "")),
        func.lower(func.coalesce(Album.title_sort, Album.title)),
        Album.rating_key,
    )
    smallest = (case((cover_state == "missing", 0), else_=1), shorter.asc().nulls_first())
    sort = sort or ("name" if show == "all" else "smallest")
    order = (
        by_name
        if sort == "name"
        else (Album.added_at.desc().nulls_last(), Album.rating_key.desc())
        if sort == "added"
        else (*smallest, *by_name)
    )
    rows = await db.execute(query.order_by(*order).offset(offset).limit(limit))
    return AlbumCoverPage(
        items=[
            AlbumCoverOut(
                rating_key=album.rating_key,
                title=album.title,
                artist=artist,
                year=album.year,
                thumb=album.thumb,
                width=cover.width if cover and state_name != "unchecked" else None,
                height=cover.height if cover and state_name != "unchecked" else None,
                state=state_name,
                kept=bool(is_kept),
                source=cover.source if cover else None,
                applied_at=cover.applied_at if cover else None,
                added_at=album.added_at,
            )
            for album, artist, cover, state_name, is_kept in rows.all()
        ],
        total=total,
        counts=counts,
    )


async def _cover_row(db: DbDep, album: Album) -> AlbumCover:
    return await covers.cover_row(db, album)


def _cover_out(album: Album, artist: str | None, row: AlbumCover) -> AlbumCoverOut:
    size = (row.width or 0, row.height or 0)
    return AlbumCoverOut(
        rating_key=album.rating_key,
        title=album.title,
        artist=artist,
        year=album.year,
        thumb=album.thumb,
        width=row.width,
        height=row.height,
        state="missing" if not row.width else "low" if min(size) < LOW else "ok",
        kept=bool(row.kept),
        source=row.source,
        applied_at=row.applied_at,
    )


class CoverSourcesOut(BaseModel):
    sources: list[CoverSource]


@router.get("/sources", response_model=CoverSourcesOut)
async def cover_sources(
    server_id: int, db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep
) -> CoverSourcesOut:
    """The sources that can be used here: the open ones unless an admin turned them off;
    Tidal and fanart.tv once an admin has set them up (checked when saved); web search
    while SearXNG answers."""
    await owner_server(db, user, server_id)
    off = await get_cover_sources_off(db)
    found: list[CoverSource] = [s for s in ("deezer", "itunes", "coverartarchive") if s not in off]
    if await get_tidal_client(db, settings) is not None:
        found.append("tidal")
    if (await get_fanart_api_key(db, settings))[0]:
        found.append("fanart")
    found.append("plex")
    if settings.searxng_url and await searxng_healthy(http, settings.searxng_url):
        found.append("search")
    return CoverSourcesOut(sources=found)


NO_MBID = "MusicBrainz doesn't know this album by its name and artist, so {} can't be searched."


@router.get("/{rating_key}/candidates", response_model=CandidatesOut)
async def candidates(
    server_id: int,
    rating_key: int,
    source: CoverSource,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
    q: Annotated[str | None, Query(max_length=300, description="Web search: own words.")] = None,
) -> CandidatesOut:
    """Covers for the album from one source (with a note when there are none)."""
    us = await owner_server(db, user, server_id)
    if source in await get_cover_sources_off(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "An admin has turned this source off.")
    album, artist = await _album(db, user, us, rating_key)
    ref = AlbumRef(album.title, artist, album.mbid)
    found: list[Candidate] = []
    note: str | None = None
    try:
        if source in ("coverartarchive", "fanart"):
            name = "the Cover Art Archive" if source == "coverartarchive" else "fanart.tv"
            api_key = None
            if source == "fanart":
                api_key, _ = await get_fanart_api_key(db, settings)
                if not api_key:
                    return CandidatesOut(
                        candidates=[],
                        note="fanart.tv isn't set up: an admin can add an API key in Settings.",
                    )
            cover = await _cover_row(db, album)
            mbids = [m for m in (album.mbid,) if m]
            if not mbids or source == "fanart":  # fanart.tv only knows release groups
                if found_group := await covers.release_group(db, http, settings, cover, ref):
                    mbids.append(found_group)
            await db.commit()
            if not mbids:
                note = NO_MBID.format(name)
            elif source == "coverartarchive":
                found = await covers.cover_art_archive(http, settings.coverartarchive_url, mbids)
                note = None if found else "The Cover Art Archive has no covers for this album yet."
            else:
                assert api_key
                found = await covers.fanart_covers(http, settings.fanart_api_url, api_key, mbids)
                note = None if found else "fanart.tv has no covers for this album yet."
            await covers.measure_candidates(http, found)  # neither says how big they are
        elif source == "deezer":
            found = await covers.deezer(http, settings.deezer_api_url, ref)
            note = None if found else "Deezer found nothing by that name."
        elif source == "itunes":
            found = await covers.itunes(http, settings.itunes_api_url, ref)
            note = None if found else "iTunes found nothing by that name."
            # Asked for at 3000 px, Apple sends its original when that's smaller: how big.
            await covers.measure_candidates(http, found, limit=15)
        elif source == "tidal":
            client = await get_tidal_client(db, settings)
            if client is None:
                note = (
                    "Tidal isn't set up: an admin can add a Tidal client ID and secret in "
                    "Settings → Services → Artwork."
                )
            else:
                found = await covers.tidal(http, settings, client, ref)
                note = None if found else "Tidal found nothing by that name."
        elif source == "plex":
            try:
                posters = await server_client(us, cipher, settings, http).posters(rating_key)
            except PlexError as exc:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, f"Plex didn't list them: {exc}"
                ) from exc
            found = [
                Candidate(
                    url=f"plex:{p['key']}",
                    label=("Current · " if p.get("selected") else "")
                    + str(p.get("provider") or "Plex"),
                )
                for p in posters
                if isinstance(p.get("key"), str) and p["key"]
            ]
            note = None if found else "Plex has no other covers for this album."
        else:
            if not settings.searxng_url:
                note = "Image search isn't set up on this instance (SEARXNG_URL)."
            else:
                words = (q or "").strip() or f"{artist or ''} {album.title} album cover".strip()
                found = await art.cached_search(db, http, settings.searxng_url, words, "cover")
    except (SourceError, art.ArtworkError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return candidates_out(settings, server_id, found, note)


async def _render(
    body: CoverRenderIn,
    album: Album,
    settings: SettingsDep,
    http: HttpDep,
    client: PlexServerClient,
) -> tuple[bytes, tuple[int, int], str, str | None]:
    """The cover as it would be saved: (JPEG, size, source, address)."""
    spec = body.source
    if spec.kind == "text":
        raise CoverError("Text images are for artists.")
    if spec.kind == "upload":
        path = art.uploads_dir(settings) / f"{spec.upload_id}.png"
        if not path.exists():
            raise CoverError("The upload has expired; upload the image again.")
        data, source, url = path.read_bytes(), "upload", None
    elif spec.kind == "current":
        if not album.thumb:
            raise CoverError("This album has no cover in Plex yet.")
        data, source, url = (
            await covers.image_bytes(http, client, f"plex:{album.thumb}"),
            "plex",
            None,
        )
    else:
        try:
            ref = art.unsign_ref(settings, spec.token or "")
        except art.ArtworkError as exc:
            raise CoverError(str(exc)) from exc
        data = await covers.image_bytes(http, client, ref)
        source = covers.source_of(ref)
        url = None if ref.startswith("plex:") else ref
    jpeg, size = covers.cover_jpeg(data, body.square)
    return jpeg, size, source, url


@router.post(
    "/{rating_key}/preview",
    response_class=RawResponse,
    responses={200: {"content": {"image/jpeg": {}}}},
    dependencies=[rate_limit("cover-preview", limit=120, window=60)],
)
async def preview(
    server_id: int,
    rating_key: int,
    body: CoverRenderIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> RawResponse:
    """The cover as it would be saved (its size in X-Image-Width and X-Image-Height)."""
    us = await owner_server(db, user, server_id)
    album, _ = await _album(db, user, us, rating_key)
    try:
        jpeg, (w, h), _, _ = await _render(
            body, album, settings, http, server_client(us, cipher, settings, http)
        )
    except CoverError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return RawResponse(
        jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Image-Width": str(w),
            "X-Image-Height": str(h),
            "Access-Control-Expose-Headers": "X-Image-Width, X-Image-Height",
        },
    )


@router.post("/{rating_key}/apply", response_model=AlbumCoverOut)
async def apply(
    server_id: int,
    rating_key: int,
    body: CoverRenderIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> AlbumCoverOut:
    """Make the cover the album's poster in Plex."""
    us = await owner_server(db, user, server_id)
    album, artist = await _album(db, user, us, rating_key)
    client = server_client(us, cipher, settings, http)
    try:
        jpeg, size, source, url = await _render(body, album, settings, http, client)
        row = await covers.apply(db, client, album, jpeg, size, source, url, user.id)
    except CoverError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _cover_out(album, artist, row)


@router.post(
    "/{rating_key}/best",
    response_model=AlbumBestOut,
    dependencies=[rate_limit("cover-best", limit=20, window=60)],
)
async def apply_best(
    server_id: int,
    rating_key: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> AlbumBestOut:
    """The best match saved to Plex: the biggest cover of this very album (same title and
    artist) on Deezer, iTunes, Tidal and the Cover Art Archive, at least 1000 x 1000."""
    us = await owner_server(db, user, server_id)
    album, artist = await _album(db, user, us, rating_key)
    try:
        result = await covers.apply_best(
            db, http, settings, server_client(us, cipher, settings, http),
            await get_tidal_client(db, settings), album, artist, user.id,
            await _best_sources(db, load_preferences(user).best_cover_sources),
        )  # fmt: skip
    except (CoverError, FetchError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await db.commit()
    match = result.match
    return AlbumBestOut(
        outcome=result.outcome,
        cover=_cover_out(album, artist, result.cover) if result.cover else None,
        source=match.source if match else None,
        width=match.size[0] if match else None,
        height=match.size[1] if match else None,
        problems=result.problems,
    )


@router.post("/best", response_model=ArtworkJobOut, status_code=status.HTTP_201_CREATED)
async def start_best(
    server_id: int, body: BestCoversJobIn, db: DbDep, user: UserDep
) -> ArtworkJobOut:
    """Apply the best match to every album the list shows with this filter and search (in
    the background, a few seconds an album)."""
    _check_low(body.low_px)
    us = await owner_server(db, user, server_id)
    sources = await _best_sources(db, body.sources or load_preferences(user).best_cover_sources)
    if not sources:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Every source chosen has been turned off."
        )
    vis, cond, where = visible(Album, user.id, us)
    cover_state, kept, shorter = _columns(body.low_px)
    keys = list(
        await db.scalars(
            select(Album.rating_key)
            .join(vis, cond)
            .outerjoin(
                Artist,
                and_(
                    Artist.server_id == Album.server_id,
                    Artist.rating_key == Album.artist_rating_key,
                ),
            )
            .outerjoin(
                AlbumCover,
                and_(
                    AlbumCover.server_id == Album.server_id,
                    AlbumCover.rating_key == Album.rating_key,
                ),
            )
            .where(*where, *_shown(body.show, body.q, cover_state, kept))
            .order_by(
                case((cover_state == "missing", 0), else_=1),
                shorter.asc().nulls_first(),
                Album.rating_key,
            )
        )
    )
    if not keys:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No albums to look for.")
    job = await start_job(
        db, us, "covers", {"rating_keys": keys, "show": body.show, "sources": list(sources)}
    )
    return job_out(server_id, job)


@router.put("/{rating_key}/keep", status_code=status.HTTP_204_NO_CONTENT)
async def keep(
    server_id: int, rating_key: int, body: AlbumCoverKeepIn, db: DbDep, user: UserDep
) -> Response:
    """Mark the cover as fine as it is (or not): kept ones leave the lists to improve."""
    us = await owner_server(db, user, server_id)
    album, _ = await _album(db, user, us, rating_key)
    row = await _cover_row(db, album)
    row.kept = body.kept
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
