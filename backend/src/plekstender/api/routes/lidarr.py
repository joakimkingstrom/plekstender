"""Lidarr: connecting it (admins) and adding albums and artists to it (see plekstender.lidarr)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from plekstender import lidarr
from plekstender.api.deps import AdminDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.schemas import (
    LidarrAdminOut,
    LidarrCheckIn,
    LidarrFolderOut,
    LidarrIn,
    LidarrOptionsOut,
    LidarrProfileOut,
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
