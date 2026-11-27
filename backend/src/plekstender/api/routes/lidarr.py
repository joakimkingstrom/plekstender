"""Lidarr: connecting it (admins) and adding albums and artists to it (see plekstender.lidarr)."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from plekstender import lidarr
from plekstender.api.deps import AdminDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import (
    LidarrAdminOut,
    LidarrAlbumIn,
    LidarrAlbumOut,
    LidarrAlbumState,
    LidarrArtistAddedOut,
    LidarrArtistIn,
    LidarrArtistOut,
    LidarrCheckIn,
    LidarrFolderOut,
    LidarrGrabIn,
    LidarrIn,
    LidarrOptionsOut,
    LidarrProfileOut,
    LidarrReleaseOut,
    LidarrStatesIn,
    LidarrStatusOut,
)

router = APIRouter(prefix="/api/lidarr", tags=["lidarr"])
admin_router = APIRouter(prefix="/api/admin/lidarr", tags=["admin"])


def _bad_gateway(exc: lidarr.LidarrError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


# --- admins -------------------------------------------------------------------------------


@admin_router.get("", response_model=LidarrAdminOut)
async def get_lidarr(db: DbDep, _: AdminDep, settings: SettingsDep) -> LidarrAdminOut:
    found = await lidarr.load(db, settings)
    if found is None:
        return LidarrAdminOut(configured=False)
    return LidarrAdminOut(configured=True, **found[0].model_dump())


async def _api_key(db: DbDep, settings: SettingsDep, given: str | None) -> str:
    if given and given.strip():
        return given.strip()
    found = await lidarr.load(db, settings)
    if found is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Lidarr's API key is needed.")
    return found[1]


@admin_router.post(
    "/check",
    response_model=LidarrOptionsOut,
    dependencies=[rate_limit("lidarr-check", limit=20, window=60)],
)
async def check_lidarr(
    body: LidarrCheckIn, db: DbDep, _: AdminDep, settings: SettingsDep, http: HttpDep
) -> LidarrOptionsOut:
    """Try the address and API key (the saved key when none is given), and say what that
    Lidarr offers: its root folders and quality and metadata profiles."""
    try:
        client = lidarr.LidarrClient(http, body.url, await _api_key(db, settings, body.api_key))
        found = await client.status()
        folders = await client.root_folders()
        quality = await client.quality_profiles()
        metadata = await client.metadata_profiles()
    except lidarr.LidarrError as exc:
        raise _bad_gateway(exc) from exc
    return LidarrOptionsOut(
        version=str(found.get("version")),
        root_folders=[
            LidarrFolderOut(path=str(f["path"]), free_space=f.get("freeSpace"))
            for f in folders
            if f.get("path")
        ],
        quality_profiles=[
            LidarrProfileOut(id=int(p["id"]), name=str(p.get("name") or p["id"])) for p in quality
        ],
        metadata_profiles=[
            LidarrProfileOut(id=int(p["id"]), name=str(p.get("name") or p["id"])) for p in metadata
        ],
    )


@admin_router.put("", response_model=LidarrAdminOut)
async def put_lidarr(
    body: LidarrIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> LidarrAdminOut:
    """Connect Lidarr (checked first: the folder and profiles must be ones it has)."""
    options = await check_lidarr(
        LidarrCheckIn(url=body.url, api_key=body.api_key), db, admin, settings, http
    )
    if body.root_folder not in {f.path for f in options.root_folders}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Lidarr has no such root folder."
        )
    if body.quality_profile_id not in {p.id for p in options.quality_profiles}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Lidarr has no such quality profile."
        )
    if body.metadata_profile_id not in {p.id for p in options.metadata_profiles}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Lidarr has no such metadata profile."
        )
    value = lidarr.LidarrSettings(
        url=lidarr.clean_url(body.url),
        root_folder=body.root_folder,
        quality_profile_id=body.quality_profile_id,
        metadata_profile_id=body.metadata_profile_id,
        access=body.access,
    )
    await lidarr.save(db, settings, value, await _api_key(db, settings, body.api_key))
    await db.commit()
    return await get_lidarr(db, admin, settings)


@admin_router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lidarr(db: DbDep, _: AdminDep, settings: SettingsDep) -> Response:
    """Disconnect Lidarr (the API key is forgotten)."""
    await lidarr.save(db, settings, None, None)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- everyone ------------------------------------------------------------------------------


@router.get("", response_model=LidarrStatusOut)
async def lidarr_status(db: DbDep, user: UserDep, settings: SettingsDep) -> LidarrStatusOut:
    """Whether Lidarr's buttons show for you (connected, and admins let you)."""
    found = await lidarr.load(db, settings)
    return LidarrStatusOut(available=found is not None and lidarr.may_use(found[0], user.is_admin))


async def _user_lidarr(
    db: DbDep, user: UserDep, settings: SettingsDep, http: HttpDep
) -> lidarr.Lidarr:
    """The connected Lidarr, for someone who may use it."""
    found = await lidarr.connected(db, settings, http)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lidarr isn't connected.")
    if not lidarr.may_use(found.settings, user.is_admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can add to Lidarr here.")
    return found


LidarrDep = Annotated[lidarr.Lidarr, Depends(_user_lidarr)]
AT_ONCE = 8  # albums asked about at a time


@router.post("/albums/states", response_model=dict[str, LidarrAlbumState])
async def album_states(body: LidarrStatesIn, found: LidarrDep) -> dict[str, LidarrAlbumState]:
    """Whether each album (release group MBID) is in Lidarr: absent, monitored, not
    monitored, or downloaded."""
    gate = asyncio.Semaphore(AT_ONCE)

    async def one(mbid: str) -> LidarrAlbumState:
        async with gate:
            return lidarr.album_state(await found.client.album(mbid))

    mbids = list(dict.fromkeys(body.mbids))
    try:
        states = await asyncio.gather(*(one(m) for m in mbids))
    except lidarr.LidarrError as exc:
        raise _bad_gateway(exc) from exc
    return dict(zip(mbids, states, strict=True))


@router.post(
    "/albums",
    response_model=LidarrAlbumOut,
    dependencies=[rate_limit("lidarr-add", limit=60, window=60)],
)
async def add_album(body: LidarrAlbumIn, found: LidarrDep) -> LidarrAlbumOut:
    """Add the album to Lidarr, monitored (its artist too if new, not monitored for anything
    else); already there, it's monitored. With `search`, Lidarr looks for it right away."""
    client = found.client
    try:
        album = await client.album(body.mbid)
        if album is None:
            looked_up = await client.lookup_album(body.mbid)
            if looked_up is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "Lidarr's metadata doesn't know this album yet."
                )
            album = await client.add_album(looked_up, found.settings, search=body.search)
        else:
            if not album.get("monitored"):
                await client.monitor(int(album["id"]))
                album = {**album, "monitored": True}
            if body.search:
                await client.search(int(album["id"]))
    except lidarr.LidarrError as exc:
        raise _bad_gateway(exc) from exc
    return LidarrAlbumOut(
        id=int(album["id"]), title=str(album.get("title") or ""), state=lidarr.album_state(album)
    )


def _release_out(r: dict[str, Any]) -> LidarrReleaseOut:
    quality = ((r.get("quality") or {}).get("quality") or {}).get("name")
    return LidarrReleaseOut(
        guid=str(r.get("guid")),
        indexer_id=int(r.get("indexerId") or 0),
        indexer=r.get("indexer"),
        title=str(r.get("title") or ""),
        size=r.get("size"),
        quality=quality,
        protocol=r.get("protocol"),
        seeders=r.get("seeders"),
        leechers=r.get("leechers"),
        age_days=lidarr.release_age_days(r),
        approved=bool(r.get("approved", not r.get("rejected"))),
        rejections=[str(x) for x in r.get("rejections") or []][:5],
    )


@router.get(
    "/albums/{album_id}/releases",
    response_model=list[LidarrReleaseOut],
    dependencies=[rate_limit("lidarr-releases", limit=20, window=60)],
)
async def album_releases(album_id: int, found: LidarrDep) -> list[LidarrReleaseOut]:
    """What Lidarr's indexers have for an album in Lidarr, the ones it would take first,
    then by seeders (slow: Lidarr asks them all)."""
    try:
        found_releases = await found.client.releases(album_id)
    except lidarr.LidarrError as exc:
        raise _bad_gateway(exc) from exc
    out = [_release_out(r) for r in found_releases if r.get("guid")]
    out.sort(key=lambda r: (not r.approved, -(r.seeders or 0), -(r.size or 0)))
    return out


@router.post(
    "/releases",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[rate_limit("lidarr-grab", limit=30, window=60)],
)
async def grab_release(body: LidarrGrabIn, found: LidarrDep) -> Response:
    """Have Lidarr download one of the releases found."""
    try:
        await found.client.grab(body.guid, body.indexer_id)
    except lidarr.LidarrError as exc:
        raise _bad_gateway(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


LOOKUP_SHOWN = 6
OVERVIEW = 300  # characters of the biography shown


def _artist_out(a: dict[str, Any]) -> LidarrArtistOut:
    overview = " ".join(str(a.get("overview") or "").split())
    if len(overview) > OVERVIEW:
        overview = overview[:OVERVIEW].rsplit(" ", 1)[0] + "…"
    return LidarrArtistOut(
        mbid=str(a["foreignArtistId"]),
        name=str(a.get("artistName") or ""),
        disambiguation=a.get("disambiguation") or None,
        kind=a.get("artistType") or None,
        overview=overview or None,
        in_lidarr=bool(a.get("id")),
    )


@router.get(
    "/artists/lookup",
    response_model=list[LidarrArtistOut],
    dependencies=[rate_limit("lidarr-lookup", limit=30, window=60)],
)
async def lookup_artists(
    found: LidarrDep, name: Annotated[str, Query(min_length=1, max_length=200)]
) -> list[LidarrArtistOut]:
    """Artists by this name as Lidarr's metadata knows them (to pick the right one), the
    closest first; those already in Lidarr are said so."""
    try:
        results = await found.client.lookup_artists(name.strip())
    except lidarr.LidarrError as exc:
        raise _bad_gateway(exc) from exc
    return [_artist_out(a) for a in results if a.get("foreignArtistId")][:LOOKUP_SHOWN]


@router.post(
    "/artists",
    response_model=LidarrArtistAddedOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("lidarr-add", limit=60, window=60)],
)
async def add_artist(body: LidarrArtistIn, found: LidarrDep) -> LidarrArtistAddedOut:
    """Add an artist to Lidarr (in the admin's root folder and profiles), monitoring the
    albums asked for, and with `search`, looking for them now."""
    client = found.client
    try:
        if await client.artist(body.mbid) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "That artist is in Lidarr already.")
        results = await client.lookup_artists(f"lidarr:{body.mbid}")
        looked_up = next((a for a in results if a.get("foreignArtistId") == body.mbid), None)
        if looked_up is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Lidarr's metadata doesn't know them.")
        added = await client.add_artist(
            looked_up, found.settings, monitor=body.monitor, search=body.search
        )
    except lidarr.LidarrError as exc:
        raise _bad_gateway(exc) from exc
    return LidarrArtistAddedOut(id=int(added["id"]), name=str(added.get("artistName") or ""))
