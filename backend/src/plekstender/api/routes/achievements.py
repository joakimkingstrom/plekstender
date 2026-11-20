"""Achievements: the list (with progress), new ones to celebrate, and turning them off."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from plekstender import achievements
from plekstender.api.deps import AdminDep, DbDep, UserDep
from plekstender.api.routes.auth import load_preferences
from plekstender.api.schemas import (
    AchievementOut,
    AchievementsOut,
    AchievementsSeenIn,
    AchievementsSettings,
)
from plekstender.history import stats

router = APIRouter(prefix="/api", tags=["achievements"])


@router.get("/achievements", response_model=AchievementsOut)
async def list_achievements(
    db: DbDep,
    user: UserDep,
    tz: Annotated[str, Query(max_length=64)] = "UTC",
    new: Annotated[bool, Query(description="Only unlocked ones not celebrated yet.")] = False,
) -> AchievementsOut:
    """Every achievement with your progress, unlocking any you've just reached. Empty (and
    `enabled` false) when they're turned off, for you or by the admins."""
    on = load_preferences(user).achievements and await achievements.enabled_for_everyone(db)
    if not on:
        return AchievementsOut(enabled=False, items=[])
    try:
        zone = stats.zone(tz)
    except stats.StatsError:
        zone = stats.zone("UTC")
    found = await achievements.evaluate(db, user.id, zone)
    return AchievementsOut(
        enabled=True,
        items=[
            AchievementOut(
                id=p.achievement.id,
                name=p.achievement.name,
                group=p.achievement.group,
                message=p.achievement.message,
                hint=p.achievement.hint,
                goal=p.achievement.goal,
                progress=p.value,
                unlocked_at=p.unlocked_at,
                seen=p.seen,
            )
            for p in found
            if not new or (p.unlocked_at is not None and not p.seen)
        ],
    )


@router.post("/achievements/seen", status_code=status.HTTP_204_NO_CONTENT)
async def seen(body: AchievementsSeenIn, db: DbDep, user: UserDep) -> None:
    await achievements.mark_seen(db, user.id, body.ids)


@router.get("/admin/achievements", response_model=AchievementsSettings)
async def get_settings(db: DbDep, _: AdminDep) -> AchievementsSettings:
    return AchievementsSettings(enabled=await achievements.enabled_for_everyone(db))


@router.put("/admin/achievements", response_model=AchievementsSettings)
async def put_settings(body: AchievementsSettings, db: DbDep, _: AdminDep) -> AchievementsSettings:
    await achievements.set_enabled_for_everyone(db, body.enabled)
    await db.commit()
    return body
