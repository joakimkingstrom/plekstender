"""Instance administration: access mode, approved servers, users."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from plekstender import acoustid
from plekstender.api.deps import AdminDep, DbDep, HttpDep, SettingsDep
from plekstender.api.routes.servers import get_user_server
from plekstender.api.schemas import (
    AccessSettingsIn,
    AccessSettingsOut,
    AcoustIdKeyIn,
    AcoustIdKeyOut,
    AdminServerIn,
    AdminServerOut,
    AdminUserIn,
    AdminUserOut,
    CatalogueFont,
    CataloguePage,
    FanartKeyIn,
    FanartKeyOut,
    FontGroup,
    FontGroupName,
    FontIn,
    LastFmKeyIn,
    LastFmKeyOut,
    PopularityPreviewItem,
)
from plekstender.artwork.catalogue import catalogue, find
from plekstender.artwork.sources import SourceError, fanart_logos
from plekstender.artwork.text import FontError, check_font
from plekstender.db.instance import (
    LibrarySettings,
    get_access_settings,
    get_acoustid_api_key,
    get_fanart_api_key,
    get_fonts,
    get_lastfm_api_key,
    get_library_settings,
    set_access_settings,
    set_acoustid_api_key,
    set_fanart_api_key,
    set_fonts,
    set_lastfm_api_key,
    set_library_settings,
)
from plekstender.db.models import PlexServer, User, UserServer
from plekstender.history.lastfm import LastFmClient, LastFmError
from plekstender.sync.popularity import preview_artist_ranking
from plekstender.worker import enqueue_popularity_recompute

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/access", response_model=AccessSettingsOut)
async def get_access(db: DbDep, _: AdminDep, settings: SettingsDep) -> AccessSettingsOut:
    access = await get_access_settings(db)
    return AccessSettingsOut(
        mode=access.mode,
        allowlist=access.allowlist,
        initial_admin_configured=bool(settings.initial_admin),
    )


@router.put("/access", response_model=AccessSettingsOut)
async def put_access(
    body: AccessSettingsIn, db: DbDep, _: AdminDep, settings: SettingsDep
) -> AccessSettingsOut:
    await set_access_settings(db, body.mode, body.allowlist)
    await db.commit()
    return await get_access(db, _, settings)


@router.get("/servers", response_model=list[AdminServerOut])
async def list_all_servers(db: DbDep, _: AdminDep) -> list[AdminServerOut]:
    rows = await db.execute(
        select(PlexServer, func.count(UserServer.id))
        .outerjoin(UserServer)
        .group_by(PlexServer.id)
        .order_by(PlexServer.name)
    )
    return [
        AdminServerOut(
            id=s.id,
            machine_identifier=s.machine_identifier,
            name=s.name,
            approved=s.approved,
            user_count=count,
        )
        for s, count in rows.all()
    ]


@router.put("/servers/{server_id}", response_model=AdminServerOut)
async def update_server(
    server_id: int, body: AdminServerIn, db: DbDep, _: AdminDep
) -> AdminServerOut:
    server = await db.get(PlexServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")
    server.approved = body.approved
    await db.commit()
    count = await db.scalar(
        select(func.count(UserServer.id)).where(UserServer.server_id == server.id)
    )
    return AdminServerOut(
        id=server.id,
        machine_identifier=server.machine_identifier,
        name=server.name,
        approved=server.approved,
        user_count=count or 0,
    )


def _admin_user_out(u: User, server_count: int) -> AdminUserOut:
    return AdminUserOut(
        id=u.id,
        username=u.username,
        email=u.email,
        thumb_url=u.thumb_url,
        is_admin=u.is_admin,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        server_count=server_count,
    )


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: DbDep, _: AdminDep) -> list[AdminUserOut]:
    rows = await db.execute(
        select(User, func.count(UserServer.id))
        .outerjoin(UserServer)
        .group_by(User.id)
        .order_by(User.created_at)
    )
    return [_admin_user_out(u, count) for u, count in rows.all()]


@router.put("/users/{user_id}", response_model=AdminUserOut)
async def update_user(user_id: int, body: AdminUserIn, db: DbDep, admin: AdminDep) -> AdminUserOut:
    if user_id == admin.id and not body.is_admin:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove your own admin role")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.is_admin = body.is_admin
    await db.commit()
    count = await db.scalar(select(func.count(UserServer.id)).where(UserServer.user_id == user.id))
    return _admin_user_out(user, count or 0)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: DbDep, admin: AdminDep) -> None:
    """Removes the user and all their data and sessions. They can sign in again if allowed."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove yourself")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.delete(user)
    await db.commit()


# --- library settings ---------------------------------------------------------------------


@router.get("/library", response_model=LibrarySettings)
async def get_library(db: DbDep, _: AdminDep) -> LibrarySettings:
    return await get_library_settings(db)


@router.put("/library", response_model=LibrarySettings)
async def put_library(body: LibrarySettings, db: DbDep, _: AdminDep) -> LibrarySettings:
    before = await get_library_settings(db)
    await set_library_settings(db, body)
    await db.commit()
    if (before.album_top_n, before.artist_top_n) != (body.album_top_n, body.artist_top_n):
        # Re-scoring every server can take a few seconds; do it in the background.
        await enqueue_popularity_recompute()
    return body


@router.get("/library/preview", response_model=list[PopularityPreviewItem])
async def preview_popularity(
    db: DbDep,
    admin: AdminDep,
    server_id: int,
    artist_top_n: int = Query(ge=1, le=50),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[PopularityPreviewItem]:
    """Your top artists if artist popularity used `artist_top_n`, with their current rank."""
    us = await get_user_server(db, admin, server_id)
    rows = await preview_artist_ranking(
        db,
        user_id=admin.id,
        server_id=us.server_id,
        sections=us.music_section_keys,
        artist_top_n=artist_top_n,
        limit=limit,
    )
    return [PopularityPreviewItem(**row.__dict__) for row in rows]


@router.get("/lastfm", response_model=LastFmKeyOut)
async def get_lastfm(db: DbDep, _: AdminDep, settings: SettingsDep) -> LastFmKeyOut:
    key, source = await get_lastfm_api_key(db, settings)
    return LastFmKeyOut(configured=key is not None, source=source)


@router.put("/lastfm", response_model=LastFmKeyOut)
async def put_lastfm(
    body: LastFmKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> LastFmKeyOut:
    """Set (after checking it with Last.fm) or clear the instance's Last.fm API key."""
    if body.api_key:
        try:
            await LastFmClient(http, body.api_key, settings.lastfm_api_url).check_key()
        except LastFmError as exc:
            code = status.HTTP_502_BAD_GATEWAY if exc.transient else status.HTTP_400_BAD_REQUEST
            raise HTTPException(code, str(exc)) from exc
    await set_lastfm_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_lastfm(db, admin, settings)


# Radiohead: a well-known artist to check a fanart.tv key against.
FANART_CHECK_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


@router.get("/fanart", response_model=FanartKeyOut)
async def get_fanart(db: DbDep, _: AdminDep, settings: SettingsDep) -> FanartKeyOut:
    key, source = await get_fanart_api_key(db, settings)
    return FanartKeyOut(configured=key is not None, source=source)


@router.put("/fanart", response_model=FanartKeyOut)
async def put_fanart(
    body: FanartKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> FanartKeyOut:
    """Set (after checking it with fanart.tv) or clear the instance's fanart.tv API key."""
    if body.api_key:
        try:
            await fanart_logos(http, settings.fanart_api_url, body.api_key, FANART_CHECK_MBID)
        except SourceError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await set_fanart_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_fanart(db, admin, settings)


@router.get("/acoustid", response_model=AcoustIdKeyOut)
async def get_acoustid(db: DbDep, _: AdminDep, settings: SettingsDep) -> AcoustIdKeyOut:
    key, source = await get_acoustid_api_key(db, settings)
    return AcoustIdKeyOut(configured=key is not None, source=source)


@router.put("/acoustid", response_model=AcoustIdKeyOut)
async def put_acoustid(
    body: AcoustIdKeyIn, db: DbDep, admin: AdminDep, settings: SettingsDep, http: HttpDep
) -> AcoustIdKeyOut:
    """Set (after checking it with AcoustID) or clear the instance's AcoustID application key."""
    if body.api_key:
        try:
            await acoustid.check_key(http, settings.acoustid_api_url, body.api_key)
        except acoustid.AcoustIdError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await set_acoustid_api_key(db, settings, body.api_key)
    await db.commit()
    return await get_acoustid(db, admin, settings)


# --- text logo fonts -----------------------------------------------------------------------


async def _font_groups(db: DbDep) -> list[FontGroup]:
    return [FontGroup(name=name, fonts=names) for name, names in (await get_fonts(db)).items()]


@router.get("/fonts/catalogue", response_model=CataloguePage)
async def font_catalogue(
    db: DbDep,
    _: AdminDep,
    http: HttpDep,
    settings: SettingsDep,
    q: Annotated[str, Query(max_length=64)] = "",
    group: FontGroupName | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
) -> CataloguePage:
    """Browse Google Fonts (most used first), to add fonts for text logos."""
    families = await catalogue(http, settings.data_dir / "fonts")
    offered = {n.casefold() for names in (await get_fonts(db)).values() for n in names}
    wanted = " ".join(q.split()).casefold()
    matches = [
        f
        for f in families
        if (not wanted or wanted in f.name.casefold()) and (group is None or f.group == group)
    ]
    # An exact name first, then the most used.
    matches.sort(key=lambda f: (f.name.casefold() != wanted, f.popularity))
    return CataloguePage(
        items=[
            CatalogueFont(name=f.name, group=f.group, offered=f.name.casefold() in offered)
            for f in matches[offset : offset + limit]
        ],
        total=len(matches),
        available=bool(families),
    )


@router.post("/fonts", response_model=list[FontGroup])
async def add_font(
    body: FontIn, db: DbDep, _: AdminDep, http: HttpDep, settings: SettingsDep
) -> list[FontGroup]:
    """Offer another Google Fonts family for text logos. The name may use any capitals:
    the catalogue gives the family's exact name and its group."""
    name = " ".join(body.name.split())
    family = await find(http, settings.data_dir / "fonts", name)
    if family is not None:
        name, group = family.name, family.group
    elif await catalogue(http, settings.data_dir / "fonts"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Google Fonts has no font called {name!r}."
        )
    else:
        # No catalogue (Google unreachable): ask the CSS API (exact name needed then).
        try:
            await check_font(http, name)
        except FontError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        group = body.group or "Display"
    groups = await get_fonts(db)
    if any(name.casefold() == n.casefold() for names in groups.values() for n in names):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is already in the list.")
    groups[group] = sorted([*groups[group], name])
    await set_fonts(db, groups)
    await db.commit()
    return await _font_groups(db)


@router.delete("/fonts/{name}", response_model=list[FontGroup])
async def remove_font(name: str, db: DbDep, _: AdminDep) -> list[FontGroup]:
    groups = await get_fonts(db)
    remaining = {g: [n for n in names if n != name] for g, names in groups.items()}
    if remaining == groups:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That font isn't in the list.")
    if not any(remaining.values()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keep at least one font.")
    await set_fonts(db, remaining)
    await db.commit()
    return await _font_groups(db)


@router.delete("/fonts", response_model=list[FontGroup])
async def reset_fonts(db: DbDep, _: AdminDep) -> list[FontGroup]:
    """Go back to the default list."""
    await set_fonts(db, None)
    await db.commit()
    return await _font_groups(db)
