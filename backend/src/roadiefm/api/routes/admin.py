"""Instance administration: access mode, approved servers, users."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from roadiefm.api.deps import AdminDep, DbDep, SettingsDep
from roadiefm.api.schemas import (
    AccessSettingsIn,
    AccessSettingsOut,
    AdminServerIn,
    AdminServerOut,
    AdminUserIn,
    AdminUserOut,
)
from roadiefm.db.instance import get_access_settings, set_access_settings
from roadiefm.db.models import PlexServer, User, UserServer

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
        for s, count in rows.tuples()
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
    return [_admin_user_out(u, count) for u, count in rows.tuples()]


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
