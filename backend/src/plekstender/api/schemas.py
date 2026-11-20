"""Request/response models for the HTTP API (these also define the frontend's types)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from plekstender.config import AccessMode
from plekstender.playlists.definition import Definition


class GameSettings(BaseModel):
    """Guess the intro, per account."""

    round_seconds: Literal[15, 20, 25, 30] = Field(default=30, description="Length of a round.")
    max_guesses: int = Field(default=3, ge=0, le=10, description="Guesses per round; 0 = any.")
    artist_feedback: bool = Field(
        default=True, description="Song and album modes: say whether the artist guessed is right."
    )
    playlist: str = Field(
        default="Guess the intro",
        min_length=1,
        max_length=100,
        description="The Plex playlist songs are added to (created when missing).",
    )
    popularity: Literal["popular", "mixed", "deep"] = Field(
        default="popular", description="Well-known songs, any, or deep cuts."
    )


class Preferences(BaseModel):
    """Per-user settings, stored on the account so they follow the user across devices."""

    accent: str | None = Field(
        default=None,
        pattern=r"^#[0-9a-f]{6}$",
        description="Accent colour; None = the brand amber.",
    )
    skip_threshold: int = Field(
        default=80,
        ge=10,
        le=95,
        description="A play counts as skipped when less than this % of the track was heard.",
    )
    phone_start: Literal["rate", "home"] = Field(
        default="rate", description="The page the app opens on, on a phone."
    )
    game: GameSettings = Field(default_factory=GameSettings)


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


class TagAlbum(AlbumSummary):
    artist_rating_key: int | None
    artist_title: str | None


class TagDetail(BaseModel):
    """A genre's, style's or mood's page."""

    id: int
    kind: str
    name: str
    artist_count: int
    album_count: int
    artists: list[ArtistSummary] = Field(description="Tagged themselves or on an album.")
    albums: list[TagAlbum]
    related: list[TagOut] = Field(description="Tags often on the same artists and albums.")


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
    client: str | None = Field(default=None, description="Player, for plays seen live.")
    address: str | None = None
    network: Literal["lan", "wan"] | None = None
    location: str | None = Field(default=None, description="City, country (IP database).")


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
    source: Literal["lastfm", "listenbrainz", "lb_send"] = Field(
        description="lb_send: sending the history to ListenBrainz."
    )
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
    order: Literal["weighted", "plays", "popular", "obscure"] = "weighted"
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
    folder: str | None = Field(default=None, description="Where artist.jpg was last saved.")
    folder_error: str | None = Field(
        default=None, description="Why artist.jpg wasn't saved, the last time."
    )
    folder_queued: bool = Field(
        default=False, description="Apply: artist.jpg is being written by the worker."
    )


class ArtworkArtistPage(BaseModel):
    items: list[ArtworkArtistOut]
    total: int
    counts: dict[str, int] = Field(description="Artists per status, for the filter.")


class ArtworkStatusIn(BaseModel):
    status: ArtworkStatus


class ArtworkBulkStatusIn(BaseModel):
    status: Literal["custom", "done"] = Field(description="New status for every To do artist.")


class ArtworkBulkStatusOut(BaseModel):
    changed: int = Field(description="Artists whose status changed.")


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


FontGroupName = Literal["Sans serif", "Serif", "Display", "Handwriting", "Monospace"]


class FontIn(BaseModel):
    name: str = Field(
        min_length=1, max_length=64, description="A Google Fonts family, any capitals."
    )
    group: FontGroupName | None = Field(
        default=None,
        description="Only needed when the Google Fonts catalogue can't be reached "
        "(otherwise it comes from the catalogue).",
    )


class CatalogueFont(BaseModel):
    name: str
    group: FontGroupName
    offered: bool = Field(description="Already in the list.")


class CataloguePage(BaseModel):
    items: list[CatalogueFont]
    total: int
    available: bool = Field(description="False when Google Fonts couldn't be reached.")


class FanartKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None


class FanartKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{32}$")


class AcoustIdKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None


class AcoustIdKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{6,64}$")


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
    checking: bool = Field(default=False, description="The worker is checking the mapping.")
    problem: str | None = Field(default=None, description="From the check: what's wrong.")


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


class SkippedItemOut(BaseModel):
    rating_key: int
    name: str
    artist: str | None
    thumb: str | None
    skips: int
    plays: int = Field(description="Plays seen live by the session watcher.")


class SkipsOut(BaseModel):
    threshold: int = Field(description="Percent heard below which a play counts as skipped.")
    watching: bool = Field(description="Whether any plays have been seen live yet.")
    items: list[SkippedItemOut]


# --- smart playlists ---------------------------------------------------------------------

Schedule = Literal["manual", "daily", "weekly", "monthly"]


class SeedArtist(BaseModel):
    rating_key: int
    title: str
    thumb: str | None


class SeedTrack(BaseModel):
    rating_key: int
    title: str
    artist: str | None
    thumb: str | None


class PlaylistSearchOut(BaseModel):
    artists: list[SeedArtist]
    tracks: list[SeedTrack]


class PlaylistSuggestionsOut(BaseModel):
    similar: list[SeedArtist] = Field(description="Plex's similar artists, in your library.")
    sonic_artists: list[SeedArtist] = Field(description="Sonically similar (Plex Pass analysis).")
    sonic_tracks: list[SeedTrack]


class PlaylistSong(BaseModel):
    rating_key: int
    title: str
    artist: str | None
    artist_key: int | None
    album: str | None
    thumb: str | None
    duration_ms: int | None
    rating: float | None


class PlaylistPreviewOut(BaseModel):
    matched: int = Field(description="Songs matching, before the limit.")
    count: int
    duration_ms: int
    artists: int
    top_artists: list[str]
    songs: list[PlaylistSong] = Field(description="Up to 100 of the songs, in playlist order.")


class PlaylistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    definition: Definition
    display: dict[str, Any] = Field(default_factory=dict)
    schedule: Schedule = "manual"


class PlaylistOut(BaseModel):
    id: int
    name: str
    description: str | None
    definition: dict[str, Any]
    display: dict[str, Any]
    schedule: Schedule
    plex_playlist_key: int | None
    status: Literal["new", "queued", "running", "ok", "error"]
    error: str | None
    last_result: dict[str, Any]
    last_built_at: dt.datetime | None
    next_run_at: dt.datetime | None
    created_at: dt.datetime
    exportable: bool = Field(description="Built at least once, so it can be exported as M3U.")


class PlaylistDetail(PlaylistOut):
    seed_artists: list[SeedArtist]
    seed_tracks: list[SeedTrack]


# --- listens map -------------------------------------------------------------------------


class CountryListensOut(BaseModel):
    country_code: str = Field(description="ISO 3166-1 alpha-2")
    continent_code: str | None = Field(description="AF AN AS EU NA OC SA")
    listens: int
    listening_ms: int


class PlacesOut(BaseModel):
    countries: list[CountryListensOut]
    located: int = Field(description="Listens in the period with a known place.")
    total: int = Field(description="All listens in the period.")
    attribution: str


class CityListensOut(BaseModel):
    name: str
    listens: int


class PlaceDetailOut(BaseModel):
    listens: int
    listening_ms: int
    top_artists: list[TopItemOut]
    top_tracks: list[TopItemOut]
    cities: list[CityListensOut] = Field(description="For a country: its towns and cities.")


# --- previews and the intro game ---------------------------------------------------------


class PreviewOut(BaseModel):
    rating_key: int
    url: str = Field(description="The clip (AAC in MP4, 40 seconds).")
    start_ms: int = Field(description="Where the music starts in the clip (after silence).")
    quiet_intro: bool = Field(description="Silent or near-silent for 8 seconds or more.")


GameMode = Literal["track", "album", "artist"]


class GameRoundOut(BaseModel):
    playable: bool
    reason: str | None = Field(description="Why not: short-title, quiet-intro, no-file.")
    preview: PreviewOut | None = Field(description="The song to play for this round.")
    song_title: str | None = Field(default=None, description="Its title (a hint, late in a round).")


class GameSearchItem(BaseModel):
    rating_key: int
    kind: GameMode
    title: str
    subtitle: str | None = Field(description="The artist, for albums and songs.")
    thumb: str | None


class GameCheckIn(BaseModel):
    mode: GameMode
    target: int = Field(description="The item being guessed (song, album or artist).")
    guess: int
    guess_kind: GameMode | None = Field(
        default=None, description="What `guess` is, when not the mode's kind (an artist)."
    )


class GameCheckOut(BaseModel):
    correct: bool
    artist_correct: bool | None = Field(
        description="Song and album modes: whether the guess is by the right artist."
    )


class GamePlaylistIn(BaseModel):
    mode: GameMode
    target: int = Field(description="The card: its artist's most popular songs are added.")


class GamePlaylistOut(BaseModel):
    playlist: str
    added: list[str] = Field(description="Titles of the songs added.")
    already: int = Field(description="Songs that were in the playlist already.")


class GameResultIn(BaseModel):
    mode: GameMode
    correct: bool
    points: int = Field(ge=0, le=100)
    streak: int = Field(ge=0, le=100_000)


class GameStatsOut(BaseModel):
    mode: GameMode
    rounds: int
    correct: int
    points: int
    best_streak: int


# --- your ratings ------------------------------------------------------------------------


class RatedItemOut(BaseModel):
    rating_key: int
    kind: Literal["track", "album", "artist"]
    title: str
    subtitle: str | None = Field(description="Songs: artist · album; albums: artist.")
    thumb: str | None
    user_rating: float
    rated_at: dt.datetime | None = Field(description="When it was last rated (Plex or here).")
    link_key: int | None = Field(description="The album (songs, albums) or artist to link to.")
    undo_to: float | None = Field(
        default=None, description="The rating an undo would put back (None: unrated)."
    )
    undoable: bool = Field(
        default=False,
        description="Rated here, and not changed since (ratings set in Plex have no undo).",
    )


class RatedPage(BaseModel):
    items: list[RatedItemOut]
    total: int


class LeaderboardRow(BaseModel):
    user_id: int
    username: str
    thumb_url: str | None
    you: bool
    points: int
    rounds: int
    correct: int
    best_streak: int
    last_score: int | None = Field(
        description="Points in the latest game (rounds without a 30-minute break)."
    )
    last_rounds: int | None = Field(description="Rounds in the latest game.")
    last_played_at: dt.datetime | None


# --- music files (tag editor) --------------------------------------------------------------


class FileScanOut(BaseModel):
    id: int
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: dict[str, Any]
    stats: dict[str, Any]
    error: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None


class FilesSummaryOut(BaseModel):
    mapped: bool = Field(description="The music folder is mapped (Settings → Music files).")
    tag_editing: bool = Field(default=False, description="Tags may be edited (owner's choice).")
    files: int
    matched: int = Field(description="Files that are a Plex track.")
    errors: int = Field(description="Files whose tags couldn't be read.")
    active: FileScanOut | None
    last: FileScanOut | None


class TrackFileOut(BaseModel):
    rating_key: int
    file_id: int | None = Field(default=None, description="The music file, for edits.")
    disc: int | None
    number: int | None
    plex: dict[str, Any] = Field(description="Plex's values, in the same fields as `tags`.")
    file: str | None = Field(description="The file, relative to the music folder.")
    format: str | None
    duration_ms: int | None
    has_cover: bool
    tags: dict[str, Any]
    error: str | None
    differences: list[str] = Field(description="Fields where the file and Plex disagree.")


class AlbumFilesOut(BaseModel):
    scanned: bool = Field(description="The music folder has been scanned.")
    tracks: list[TrackFileOut]


TagValue = str | list[str] | None


class TagEditChange(BaseModel):
    file_id: int
    field: Literal[
        "title", "artist", "album", "albumartist", "date", "genre",
        "tracknumber", "discnumber", "composer", "originaldate", "releasecountry",
        "label", "catalognumber", "barcode", "musicbrainz_albumid",
        "musicbrainz_releasegroupid", "musicbrainz_albumartistid",
    ]  # fmt: skip
    value: TagValue = Field(description="New value; a list for genre; null removes the tag.")


class TagEditIn(BaseModel):
    description: str | None = Field(default=None, max_length=200)
    changes: list[TagEditChange] = Field(min_length=1, max_length=5000)


class TagChangeOut(BaseModel):
    id: int
    file_id: int | None
    path: str
    rating_key: int | None
    field: str
    old_value: TagValue
    new_value: TagValue
    status: Literal["pending", "applied", "conflict", "failed"]
    error: str | None
    undoes_id: int | None
    undone: bool = Field(description="An applied undo put this change back.")


class TagChangeSetOut(BaseModel):
    id: int
    kind: Literal["edit", "undo"]
    description: str
    status: Literal["queued", "running", "applied", "partial", "failed"]
    error: str | None
    user: str | None
    created_at: dt.datetime
    applied_at: dt.datetime | None
    total: int = Field(default=0, description="All its changes.")
    counts: dict[str, int] = Field(
        default_factory=dict, description="Changes per status (pending, applied, ...)."
    )
    undoable: int = Field(default=0, description="Written changes not undone yet.")
    changes: list[TagChangeOut] = Field(description="The first 200 (big edits are listed in part).")


class TagUndoIn(BaseModel):
    change_ids: list[int] = Field(default_factory=list, max_length=5000)
    set_id: int | None = Field(default=None, description="Undo all of an edit (any size).")


SyncField = Literal[
    "title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date", "genre",
    "rating",
]  # fmt: skip


class PlexSyncIn(BaseModel):
    scope: Literal["library", "artist", "album"]
    key: int | None = Field(default=None, description="The artist or album.")
    fields: list[SyncField] = Field(
        default_factory=lambda: [f for f in get_args(SyncField) if f != "rating"],
        min_length=1,
        description='Your ratings are only written when asked for ("rating").',
    )
    description: str | None = Field(default=None, max_length=200)


class PlexSyncSample(BaseModel):
    path: str
    field: str
    old_unknown: bool = Field(
        default=False, description="The file was scanned before this field was read."
    )
    old_value: TagValue
    new_value: TagValue


class PlexSyncPreviewOut(BaseModel):
    files: int = Field(description="Files that would change.")
    changes: int
    by_field: dict[str, int]
    samples: list[PlexSyncSample] = Field(description="The first 100 changes.")


MbField = Literal[
    "title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date",
    "originaldate", "releasecountry", "musicbrainz_trackid", "musicbrainz_releasetrackid",
    "musicbrainz_albumid", "musicbrainz_artistid", "musicbrainz_albumartistid",
    "musicbrainz_releasegroupid",
]  # fmt: skip


class MbReleaseSummary(BaseModel):
    id: str
    score: int = Field(description="MusicBrainz's search score (0-100).")
    title: str
    artist: str
    date: str | None
    country: str | None
    formats: str = Field(description="The media: CD, two CDs, Digital Media...")
    track_count: int
    label: str | None
    catalog_number: str | None
    type: str | None = Field(description='"Album", "Album + Live"...')
    status: str | None
    disambiguation: str | None
    barcode: str | None


class MbTrackOut(BaseModel):
    id: str
    disc: int
    position: int
    title: str
    artist: str
    length_ms: int | None


class MbApplyIn(BaseModel):
    release_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    mapping: dict[int, str | None] | None = Field(
        default=None,
        description="File id -> release track id (None: leave the file alone). "
        "Left out: matched automatically.",
    )
    fields: list[MbField] = Field(default_factory=lambda: list(get_args(MbField)), min_length=1)


class MbFieldChange(BaseModel):
    field: str
    old_unknown: bool = Field(
        default=False, description="The file was scanned before this field was read."
    )
    old_value: TagValue
    new_value: TagValue


class MbFileOut(BaseModel):
    file_id: int
    path: str
    disc: int | None
    track: int | None
    title: str | None
    duration_ms: int | None
    track_id: str | None = Field(description="The release track it's matched to.")
    changes: list[MbFieldChange]


class MbPreviewOut(BaseModel):
    release: MbReleaseSummary
    tracks: list[MbTrackOut]
    files: list[MbFileOut]
    changes: int


UnknownKind = Literal["missing", "unmatched", "no_mbid"]


class IdentifyIn(BaseModel):
    kind: UnknownKind | None = Field(default=None, description="Every file of this kind...")
    file_ids: list[int] = Field(default_factory=list, max_length=5000, description="...or these.")
    album_key: int | None = Field(default=None, description="...or an album's files.")
    again: bool = Field(default=False, description="Look up files identified before again.")


class IdentifyStatusOut(BaseModel):
    configured: bool = Field(description="An AcoustID key is set.")
    active: FileScanOut | None
    last: FileScanOut | None


class IdentityRelease(BaseModel):
    id: str
    title: str
    artist: str
    date: str | None
    country: str | None
    format: str | None
    disc: int
    discs: int
    track_count: int
    position: int
    track_id: str | None


class IdentityRecording(BaseModel):
    id: str
    title: str
    artist: str
    duration: int | None
    releases: list[IdentityRelease]


class IdentityResult(BaseModel):
    acoustid: str | None
    score: float = Field(description="How alike it sounds, 0-1.")
    recordings: list[IdentityRecording]


class IdentityChoice(BaseModel):
    file_id: int
    recording_id: str
    track_id: str | None = Field(default=None, description="A release track; none: song only.")


class UnknownFileOut(BaseModel):
    file_id: int
    path: str
    in_plex: bool
    duration_ms: int | None
    tags: dict[str, Any]
    status: Literal["new", "identified", "no_match", "error"]
    error: str | None
    results: list[IdentityResult]
    suggestion: IdentityChoice | None


class UnknownFilesOut(BaseModel):
    total: int
    files: list[UnknownFileOut] = Field(description="The first 200.")


class IdentifyApplyIn(BaseModel):
    choices: list[IdentityChoice] = Field(min_length=1, max_length=5000)
    fields: list[MbField] = Field(default_factory=lambda: list(get_args(MbField)), min_length=1)


class IdentifyFileChanges(BaseModel):
    file_id: int
    path: str
    changes: list[MbFieldChange]


class IdentifyPreviewOut(BaseModel):
    files: list[IdentifyFileChanges]
    changes: int


class RenameIn(BaseModel):
    scope: Literal["album", "artist"]
    key: int
    template: str = Field(min_length=1, max_length=300)


class RenameMove(BaseModel):
    kind: Literal["song", "sidecar", "folder"] = Field(
        description="A song, a file named like a song (lyrics), or a folder's other files."
    )
    source: str
    target: str


class RenameSkip(BaseModel):
    path: str
    target: str | None
    reason: str


class RenamePlanOut(BaseModel):
    job: FileScanOut
    template: str | None
    songs: int = Field(description="Songs the template was applied to.")
    unchanged: int = Field(description="Songs already where the template puts them.")
    moves: list[RenameMove]
    skipped: list[RenameSkip]
    applied_set_id: int | None = Field(description="The edit that made these moves, once made.")


class NfoOut(BaseModel):
    path: str
    kind: Literal["artist", "album"]
    data: dict[str, Any] = Field(
        description="album: title, artist, year, release_date, label, type, review, "
        "musicbrainz_albumid, musicbrainz_releasegroupid, genres, styles, moods, themes. "
        "artist: name, sort_name, type, gender, disambiguation, born, died, years_active, "
        "biography, musicbrainz_artistid, genres, styles, moods, themes."
    )
    error: str | None
    file_release_ids: list[str] = Field(
        default_factory=list, description="Album: the MusicBrainz release IDs in its files."
    )
    plex_mbid: str | None = Field(default=None, description="Artist: Plex's MusicBrainz ID.")


class FolderImageOut(BaseModel):
    path: str
    kind: Literal["cover", "artist"]
    sha1: str | None
    width: int | None
    height: int | None
    size: int
    error: str | None


class EmbeddedCoverOut(BaseModel):
    sha1: str
    mime: str
    bytes: int
    width: int | None
    height: int | None
    files: int = Field(description="Songs with this cover.")
    oversized: bool = Field(description="Bigger than players cope with well.")


class AlbumArtworkOut(BaseModel):
    songs: int = Field(description="The album's songs with a file.")
    without_cover: int
    covers: list[EmbeddedCoverOut] = Field(description="Embedded covers, most songs first.")
    folder_images: list[FolderImageOut]
    folder: str | None = Field(description="The album's folder (where cover.jpg goes).")
    plex_poster: bool


class EmbedCoverIn(BaseModel):
    source: Literal["folder", "plex"]
    path: str | None = Field(default=None, description="The folder image (source folder).")
    max_px: int = Field(default=1000, ge=300, le=3000)


class FolderCoverIn(BaseModel):
    source: Literal["embedded", "plex"]
    sha1: str | None = Field(default=None, description="Which embedded cover (source embedded).")
    name: Literal["cover.jpg", "folder.jpg"] = "cover.jpg"


class ArtworkSummaryOut(BaseModel):
    songs: int
    without_cover: int
    oversized: int = Field(description="Embedded covers over 1000 px or about 1 MB.")
    folders_without_cover: int = Field(description="Folders with songs and no cover image.")


class ShrinkCoversIn(BaseModel):
    max_px: int = Field(default=1000, ge=300, le=3000)


class TagSettingsIn(BaseModel):
    tag_editing: bool


# --- live ----------------------------------------------------------------------------------


class LiveSettingsIn(BaseModel):
    place_name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: int = Field(default=100, ge=5, le=1000)
    top_percent: int = Field(default=10, ge=1, le=100)
    period_months: int | None = Field(
        default=None,
        ge=1,
        le=600,
        description="Rank by the past N months' listens (None: all time).",
    )


class LiveSettingsOut(BaseModel):
    place_name: str | None
    latitude: float | None
    longitude: float | None
    radius_km: int
    top_percent: int
    period_months: int | None
    listened_artists: int = Field(description="Artists the user has played (what % is of).")
    followed_artists: int = Field(description="Artists looked up with the current %.")
    refresh_status: Literal["queued", "running", "done", "failed"] | None
    refresh_error: str | None
    refreshed_at: dt.datetime | None
    refresh_available_at: dt.datetime | None = Field(
        description="When a refresh can be asked for again (None: now)."
    )
    source_ready: bool = Field(description="A source has a key, or the user has calendar links.")
    sources: list[Literal["jambase", "ticketmaster", "datathistle"]] = Field(
        description="Sources with a key."
    )
    datathistle_at: dt.datetime | None = Field(description="Data Thistle last looked up.")
    datathistle_error: str | None


class PlaceOut(BaseModel):
    name: str
    latitude: float
    longitude: float
    country: str | None


class LiveArtistOut(BaseModel):
    name: str
    headliner: bool
    server_id: int | None = None
    rating_key: int | None = None


class LiveTicketOut(BaseModel):
    source: str
    url: str
    seller: str | None
    is_ticket: bool = Field(description="A ticket link (else the show's page).")


class LiveShowOut(BaseModel):
    id: int = Field(description="The first source's row.")
    ids: list[int] = Field(description="Every source's row for this show (to star them).")
    source: str
    sources: list[str]
    starred: bool
    got_ticket: bool = Field(description="The user has a ticket (starred shows).")
    first_seen_at: dt.datetime | None = Field(
        default=None, description="When Plekstender first found it (any source)."
    )
    is_new: bool = Field(default=False, description="First found in the past week.")
    tickets: list[LiveTicketOut] = Field(description="Ticket links, the first source's first.")
    name: str
    festival: bool
    start_date: dt.date
    start_time: str | None
    end_date: dt.date | None
    status: str
    venue: str | None
    city: str | None
    region: str | None
    country: str | None
    distance_km: float | None
    ticket_url: str | None
    seller: str | None
    page_url: str | None
    price_min: float | None = Field(default=None, description="Cheapest ticket (0: free).")
    price_max: float | None = None
    currency: str | None = None
    artists: list[LiveArtistOut] = Field(description="The user's artists on the bill.")
    performers: list[LiveArtistOut] = Field(description="Everyone on the bill.")


class LiveArtistCount(BaseModel):
    name: str
    shows: int


class LiveVenueCount(BaseModel):
    name: str
    city: str | None
    shows: int


class LiveShowsOut(BaseModel):
    shows: list[LiveShowOut]
    artists: list[LiveArtistCount] = Field(description="Artists with shows, for the filter.")
    venues: list[LiveVenueCount] = Field(description="Venues with shows, for the filter.")


class JamBaseKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^jbd_[A-Za-z0-9_\-]{8,200}$")


class JamBaseKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None
    month: str
    calls: int = Field(description="Requests Plekstender sent this month.")
    cap: int = Field(description="Plekstender stops at this many requests a month.")
    quota: dict[str, Any] | None = Field(description="JamBase's own count, when last checked.")
    quota_checked_at: dt.datetime | None


class LiveStarIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=20)
    starred: bool


class LiveTicketIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=20)
    got_ticket: bool


class LiveCalendarOut(BaseModel):
    url: str = Field(description="Private address of the starred shows' calendar (iCal).")


class TicketmasterKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{16,64}$")


class TicketmasterKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None
    day: str
    calls: int = Field(description="Requests Plekstender sent today (UTC).")
    cap: int = Field(description="Plekstender stops at this many requests a day.")


class LiveListenedOut(BaseModel):
    listened_artists: int = Field(description="Artists played in the period (all time: None).")


class LiveFeedIn(BaseModel):
    url: str = Field(min_length=10, max_length=2000)


class LiveFeedOut(BaseModel):
    id: int
    host: str = Field(description="The site (the address itself may be private).")
    name: str | None
    fetched_at: dt.datetime | None
    error: str | None
    show_count: int | None


class DataThistleTokensIn(BaseModel):
    access_token: str = Field(pattern=r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
    refresh_token: str = Field(pattern=r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


class DataThistleOut(BaseModel):
    configured: bool
    access_expires: dt.datetime | None
    refresh_expires: dt.datetime | None
    renewed_at: dt.datetime | None = Field(description="When Plekstender last renewed them.")
    error: str | None = Field(description="Why renewing failed, the last time.")
    day: str
    calls: int = Field(description="Requests Plekstender sent today (UTC).")
    cap: int
    limit: int | None = Field(description="Data Thistle's monthly limit, as it last said.")
    remaining: int | None
    resets_at: dt.datetime | None


# --- gigs (the ones the user went to) -------------------------------------------------------


class GigArtistOut(BaseModel):
    name: str
    server_id: int | None = None
    rating_key: int | None = None


class GigIn(BaseModel):
    date: dt.date
    artists: list[str] = Field(min_length=1, max_length=50)
    name: str | None = Field(default=None, max_length=300)
    venue: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    notes: str | None = Field(default=None, max_length=4000)
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")


class GigOut(BaseModel):
    id: int
    date: dt.date
    name: str | None
    venue: str | None
    city: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    place_precision: Literal["venue", "city"] | None
    notes: str | None
    artists: list[GigArtistOut]
    source: str
    url: str | None


class GigYear(BaseModel):
    year: int
    gigs: int


class GigsOut(BaseModel):
    gigs: list[GigOut]
    years: list[GigYear] = Field(description="Gigs per year (all of them, for the filter).")
    total: int


class GigFromLiveIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=20, description="The Live gig's rows.")


class GigImportOut(BaseModel):
    added: int
    updated: int = Field(description="Imported before: brought up to date.")
    skipped: int = Field(description="Already there (added another way).")
    problems: list[str] = Field(description="Rows that couldn't be read (the first few).")


class SetlistFmImportIn(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.\-]+$")


class SetlistFmKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9\-]{20,64}$")


class SetlistFmKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None


class GigTopArtist(BaseModel):
    name: str
    gigs: int
    server_id: int | None
    rating_key: int | None


class GigTopVenue(BaseModel):
    name: str
    city: str | None
    gigs: int


class GigStatsOut(BaseModel):
    total: int
    artists: int = Field(description="Different artists seen.")
    venues: int = Field(description="Different venues.")
    first: dt.date | None
    last: dt.date | None
    top_artists: list[GigTopArtist]
    top_venues: list[GigTopVenue]
    years: list[GigYear] = Field(description="Every year from the first gig's to the last's.")
    months: list[int] = Field(description="Gigs in each month, January first.")
    seasons: dict[str, int] = Field(description="winter, spring, summer, autumn (by hemisphere).")


ScrobbleService = Literal["maloja", "koito", "multi-scrobbler", "listenbrainz-other", "librefm"]


class ScrobbleTargetIn(BaseModel):
    service: ScrobbleService
    url: str | None = Field(default=None, max_length=500, description="The server's address.")
    token: str | None = Field(default=None, max_length=500, description="ListenBrainz-compatible.")
    username: str | None = Field(default=None, max_length=100, description="Libre.fm.")
    password: str | None = Field(
        default=None, max_length=200, description="Libre.fm: used once to sign in, not kept."
    )


class ScrobbleTargetOut(BaseModel):
    id: int
    service: ScrobbleService
    label: str
    url: str
    username: str | None
    enabled_at: dt.datetime
    sent: int
    pending: int
    last_submitted_at: dt.datetime | None
    paused: bool
    error: str | None


class PlaylistImportMatch(BaseModel):
    rating_key: int
    title: str
    artist: str | None
    album: str | None
    duration_ms: int | None
    how: Literal["exact", "title"] = Field(
        description="exact: title and artist; title: only a lone same-title song of that length."
    )


class PlaylistImportSong(BaseModel):
    index: int
    title: str
    artist: str
    album: str | None
    duration_ms: int | None
    match: PlaylistImportMatch | None


class PlaylistImportPreviewOut(BaseModel):
    name: str | None
    total: int
    matched: int
    songs: list[PlaylistImportSong]


class PlaylistImportIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rating_keys: list[int] = Field(min_length=1, max_length=5000)


class PlaylistImportOut(BaseModel):
    plex_playlist_key: int
    songs: int


class SpotifyAppIn(BaseModel):
    client_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{20,64}$")
    client_secret: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{20,64}$")


class SpotifyAppOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None
    client_id: str | None = Field(description="Not secret: shown so the app can be recognised.")


class SpotifyImportIn(BaseModel):
    url: str = Field(min_length=10, max_length=500)


class ConnectionStatus(BaseModel):
    name: str
    group: Literal["yours", "instance"]
    state: Literal["ok", "warning", "error", "off"] = Field(
        description="off: not set up; warning: working, but look (nearly at a limit)."
    )
    detail: str | None = None
    link: str | None = Field(default=None, description="Where it's set up or fixed.")
