"""Typed accessors for instance-wide settings stored in `instance_settings`."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from roadiefm.config import AccessMode, get_settings
from roadiefm.db.models import InstanceSetting

ACCESS_KEY = "access"
WORKER_HEARTBEAT_KEY = "worker_heartbeat"


async def _get(db: AsyncSession, key: str) -> dict[str, Any] | None:
    row = await db.scalar(select(InstanceSetting).where(InstanceSetting.key == key))
    return row.value if row else None


async def _put(db: AsyncSession, key: str, value: dict[str, Any]) -> None:
    stmt = insert(InstanceSetting).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[InstanceSetting.key],
        set_={"value": stmt.excluded.value, "updated_at": dt.datetime.now(dt.UTC)},
    )
    await db.execute(stmt)


class AccessSettings:
    def __init__(self, mode: AccessMode, allowlist: list[str]) -> None:
        self.mode = mode
        self.allowlist = allowlist


async def get_access_settings(db: AsyncSession) -> AccessSettings:
    stored = await _get(db, ACCESS_KEY) or {}
    mode = AccessMode(stored.get("mode", get_settings().access_mode))
    allowlist = [str(x) for x in stored.get("allowlist", [])]
    return AccessSettings(mode, allowlist)


async def set_access_settings(db: AsyncSession, mode: AccessMode, allowlist: list[str]) -> None:
    cleaned = sorted({x.strip().lower() for x in allowlist if x.strip()})
    await _put(db, ACCESS_KEY, {"mode": mode.value, "allowlist": cleaned})


async def record_worker_heartbeat(db: AsyncSession, now: dt.datetime | None = None) -> None:
    ts = (now or dt.datetime.now(dt.UTC)).isoformat()
    await _put(db, WORKER_HEARTBEAT_KEY, {"at": ts})


async def get_worker_heartbeat(db: AsyncSession) -> dt.datetime | None:
    stored = await _get(db, WORKER_HEARTBEAT_KEY)
    if not stored or "at" not in stored:
        return None
    return dt.datetime.fromisoformat(stored["at"])


# --- library settings -------------------------------------------------------------------

LIBRARY_KEY = "library"


class LibrarySettings(BaseModel):
    """Instance-wide library tuning, editable by admins on the Settings page."""

    album_top_n: int = Field(
        default=3, ge=1, le=20, description="Album popularity = sum of its top N tracks."
    )
    artist_top_n: int = Field(
        default=10, ge=1, le=50, description="Artist popularity = sum of their top N songs."
    )
    incremental_minutes: int = Field(
        default=30, ge=10, le=1440, description="How often to fetch plays, ratings and changes."
    )
    full_days: int = Field(
        default=7, ge=1, le=30, description="How often to re-read whole libraries."
    )


async def get_library_settings(db: AsyncSession) -> LibrarySettings:
    stored = await _get(db, LIBRARY_KEY) or {}
    defaults = LibrarySettings().model_dump()
    # Keep each stored value that's still valid, so a bad value (e.g. from an older
    # version with different limits) only resets that one setting.
    values = {}
    for name, default in defaults.items():
        candidate = {**defaults, name: stored.get(name, default)}
        try:
            values[name] = getattr(LibrarySettings.model_validate(candidate), name)
        except ValidationError:
            values[name] = default
    return LibrarySettings(**values)


async def set_library_settings(db: AsyncSession, settings: LibrarySettings) -> None:
    await _put(db, LIBRARY_KEY, settings.model_dump())
