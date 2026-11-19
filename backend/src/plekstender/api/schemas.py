"""Request/response models for the HTTP API (these also define the frontend's types)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from plekstender.config import AccessMode


class Preferences(BaseModel):
    """Per-user settings, stored on the account so they follow the user across devices."""

    accent: str | None = Field(
        default=None,
        pattern=r"^#[0-9a-f]{6}$",
        description="Accent colour; None = the brand amber.",
    )


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    thumb_url: str | None
    is_admin: bool
    preferences: Preferences = Field(default_factory=Preferences)


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
    release_type: str = Field(
        default="Album",
        description="Album, EP, Single, Compilation, Live, Soundtrack… (from Plex's tags).",
    )


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
    listens: int = Field(default=0, description="Your listens of this song (all sources).")


class SimilarArtistOut(BaseModel):
    name: str
    rating_key: int | None  # set when the artist is in your library


class YearListens(BaseModel):
    year: int
    listens: int


class SongListens(BaseModel):
    name: str
    listens: int


class ItemListensOut(BaseModel):
    """The user's own listening history for an artist or album (from Plekstender's
    history, incl. imported Last.fm scrobbles; not Plex's play count)."""

    listens: int
    first_at: dt.datetime | None
    last_at: dt.datetime | None
    years: list[YearListens]
    songs: list[SongListens]
    artist_keys: list[str] = Field(description="Filters for /api/listens: these artists…")
    album_key: str | None = Field(description="…and, for an album, this album.")


class ArtistDetail(ArtistSummary):
    summary: str | None
    art: str | None
    tags: list[TagOut]
    similar: list[SimilarArtistOut]
    albums: list[AlbumSummary]
    top_tracks: list[TrackOut]
    your_listens: ItemListensOut


class AlbumDetail(AlbumSummary):
    summary: str | None
    released: dt.date | None
    studio: str | None
    artist_rating_key: int | None
    artist_title: str | None
    tags: list[TagOut]
    tracks: list[TrackOut]
    your_listens: ItemListensOut


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
    source: Literal["lastfm", "listenbrainz"]
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


class ImportStatusOut(BaseModel):
    available: bool = Field(description="Whether this kind of import can run on this instance.")
    username: str | None = Field(description="Username used by the latest import.")
    active: ListenImportOut | None
    last: ListenImportOut | None


class LastFmImportIn(BaseModel):
    username: str = Field(min_length=2, max_length=15, pattern=r"^[A-Za-z][A-Za-z0-9_-]+$")


class ListenBrainzImportIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[^/?#\s][^/?#]*$")


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


class ListenBrainzStatusOut(BaseModel):
    connected: bool
    username: str | None
    enabled_at: dt.datetime | None = Field(description="Plays from this time on are sent.")
    sent: int
    pending: int
    last_submitted_at: dt.datetime | None
    paused: bool = Field(description="ListenBrainz rejected the token; nothing is sent.")
    error: str | None


class ListenBrainzTokenIn(BaseModel):
    token: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9-]+$")


# --- listening stats ---------------------------------------------------------------------


class StatsPeriodOut(BaseModel):
    key: str
    start: dt.datetime | None = Field(description="None for all time.")
    end: dt.datetime
    previous_start: dt.datetime | None = Field(description="Start of the comparison period.")


class StatsTotals(BaseModel):
    listens: int
    listening_ms: int = Field(description="Listens without a known length count as 3:35.")
    artists: int
    tracks: int


class TopItemOut(BaseModel):
    name: str
    artist: str | None = Field(description="For albums and tracks.")
    listens: int
    listening_ms: int = Field(description="Listens without a known length count as 3:35.")
    previous_rank: int | None = Field(description="Rank in the previous period, if played.")
    rating_key: int | None = Field(description="Library artist / album (for tracks: album).")
    thumb: str | None


class StatsOverviewOut(BaseModel):
    period: StatsPeriodOut
    totals: StatsTotals
    previous: StatsTotals | None
    new_artists: int
    top_artists: list[TopItemOut]
    top_albums: list[TopItemOut]
    top_tracks: list[TopItemOut]
    current_streak: int = Field(description="Consecutive days with listens, up to today.")
    longest_streak: int
    first_listen_at: dt.datetime | None


class ActivityPoint(BaseModel):
    start: dt.date
    listens: int


class StatsActivityOut(BaseModel):
    bucket: Literal["day", "week", "month"]
    points: list[ActivityPoint]
    heatmap: list[list[int]] = Field(description="7 rows (Monday first) x 24 hours.")


# --- ratings -----------------------------------------------------------------------------

RateKind = Literal["track", "album", "artist"]


class RateQueueIn(BaseModel):
    kind: RateKind
    pool: Literal["listened", "unrated", "all"] = "listened"
    order: Literal["weighted", "plays", "popular"] = "weighted"
    exclude: list[int] = Field(default_factory=list, max_length=5000)
    limit: int = Field(default=20, ge=1, le=50)
    tag_id: int | None = Field(default=None, description="Only items with this genre (tag).")


class RateItemOut(BaseModel):
    rating_key: int
    kind: RateKind
    title: str
    subtitle: str | None = Field(description="Artist, for songs and albums.")
    album_title: str | None
    album_rating_key: int | None
    thumb: str | None = Field(description="Album cover for songs.")
    year: int | None
    duration_ms: int | None
    popularity: int | None
    summary: str | None = Field(description="Album review or artist biography.")
    critic_rating: float | None
    user_rating: float | None = Field(description="0-10; for songs, of any release.")
    plays: int = Field(description="Plex plays or listens in your history, whichever is more.")


class RatingIn(BaseModel):
    rating_key: int
    rating: int | None = Field(ge=0, le=10, description="0-10 (half stars = 1); None clears.")


class RatingOut(BaseModel):
    rating_key: int
    item_type: RateKind
    user_rating: float | None


# --- artwork -----------------------------------------------------------------------------

ArtworkStatus = Literal["none", "custom", "done"]


class ArtworkArtistOut(BaseModel):
    rating_key: int
    title: str
    thumb: str | None
    status: ArtworkStatus
    source: str | None
    applied_at: dt.datetime | None
    has_mbid: bool
    folder: str | None = Field(default=None, description="Apply: where artist.jpg was saved.")
    folder_error: str | None = Field(default=None, description="Apply: why it wasn't saved.")


class ArtworkArtistPage(BaseModel):
    items: list[ArtworkArtistOut]
    total: int
    counts: dict[str, int] = Field(description="Artists per status, for the filter.")


class ArtworkStatusIn(BaseModel):
    status: ArtworkStatus


class CandidateOut(BaseModel):
    token: str = Field(description="Signed reference: pass it back to preview or apply.")
    thumb: str = Field(description="Proxied thumbnail URL for <img>.")
    label: str | None
    likes: int | None
    width: int | None
    height: int | None


class CandidatesOut(BaseModel):
    candidates: list[CandidateOut]
    note: str | None


class UploadOut(BaseModel):
    upload_id: str


class FontGroup(BaseModel):
    name: str
    fonts: list[str]


class FanartKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None


class FanartKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{32}$")


class ArtworkJobOut(BaseModel):
    id: int
    kind: Literal["fanart", "export", "import"]
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: dict[str, Any]
    stats: dict[str, Any]
    error: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None
    download: str | None = Field(default=None, description="Export: where to download it.")


class FanartJobIn(BaseModel):
    rating_keys: list[int] | None = Field(default=None, max_length=10000)
    status: Literal["none", "custom", "done"] = Field(
        default="none", description="Without rating_keys: every artist with this status."
    )


class FolderSettingsIn(BaseModel):
    enabled: bool
    plex_prefix: str | None = Field(default=None, max_length=1000)
    local_prefix: str | None = Field(default=None, max_length=1000)


class FolderExample(BaseModel):
    artist: str
    local_folder: str | None
    exists: bool
    problem: str | None


class FolderSettingsOut(BaseModel):
    enabled: bool
    plex_prefix: str | None
    local_prefix: str | None
    plex_root: str | None = Field(description="The common start of Plex's music paths (a hint).")
    example: FolderExample | None


class ImportMatchOut(BaseModel):
    rating_key: int
    title: str
    thumb: str | None
    status: str | None


class ImportEntryOut(BaseModel):
    index: int
    artist_name: str
    mbid: str | None
    image: str = Field(description="Thumbnail URL of the pack's image.")
    match: ImportMatchOut | None
    matched_by: Literal["mbid", "name"] | None


class ImportPreviewOut(BaseModel):
    import_id: str
    entries: list[ImportEntryOut]
    matched: int
    total: int


class ImportItemIn(BaseModel):
    index: int = Field(ge=0)
    rating_key: int


class ImportApplyIn(BaseModel):
    items: list[ImportItemIn] = Field(min_length=1, max_length=20000)
