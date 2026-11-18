"""Request/response models for the HTTP API (these also define the frontend's types)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from plekstender.config import AccessMode


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
    """A server the current user has connected to Plekstender."""

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


# --- library sync -----------------------------------------------------------------------


class SyncRunOut(BaseModel):
    id: int
    kind: Literal["full", "incremental"]
    trigger: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    progress: dict[str, Any]
    stats: dict[str, Any]
    error: str | None


class LibraryCounts(BaseModel):
    artists: int
    albums: int
    tracks: int
    rated_tracks: int
    plays: int


class SyncStatusOut(BaseModel):
    active: SyncRunOut | None
    last: SyncRunOut | None
    last_full_sync_at: dt.datetime | None
    last_sync_at: dt.datetime | None
    counts: LibraryCounts
    # Current schedule (instance-wide, set by admins).
    incremental_minutes: int
    full_days: int


class SyncIn(BaseModel):
    kind: Literal["full", "incremental"] = "incremental"


# --- library browsing -------------------------------------------------------------------


class TagOut(BaseModel):
    id: int
    kind: str
    name: str
    count: int | None = None


class ArtistSummary(BaseModel):
    rating_key: int
    title: str
    thumb: str | None
    popularity: int | None
    user_rating: float | None
    plays: int
    album_count: int


class ArtistPage(BaseModel):
    items: list[ArtistSummary]
    total: int
    offset: int
    limit: int


class AlbumSummary(BaseModel):
    rating_key: int
    title: str
    year: int | None
    thumb: str | None
    popularity: int | None
    critic_rating: float | None
    track_count: int | None
    user_rating: float | None
    plays: int


class TrackOut(BaseModel):
    rating_key: int
    title: str
    original_title: str | None
    disc: int | None
    number: int | None
    duration_ms: int | None
    popularity: int | None
    user_rating: float | None
    plays: int
    album_rating_key: int | None
    album_title: str | None
    album_thumb: str | None


class SimilarArtistOut(BaseModel):
    name: str
    rating_key: int | None  # set when the artist is in your library


class ArtistDetail(ArtistSummary):
    summary: str | None
    art: str | None
    tags: list[TagOut]
    similar: list[SimilarArtistOut]
    albums: list[AlbumSummary]
    top_tracks: list[TrackOut]


class AlbumDetail(AlbumSummary):
    summary: str | None
    released: dt.date | None
    studio: str | None
    artist_rating_key: int | None
    artist_title: str | None
    tags: list[TagOut]
    tracks: list[TrackOut]


# --- library settings (admin) -------------------------------------------------------------


class PopularityPreviewItem(BaseModel):
    rating_key: int
    title: str
    popularity: int
    current_rank: int


# --- listening history -------------------------------------------------------------------

ListenSource = Literal["lastfm", "plex", "listenbrainz"]


class ListenOut(BaseModel):
    id: int
    listened_at: dt.datetime
    source: ListenSource
    artist_name: str
    album_name: str | None
    track_name: str
    duplicate: bool = Field(description="Also recorded by another source; not counted twice.")


class ListenPage(BaseModel):
    items: list[ListenOut]
    total: int


class ListenSummary(BaseModel):
    total: int
    first_at: dt.datetime | None
    last_at: dt.datetime | None
    by_source: dict[str, int]


class ListenImportOut(BaseModel):
    id: int
    source: Literal["lastfm"]
    username: str
    status: Literal["queued", "running", "succeeded", "failed"]
    window_from: dt.datetime | None
    window_to: dt.datetime
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    progress: dict[str, Any]
    stats: dict[str, Any]
    error: str | None


class LastFmStatusOut(BaseModel):
    available: bool = Field(description="Whether this instance has a Last.fm API key.")
    username: str | None = Field(description="Last.fm username used by the latest import.")
    active: ListenImportOut | None
    last: ListenImportOut | None


class LastFmImportIn(BaseModel):
    username: str = Field(min_length=2, max_length=15, pattern=r"^[A-Za-z][A-Za-z0-9_-]+$")


class DeletedOut(BaseModel):
    deleted: int


class LastFmKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None


class LastFmKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{32}$")


class PlexCaptureOut(BaseModel):
    server_id: int = Field(description="The user's server, as in /api/servers.")
    server_name: str
    via_owner: bool = Field(description="Read through the server owner's connection.")
    checked_at: dt.datetime | None
    backfilled: bool = Field(description="All plays from before connecting have been read.")
    error: str | None
    listens: int
