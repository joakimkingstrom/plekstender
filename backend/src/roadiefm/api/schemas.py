"""Request/response models for the HTTP API (these also define the frontend's types)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from roadiefm.config import AccessMode


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    thumb_url: str | None
    is_admin: bool


class PinStartOut(BaseModel):
    auth_url: str


class PinCheckOut(BaseModel):
    status: Literal["pending", "ok", "expired", "denied"]
    detail: str | None = None
    user: UserOut | None = None


class ServerOut(BaseModel):
    """A server the current user has connected to RoadieFM."""

    id: int
    machine_identifier: str
    name: str
    is_owner: bool
    approved: bool
    connection_kind: str
    music_section_keys: list[str]
    last_connected_at: dt.datetime | None


class AvailableServerOut(BaseModel):
    """A server the user can see on plex.tv."""

    machine_identifier: str
    name: str
    owned: bool
    online: bool
    version: str | None
    connected: bool
    allowed: bool


class ConnectServerIn(BaseModel):
    machine_identifier: str = Field(min_length=1, max_length=64)


class ProbeOut(BaseModel):
    uri_host: str
    kind: str
    ok: bool
    error: str | None


class ConnectServerOut(BaseModel):
    server: ServerOut
    probes: list[ProbeOut]


class SectionOut(BaseModel):
    key: str
    title: str
    selected: bool


class SelectSectionsIn(BaseModel):
    keys: list[str] = Field(max_length=50)


class AccessSettingsOut(BaseModel):
    mode: AccessMode
    allowlist: list[str]
    initial_admin_configured: bool


class AccessSettingsIn(BaseModel):
    mode: AccessMode
    allowlist: list[str] = Field(default_factory=list, max_length=1000)


class AdminServerOut(BaseModel):
    id: int
    machine_identifier: str
    name: str
    approved: bool
    user_count: int


class AdminServerIn(BaseModel):
    approved: bool


class AdminUserOut(BaseModel):
    id: int
    username: str
    email: str | None
    thumb_url: str | None
    is_admin: bool
    created_at: dt.datetime
    last_login_at: dt.datetime | None
    server_count: int


class AdminUserIn(BaseModel):
    is_admin: bool


class WorkerHealth(BaseModel):
    healthy: bool
    last_heartbeat: dt.datetime | None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: bool
    worker: WorkerHealth
