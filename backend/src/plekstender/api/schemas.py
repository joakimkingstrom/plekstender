"""Request/response models for the HTTP API (these also define the frontend's types)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator

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


BestCoverSource = Literal["deezer", "itunes", "tidal", "coverartarchive"]
CoverTab = Literal[
    "deezer", "itunes", "coverartarchive", "tidal", "fanart", "plex", "search", "upload"
]  # fmt: skip
BEST_COVER_SOURCES: tuple[BestCoverSource, ...] = ("deezer", "itunes", "tidal", "coverartarchive")


ReleaseKind = Literal["album", "ep", "single", "live", "compilation", "other"]


def _default_release_kinds() -> list[ReleaseKind]:
    return ["album", "ep"]


class ReleaseSettings(BaseModel):
    """Library -> Releases, per account."""

    kinds: list[ReleaseKind] = Field(
        default_factory=_default_release_kinds, max_length=6, description="Kinds shown."
    )
    follow_rating: int = Field(
        default=8,
        ge=0,
        le=10,
        description="Follow artists rated this or more (0-10, half stars = 1); 0: not by rating.",
    )
    follow_top: int = Field(
        default=50, ge=0, le=5000, description="Follow your N most played artists; 0: none."
    )


class GigPlaylistSettings(BaseModel):
    """Playlists made for gigs (in Plex), per account."""

    before: bool = Field(
        default=False,
        description="Before a starred gig: the songs the artists have been playing lately.",
    )
    after: bool = Field(default=False, description="After a gig you went to: its setlist.")
    delete_after: int = Field(
        default=0,
        ge=0,
        le=365,
        description="Delete gig playlists this many units after the gig; 0: keep them.",
    )
    delete_unit: Literal["days", "weeks", "months"] = Field(
        default="weeks", description="The unit of delete_after."
    )


class GigPlaylistOut(BaseModel):
    kind: Literal["before", "after"]
    gig_date: dt.date
    songs: int = Field(description="Songs found in the library (0: none, so no playlist).")
    playlist_id: int | None = Field(description="None: none made, or deleted since.")
    name: str | None
    server_id: int | None = Field(description="The server (your connection) it's on.")
    status: str | None


class GigPlaylistsOut(BaseModel):
    setlistfm: bool = Field(description="setlist.fm is set up (an admin's key): needed.")
    items: list[GigPlaylistOut] = Field(description="The latest, newest gig first.")


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
        description=(
            "A play counts as skipped when less than this % of the track was heard and "
            "another track started straight after."
        ),
    )
    phone_start: Literal["rate", "home"] = Field(
        default="rate", description="The page the app opens on, on a phone."
    )
    game: GameSettings = Field(default_factory=GameSettings)
    releases: ReleaseSettings = Field(default_factory=ReleaseSettings)
    gig_playlists: GigPlaylistSettings = Field(default_factory=GigPlaylistSettings)
    theme: Literal["dark", "light"] | None = Field(
        default=None, description="Light or dark on every device; None: each browser's own choice."
    )
    achievements: bool = Field(default=True, description="Achievements and their pop-ups.")
    lyrics_icons: bool = Field(
        default=False,
        description="Small icons on song and album rows: what lyrics their files have.",
    )
    instance_notifications: bool = Field(
        default=True, description="Admins: notifications about this Plekstender's services."
    )
    cover_source: Literal[
        "deezer", "itunes", "coverartarchive", "tidal", "fanart", "plex", "search"
    ] = Field(default="deezer", description="Album covers: the source an album opens on.")
    best_cover_sources: list[BestCoverSource] = Field(
        default_factory=lambda: list(BEST_COVER_SOURCES),
        min_length=1,
        max_length=4,
        description="Album covers: where a best match may come from.",
    )
    cover_tabs: list[CoverTab] = Field(
        default_factory=list,
        max_length=8,
        description="Album covers: the sources' tabs in this order (others after them).",
    )
    square_covers: bool = Field(
        default=False,
        description="Album covers: crop a cover chosen by hand to a square, unless unticked "
        "(best matches always are).",
    )


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    thumb_url: str | None
    is_admin: bool
    preferences: Preferences = Field(default_factory=Preferences)


class PinStartIn(BaseModel):
    setup_code: str | None = Field(
        default=None,
        max_length=64,
        description="While nobody has signed in: the setup code from the web app's log.",
    )


class PinStartOut(BaseModel):
    auth_url: str


class SetupOut(BaseModel):
    claimed: bool = Field(description="Someone has signed in (so no setup code is needed).")


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
    version: str = Field(description="e.g. 0.1.212 for a published image; ends in -dev otherwise.")
    commit: str | None = Field(description="The commit the image was built from, if known.")
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
    owner_full_sync_at: dt.datetime | None = Field(
        description="On a server someone else owns: their last full sync, the kind that clears "
        "out what was removed from Plex (null for the owner, who runs those)."
    )
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


class SeenLiveArtist(BaseModel):
    rating_key: int
    title: str
    thumb: str | None
    tracks: int = Field(description="Songs by them in the library.")
    seen: int = Field(description="Of those, songs played at gigs the person went to.")


class SeenLiveOut(BaseModel):
    tracks: int = Field(description="Songs in the library.")
    seen: int = Field(description="Songs in the library played at gigs the person went to.")
    setlists: int = Field(description="Gigs with a setlist (what this is worked out from).")
    gigs: int = Field(description="Gigs the person went to.")
    artists_total: int = Field(description="Artists in the library.")
    artists_seen: int = Field(description="Of those, artists on the bill of a gig they went to.")
    artists: list[SeenLiveArtist] = Field(
        description="The highest share of their songs seen live (artists with at least "
        "a few songs in the library)."
    )


class ArtistPage(BaseModel):
    items: list[ArtistSummary]
    total: int
    offset: int
    limit: int


class AlbumLyricsOut(BaseModel):
    songs: int = Field(description="Songs on the album.")
    with_lyrics: int = Field(
        description="Of those, songs whose files have lyrics (or are marked instrumental)."
    )


LyricsKind = Literal["word", "synced", "plain", "instrumental", "none"]


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
    lyrics: AlbumLyricsOut | None = Field(
        default=None,
        description="How many of its songs have lyrics in their files; None: the lyrics icons "
        "preference is off, or none of its files have been read.",
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


class CoverOf(BaseModel):
    artist: str | None
    title: str | None
    url: str | None = Field(description="The original on the source.")
    cover_url: str | None = Field(description="This recording on the source.")
    source: str = Field(description="secondhandsongs | musicbrainz")


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
    seen_live: int = Field(default=0, description="Gigs you've been to where it was played.")
    cover_of: CoverOf | None = Field(
        default=None, description="Whose song it is, when it's a cover (SecondHandSongs)."
    )
    lyrics: LyricsKind | None = Field(
        default=None,
        description="The best lyrics its files have (word-synced, synced, plain, instrumental "
        "or none); None: the lyrics icons preference is off, or its files haven't been read.",
    )


class SongVersion(BaseModel):
    rating_key: int
    title: str
    album_rating_key: int | None
    album_title: str | None
    album_thumb: str | None
    year: int | None
    release_type: str | None = Field(description="Album, Live, Compilation… (its album's).")
    duration_ms: int | None
    user_rating: float | None
    plays: int
    current: bool = Field(description="The song the dialog was opened from.")


class SongVersionsOut(BaseModel):
    title: str
    artist: str | None
    versions: list[SongVersion] = Field(description="Oldest release first.")


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
    country: str | None = Field(
        default=None,
        description="Where they're from: an ISO 3166 code (Plex's, else MusicBrainz's).",
    )
    tags: list[TagOut]
    similar: list[SimilarArtistOut]
    albums: list[AlbumSummary]
    top_tracks: list[TrackOut]
    your_listens: ItemListensOut


class LiveSource(BaseModel):
    """A studio release a live album's songs first came out on."""

    rating_key: int
    title: str
    year: int | None
    thumb: str | None
    release_type: str
    colour: str | None = Field(description="Its cover's main colour (#rrggbb), when known.")
    songs: int = Field(description="Songs on the live album first released on it.")


class AlbumDetail(AlbumSummary):
    summary: str | None
    released: dt.date | None
    studio: str | None
    artist_rating_key: int | None
    artist_title: str | None
    tags: list[TagOut]
    tracks: list[TrackOut]
    your_listens: ItemListensOut
    live_sources: list[LiveSource] | None = Field(
        default=None,
        description="Live albums: the studio releases their songs first came out on, oldest "
        "first; None for other albums.",
    )
    live_unmatched: int = Field(
        default=0, description="Live albums: songs not found on any of the artist's releases."
    )


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
    stop_requested: Literal["stop", "cancel"] | None = Field(
        default=None, description="Asked to stop (or cancel): it does so at its next item."
    )


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
    scrobbling: bool = Field(description="The shared secret is set too, so people can scrobble.")
    note: str | None = Field(
        default=None, description="On saving: something to know (it can't be used to import)."
    )


class LastFmKeyIn(BaseModel):
    """A key (with or without its shared secret), a secret for the key already there, or
    neither to clear both."""

    api_key: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{32}$")
    shared_secret: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{32}$")


class LastFmScrobblingOut(BaseModel):
    available: bool = Field(description="An admin has set the Last.fm API key and its secret.")


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
    order: Literal["weighted", "plays", "popular", "known", "obscure"] = "weighted"
    exclude: list[int] = Field(default_factory=list, max_length=5000)
    limit: int = Field(default=20, ge=1, le=50)
    tag_id: int | None = Field(default=None, description="Only items with this genre (tag).")


class RateItemOut(BaseModel):
    rating_key: int
    kind: RateKind
    title: str
    subtitle: str | None = Field(description="Artist, for songs and albums.")
    artist_key: int | None = Field(
        default=None, description="The artist's rating key (songs and albums; artists: itself)."
    )
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
    folder_saved_at: dt.datetime | None = Field(
        default=None, description="When it was saved there."
    )
    folder_error: str | None = Field(
        default=None, description="Why artist.jpg wasn't saved, the last time."
    )
    folder_queued: bool = Field(
        default=False, description="Apply: artist.jpg is being written by the worker."
    )
    added_at: dt.datetime | None = Field(default=None, description="Added to Plex.")


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
    up_to: int | None = Field(
        default=None, description="Size not known: at most this many pixels a side."
    )


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


class CoverSourcesSettings(BaseModel):
    """The album cover sources that need nothing set up: on (the default) or off."""

    deezer: bool = True
    itunes: bool = True
    coverartarchive: bool = True


class FanartKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None


class FanartKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{32}$")


class AcoustIdKeyOut(BaseModel):
    configured: bool
    source: Literal["settings", "environment"] | None


AlbumCoverState = Literal["missing", "low", "ok", "unchecked"]
AlbumCoverFilter = Literal["needs", "missing", "low", "all"]
# Under how many pixels (on its shorter side) a cover counts as low resolution.
LOW_RESOLUTIONS = (600, 1000, 1500)
# name: by artist then album; added: newest in Plex first; changed: the covers changed here
# most recently first; smallest: no cover first, then the smallest covers (the default,
# except for "all", which is by name).
AlbumCoverSort = Literal["name", "added", "changed", "smallest"]


class AlbumCoverOut(BaseModel):
    rating_key: int
    title: str
    artist: str | None
    year: int | None
    thumb: str | None
    width: int | None = Field(description="The cover's size in Plex, once measured.")
    height: int | None
    state: AlbumCoverState = Field(
        description="missing: no cover (or one that can't be read); low: under low_px (600 by "
        "default) on its "
        "shorter side; unchecked: not measured yet."
    )
    kept: bool = Field(description="Marked as fine as it is: left out of the lists to improve.")
    source: str | None
    applied_at: dt.datetime | None
    added_at: dt.datetime | None = Field(default=None, description="Added to Plex.")


class AlbumCoverPage(BaseModel):
    items: list[AlbumCoverOut]
    total: int
    counts: dict[str, int] = Field(
        description="missing and low (not counting kept ones), ok, unchecked, kept."
    )


class AlbumBestOut(BaseModel):
    outcome: Literal["applied", "not_found", "already"] = Field(
        description="applied: saved to Plex; not_found: no cover of this album at least "
        "1000 x 1000 (and square); already: Plex's cover is at least as big."
    )
    cover: AlbumCoverOut | None = Field(description="The album now (applied, already).")
    source: str | None = Field(description="Where the best match is from.")
    width: int | None
    height: int | None
    problems: list[str] = Field(description="Sources that couldn't be asked, and why.")


class AlbumCoverKeepIn(BaseModel):
    kept: bool


class TidalOut(BaseModel):
    configured: bool
    client_id: str | None


class TidalIn(BaseModel):
    """Both to set, neither to remove."""

    client_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{8,64}$")
    client_secret: str | None = Field(default=None, min_length=8, max_length=200)


class MusicBrainzContactOut(BaseModel):
    contact: str | None = Field(description="What MusicBrainz is told (none: no contact).")
    source: Literal["settings", "environment"] | None


class MusicBrainzContactIn(BaseModel):
    # An email address or a web page; nothing that could break out of a header.
    contact: str | None = Field(
        default=None,
        max_length=200,
        pattern=r"^(?:[^\s@()]+@[^\s@()]+\.[^\s@()]+|https?://[^\s()]+)$",
    )


class AcoustIdKeyIn(BaseModel):
    api_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{6,64}$")


class ArtworkJobOut(BaseModel):
    id: int
    kind: Literal["fanart", "text", "export", "import", "covers"]
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: dict[str, Any]
    stats: dict[str, Any]
    error: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None
    download: str | None = Field(default=None, description="Export: where to download it.")
    stop_requested: Literal["stop", "cancel"] | None = Field(
        default=None, description="Asked to stop (or cancel): it does so at its next item."
    )


class BestCoversJobIn(BaseModel):
    show: Literal["needs", "missing", "low"] = Field(
        default="needs", description="The albums listed with this filter (and search)."
    )
    q: str | None = Field(default=None, max_length=200)
    low_px: int = Field(
        default=600, description="Low resolution: under this many pixels (600, 1000 or 1500)."
    )
    sources: list[BestCoverSource] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
        description="Where matches may come from; None: the user's preference.",
    )


class FanartJobIn(BaseModel):
    rating_keys: list[int] | None = Field(default=None, max_length=10000)
    status: Literal["none", "custom", "done", "all"] = Field(
        default="none", description="Without rating_keys: every artist with this status."
    )
    q: str | None = Field(
        default=None, max_length=200, description="Without rating_keys: names containing this."
    )


class TextJobIn(FanartJobIn):
    """Text logos for many artists: each artist's name, in one font and colour, or in one
    picked at random (per artist) from several."""

    fonts: list[str] = Field(min_length=1, max_length=40)
    colours: list[str] = Field(min_length=1, max_length=12)
    case: Literal["none", "upper", "lower"] = "none"

    @field_validator("colours")
    @classmethod
    def _colours(cls, value: list[str]) -> list[str]:
        for colour in value:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
                raise ValueError(f"not a colour: {colour!r}")
        return value


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


class SkipRateOut(BaseModel):
    threshold: int = Field(description="Percent heard below which a play counts as skipped.")
    watching: bool = Field(description="Whether any plays have been seen live yet.")
    skips: int
    plays: int = Field(description="Plex plays seen live by the session watcher in the period.")
    min_plays: int = Field(description="Plays an artist needs to be in `artists`.")
    artists: list[SkippedItemOut] = Field(description="Highest share of plays skipped first.")


# --- looking back (on this day, taste over time, rediscover) -----------------------------


class DayArtistOut(BaseModel):
    name: str
    listens: int
    rating_key: int | None = Field(description="The library artist, when one matches.")
    thumb: str | None


class DayTrackOut(BaseModel):
    name: str
    artist: str
    listens: int


class DayYearOut(BaseModel):
    year: int
    listens: int
    artists: list[DayArtistOut] = Field(description="The day's top 3.")
    tracks: list[DayTrackOut] = Field(description="The day's top 5.")
    album: str | None = Field(description="The day's most played album.")
    album_rating_key: int | None
    album_thumb: str | None


class OnThisDayOut(BaseModel):
    date: dt.date = Field(description="Today, in the time zone asked for.")
    years: list[DayYearOut] = Field(description="Earlier years with listens on this date.")


class ShareOut(BaseModel):
    name: str
    shares: list[float] = Field(description="Per year in `years`: the share of matched listens.")


class TasteOut(BaseModel):
    years: list[int]
    listens: list[int] = Field(description="All listens per year.")
    genres: list[ShareOut] = Field(
        description="The most played genres (or styles, or moods: see `kind`) overall."
    )
    genre_matched: list[int] = Field(
        description="Listens per year whose artist has a genre (or style, or mood)."
    )
    decades: list[ShareOut] = Field(description="When the music played was released.")
    decade_matched: list[int] = Field(description="Listens per year with a known release year.")
    median_release: list[int | None] = Field(description="Median release year per year.")


class RediscoverSongOut(BaseModel):
    rating_key: int
    title: str
    artist: str | None
    album: str | None
    album_rating_key: int | None
    thumb: str | None
    rating: float | None
    plays: int
    last_played_at: dt.datetime | None
    hidden: bool = Field(description="Hidden from Rediscover (only listed when asked for).")


class RediscoverListOut(BaseModel):
    total: int
    items: list[RediscoverSongOut]


class RediscoverOut(BaseModel):
    forgotten: RediscoverListOut = Field(description="Loved, but not played for a year.")
    unplayed: RediscoverListOut = Field(description="Loved, but hardly ever played.")
    disliked: RediscoverListOut = Field(description="Played a lot, but rated low.")
    loved: int = Field(description="Ratings from this (0-10) count as loved.")
    disliked_below: int = Field(description="Ratings up to this (0-10) count as low.")
    forgotten_days: int
    hardly_played: int = Field(description="At most this many plays is hardly played.")
    played_a_lot: int = Field(description="At least this many plays is a lot.")


# --- releases (MusicBrainz) ----------------------------------------------------------------


class ReleaseOut(BaseModel):
    mbid: str = Field(description="MusicBrainz release group ID.")
    title: str
    kind: ReleaseKind
    first_release_date: str | None = Field(description='"2019", "2019-05" or "2019-05-21".')
    in_library: bool
    hidden: bool


class ReleaseArtistOut(BaseModel):
    rating_key: int
    name: str
    thumb: str | None


class NewReleaseOut(ReleaseOut):
    artist: ReleaseArtistOut
    upcoming: bool = Field(description="Not out yet.")


class NewReleasesOut(BaseModel):
    items: list[NewReleaseOut] = Field(description="Newest first; coming ones first of all.")
    following: int = Field(description="How many of your artists count as followed.")
    following_with_mbid: int = Field(
        description="Of those, the ones Plex has a MusicBrainz ID for (only they can be read)."
    )
    following_read: int = Field(
        description="Of those, the ones whose releases have been read from MusicBrainz so far."
    )
    settings: ReleaseSettings = Field(description="Your settings these were found with.")
    new_days: int = Field(description="Releases from this many days back count as new.")


class MissingArtistOut(ReleaseArtistOut):
    releases: list[ReleaseOut]


class ReleaseCoverageOut(BaseModel):
    artists: int = Field(description="Your library's artists.")
    with_mbid: int = Field(description="Those Plex has a MusicBrainz ID for.")
    checked: int = Field(description="Those whose releases have been read from MusicBrainz.")


class MissingReleasesOut(BaseModel):
    artists: list[MissingArtistOut]
    total_artists: int = Field(description="Artists with anything missing (all pages).")
    coverage: ReleaseCoverageOut


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
    stop_requested: Literal["stop", "cancel"] | None = Field(
        default=None, description="Asked to stop (or cancel): it does so at its next item."
    )


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


class DupeFileOut(BaseModel):
    file_id: int
    path: str
    format: str | None
    size: int
    duration_ms: int | None
    kbps: int | None = Field(description="Average bitrate.")
    rating_key: int | None = Field(description="The Plex track, if Plex has it.")
    title: str | None
    artist: str | None
    album: str | None
    sidecars: list[str] = Field(description="Lyrics files next to it (moved with it).")
    keep: bool = Field(description="The suggested copy to keep.")


class DupeGroupOut(BaseModel):
    confirmed: bool = Field(description="Compared by sound: they're the same recording.")
    similarity: float | None = Field(description="How alike the least alike pair sounds (0-1).")
    files: list[DupeFileOut]


class DuplicatesOut(BaseModel):
    job: FileScanOut
    groups: list[DupeGroupOut]
    total_groups: int
    files: int = Field(description="Files checked.")
    fingerprint_error: str | None = Field(description="Why files couldn't be compared by sound.")
    applied_set_id: int | None


class DuplicatesApplyIn(BaseModel):
    remove: list[int] = Field(min_length=1, max_length=5000, description="Files to take out.")


class LyricsApplyIn(BaseModel):
    sidecar: bool = Field(default=True, description="A .lrc (synced) or .txt next to each song.")
    lyricsfile: bool = Field(default=False, description="A .lyricsfile.yaml next to each song.")
    embedded: bool = Field(default=False, description="The plain lyrics in each song's tags.")
    overwrite: bool = Field(default=False, description="Replace lyrics the files already have.")


class LyricsLookupIn(BaseModel):
    albums: list[int] = Field(
        default_factory=list, max_length=1000, description="These albums; none: the library."
    )
    mode: Literal["new", "missing", "unsynced", "all"] | None = Field(
        default=None,
        description="new: songs not looked up before; missing: songs without lyrics; "
        "unsynced: songs without synced lyrics; all: every song. Left out: from `again`.",
    )
    again: bool = Field(default=False, description="Old form of mode=all (False: mode=new).")
    write: LyricsApplyIn | None = Field(
        default=None,
        description="Write lyrics as they're found (one edit), instead of after the lookup.",
    )


class LyricsSampleOut(BaseModel):
    path: str
    status: str
    lrc: bool = Field(description="A .lrc (synced lyrics) is written next to it.")
    txt: bool = Field(description="A .txt (plain lyrics) is written next to it.")
    lyricsfile: bool = Field(description="A .lyricsfile.yaml is written next to it.")
    word_synced: bool = Field(description="The lyrics found time each word.")
    lyrics: bool = Field(description="The lyrics are written into its tags.")
    had_lyrics: bool = Field(description="It had lyrics already (replaced when overwriting).")
    preview: str | None = Field(description="The start of the lyrics.")


class LyricsPlanOut(BaseModel):
    job: FileScanOut
    files: int = Field(description="Files the job covers.")
    looked_up: int
    synced: int
    word_synced: int = Field(description="Of the synced ones, those timed word by word.")
    plain: int = Field(description="Found without times.")
    instrumental: int
    none: int = Field(description="LRCLIB has nothing for them.")
    untagged: int = Field(description="No artist or title to look up.")
    errors: int
    had_lyrics: int = Field(description="Files that already had lyrics somewhere.")
    lrc: int = Field(description=".lrc files that writing would make or replace.")
    txt: int
    lyricsfile: int = Field(description=".lyricsfile.yaml files writing would make or replace.")
    lyrics: int = Field(description="Files whose tags would get lyrics.")
    songs: int = Field(description="Files that writing would change.")
    sample: list[LyricsSampleOut] = Field(description="The first 100 of them.")
    applied_set_id: int | None


class JobStopIn(BaseModel):
    cancel: bool = Field(
        default=False,
        description="Also take back what it did, where that's possible (else it's kept).",
    )


class JobStopOut(BaseModel):
    status: str
    error: str | None
    stop_requested: Literal["stop", "cancel"] | None


class LyricsStopIn(BaseModel):
    cancel: bool = Field(
        default=False, description="Throw away the lyrics it found (else they're kept)."
    )
    undo: bool = Field(
        default=True, description="When cancelling: take out the lyrics it wrote too."
    )


class LyricsCoverageOut(BaseModel):
    files: int = Field(description="Music files in the folder.")
    word: int = Field(description="With lyrics timed word by word.")
    synced: int = Field(description="With lyrics timed line by line.")
    plain: int = Field(description="With lyrics without times.")
    instrumental: int = Field(description="Marked as instrumental (a Lyricsfile says so).")
    none: int = Field(description="Without lyrics.")
    unchecked: int = Field(description="Not looked at since lyrics were counted (scan again).")


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
    stop_requested: Literal["stop", "cancel"] | None = Field(
        default=None, description="Asked to stop (or cancel): it does so at its next item."
    )


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
    "originaldate", "genre", "releasecountry", "musicbrainz_trackid", "musicbrainz_releasetrackid",
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
    duration: int | None = Field(description="Seconds.")
    releases: list[IdentityRelease]

    @field_validator("duration", mode="before")
    @classmethod
    def _whole_seconds(cls, value: object) -> object:
        """AcoustID gives some lengths with a fraction (133.426); results kept from before
        rounding was added still have them."""
        return round(value) if isinstance(value, float) else value


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
    playlist_id: int | None = Field(
        default=None, description="Its before-the-gig playlist, when there is one."
    )


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
    exists: bool = Field(description="The user has a calendar address.")
    created_at: dt.datetime | None = Field(description="When it was made (unknown for older ones).")
    url: str | None = Field(
        default=None,
        description="The private address (iCal): only in the answer that makes it (not stored).",
    )


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


class GigSong(BaseModel):
    name: str = Field(max_length=300)
    cover: str | None = Field(default=None, max_length=300, description="Whose song it is.")
    tape: bool = Field(default=False, description="Played from a recording, not live.")


class GigSetlist(BaseModel):
    artist: str = Field(max_length=300)
    url: str | None = Field(default=None, max_length=2000)
    songs: list[GigSong] = Field(max_length=150)


class GigPlacePick(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class GigPlaceFound(BaseModel):
    name: str
    address: str
    kind: str = Field(description="OpenStreetMap's type for it, e.g. theatre, nightclub.")
    latitude: float
    longitude: float


class GigSameVenueOut(BaseModel):
    gigs: int = Field(description="The person's other gigs at this venue.")


class GigIn(BaseModel):
    date: dt.date
    artists: list[str] = Field(min_length=1, max_length=50)
    name: str | None = Field(default=None, max_length=300)
    venue: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    notes: str | None = Field(default=None, max_length=4000)
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")
    setlists: list[GigSetlist] = Field(default_factory=list, max_length=50)
    place: GigPlacePick | None = Field(
        default=None,
        description="A place picked by hand. Kept until the venue, town or country change.",
    )
    place_others: bool = Field(
        default=False,
        description="Put the person's other gigs at the same venue at the picked place too.",
    )
    rename_others: bool = Field(
        default=False,
        description="Editing: when the venue, town or country changed, change them on the "
        "person's other gigs at the old venue too.",
    )
    find_place: bool = Field(
        default=False, description="Forget a place picked by hand and look it up again."
    )


class GigOut(BaseModel):
    id: int
    date: dt.date
    name: str | None
    venue: str | None
    city: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    place_precision: Literal["venue", "city", "manual"] | None = Field(
        description="venue: found; city: only the town was found; manual: picked by hand."
    )
    notes: str | None
    artists: list[GigArtistOut]
    setlists: list[GigSetlist] = []
    source: str
    url: str | None


class SetlistLookupIn(BaseModel):
    url: str = Field(max_length=2000, pattern=r"^https?://")


class SetlistLookupOut(BaseModel):
    """A gig as setlist.fm has it, to fill in the form."""

    date: dt.date
    name: str | None
    venue: str | None
    city: str | None
    country: str | None
    artists: list[str]
    url: str
    setlists: list[GigSetlist]


class SetlistSearchItem(BaseModel):
    url: str
    date: dt.date
    artist: str
    venue: str | None
    city: str | None
    country: str | None
    tour: str | None
    songs: int = Field(description="Songs in the setlist (0 when nobody's added them).")


class SetlistSearchOut(BaseModel):
    items: list[SetlistSearchItem]
    total: int
    page: int


class GigYear(BaseModel):
    year: int
    gigs: int


class GigPlace(BaseModel):
    """A place gigs were at (for the country, city and venue filters)."""

    country: str | None
    city: str | None
    venue: str | None
    gigs: int


class GigsOut(BaseModel):
    gigs: list[GigOut]
    years: list[GigYear] = Field(description="Gigs per year (all of them, for the filter).")
    total: int
    places: list[GigPlace] = Field(
        default=[], description="Every country / city / venue combination (all gigs)."
    )
    unplaced: int = Field(
        default=0, description="Gigs not pinned at their venue on the map (all gigs)."
    )


class GigFromLiveIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=20, description="The Live gig's rows.")


class GigImportOut(BaseModel):
    added: int
    updated: int = Field(description="Imported before: brought up to date.")
    skipped: int = Field(description="Already there (added another way).")
    problems: list[str] = Field(description="Rows that couldn't be read (the first few).")


class SetlistFmImportIn(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.\-]+$")


class CoverSongsIn(BaseModel):
    enabled: bool = Field(description="Look library songs up for covers.")
    secondhandsongs: bool = Field(default=True, description="Ask SecondHandSongs.")
    musicbrainz: bool = Field(
        default=True, description="Ask MusicBrainz (songs with a recording MBID)."
    )
    api_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9\-]{20,64}$",
        description="A new SecondHandSongs API key (checked first); None keeps the one saved.",
    )
    remove_key: bool = Field(default=False, description="Forget the saved API key.")


class CoverSongsOut(BaseModel):
    enabled: bool
    secondhandsongs: bool
    musicbrainz: bool
    api_key: bool = Field(description="A SecondHandSongs API key is saved (higher limits).")
    per_hour: int = Field(description="SecondHandSongs requests an hour Plekstender allows.")
    per_day: int = Field(description="And a day.")
    songs: int = Field(description="Different songs in the libraries.")
    checked: int = Field(description="Of those, looked up.")
    covers: int = Field(description="Of those, covers.")
    requests_left: int = Field(
        description="About how many SecondHandSongs requests looking up the rest takes."
    )


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


ScrobbleService = Literal[
    "maloja", "koito", "multi-scrobbler", "listenbrainz-other", "librefm", "lastfm"
]


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
    """A playlist's link (Spotify or Tidal)."""

    url: str = Field(min_length=10, max_length=500)


class ConnectionStatus(BaseModel):
    name: str
    group: Literal["yours", "instance"]
    state: Literal["ok", "warning", "error", "off"] = Field(
        description="off: not set up; warning: working, but look (nearly at a limit)."
    )
    subtitle: str | None = Field(
        default=None, description="Which one: the server's, account's or calendar's name."
    )
    detail: str | None = None
    usage: float | None = Field(
        default=None, description="Share of a request limit used (0-1), for admins."
    )
    link: str | None = Field(default=None, description="Where it's set up or fixed.")


class AlbumIssues(BaseModel):
    """An album with scanned files, and how many of them need a look, per kind."""

    rating_key: int
    title: str
    year: int | None
    thumb: str | None
    artist_rating_key: int | None
    artist_title: str | None
    files: int
    differs: int = Field(description="Files whose tags differ from Plex's metadata.")
    differing_fields: list[str] = Field(description="Which fields differ, in any file.")
    no_mbid: int = Field(description="Files without a MusicBrainz release ID.")
    missing: int = Field(description="Files without a title, artist, album or track number.")
    errors: int = Field(description="Files whose tags couldn't be read.")
    skipped_at: dt.datetime | None = Field(
        default=None, description="When you last skipped it in a MusicBrainz lookup."
    )


class AlbumIssuesPage(BaseModel):
    items: list[AlbumIssues]
    total: int
    offset: int
    limit: int
    albums: int = Field(description="Albums with scanned files.")
    counts: dict[str, int] = Field(description="Albums with each kind of issue (and any).")
    not_in_plex: int = Field(description="Readable files that aren't a Plex track.")
    skipped_hidden: int = Field(
        default=0, description="Albums with this filter left out as skipped lately."
    )


class MbBestOut(BaseModel):
    """MusicBrainz's most likely release for an album's files."""

    release: MbReleaseSummary | None
    source: Literal["tags", "sound", "search"] | None = Field(
        description="Found by the release ID in the files, by sound, or by searching."
    )
    files: int
    matched: int = Field(description="Files matched to one of the release's tracks.")
    changes: int = Field(description="Tags that writing it would change.")
    clear: bool = Field(description="Sure enough to accept with the other clear matches.")
    note: str | None = Field(description="Why it isn't a clear match.")


class GrowthPoint(BaseModel):
    month: str = Field(description='"2019-03"')
    artists: int
    albums: int
    tracks: int


class DecadeCount(BaseModel):
    decade: int = Field(description="The decade's first year (or the year, or the month 0-12).")
    albums: int


class FileTypeCount(BaseModel):
    format: str = Field(description='The file extension, lower case ("flac"); "" when unknown.')
    tracks: int
    bytes: int = Field(description="The files' size, of those whose size is known.")
    sized: int = Field(description="Songs whose file size is known (Plex's, or the scan's).")


class LibraryStatsOut(BaseModel):
    artists: int
    albums: int
    tracks: int
    growth: list[GrowthPoint] = Field(
        description="Running totals at the end of each month, by when items were added to Plex."
    )
    decades: list[DecadeCount]
    undated_albums: int


class ReleasedAlbum(BaseModel):
    rating_key: int
    title: str
    thumb: str | None
    released: dt.date | None
    artist_title: str | None
    plays: int


class ReleasedOut(BaseModel):
    buckets: list[DecadeCount] = Field(
        description="A decade's years, or a year's months (0: only the year is known)."
    )
    albums: list[ReleasedAlbum] = Field(description="The year's albums (when a year is asked).")


class WordCount(BaseModel):
    word: str
    count: int


class CountryArtists(BaseModel):
    country_code: str
    artists: int


class ArtistCountriesOut(BaseModel):
    countries: list[CountryArtists]
    unknown: int = Field(description="Artists without a known country.")
    pending: int = Field(description="Of those, waiting for a MusicBrainz lookup.")


class ColourFamily(BaseModel):
    name: str = Field(description="red, orange, … pink, white, grey or black")
    albums: int
    hex: str = Field(description="A typical cover colour in the family (the middle one).")


class ColoursOut(BaseModel):
    families: list[ColourFamily]
    strip: list[str] = Field(description="Up to 360 cover colours, in spectrum order.")
    albums: int = Field(description="Albums whose cover colour is known.")
    waiting: int = Field(description="Covers not looked at yet (the worker does them).")


class AlbumColourOut(BaseModel):
    rating_key: int
    title: str
    thumb: str | None
    artist_title: str | None
    hex: str


class RecentItem(BaseModel):
    """An album or artist recently added to Plex."""

    rating_key: int
    title: str
    subtitle: str | None = Field(description="Albums: the artist.")
    thumb: str | None
    added_at: dt.datetime | None


class RewindItem(BaseModel):
    name: str
    artist: str | None
    listens: int
    listening_ms: int
    rating_key: int | None = Field(description="Library artist or album (songs: the album).")
    thumb: str | None
    colour: str | None = Field(description="The album cover's main colour, if known.")
    track_key: int | None = Field(default=None, description="Songs: the library song (to play).")


class RewindRepeat(BaseModel):
    name: str
    artist: str
    track_key: int | None = Field(default=None, description="The library song (to play).")
    runs: int = Field(description="Times it was played two or more times in a row.")
    longest: int = Field(description="The most times in a row.")
    shortest: int = Field(description="The fewest times in a row (of those runs).")


class RewindGenreTime(BaseModel):
    when: str = Field(
        description='A weekday ("0" = Monday) or night / morning / afternoon / evening.'
    )
    genre: str
    share: float


class RewindGenre(BaseModel):
    genre: str
    listens: int


class RewindGame(BaseModel):
    rounds: int
    correct: int
    points: int
    best_streak: int
    days: int


class RewindOut(BaseModel):
    start: dt.date
    end: dt.date = Field(description="The last day (inclusive).")
    listens: int
    listening_ms: int
    days: int = Field(description="Days with listens.")
    period_days: int
    artists: int
    tracks: int
    top_artists: list[RewindItem]
    top_albums: list[RewindItem]
    top_tracks: list[RewindItem]
    new_artists: list[RewindItem] = Field(description="First played in the period, played a lot.")
    new_albums: list[RewindItem]
    new_releases: list[RewindItem] = Field(description="Released in the period.")
    weekdays: list[int] = Field(description="Listens per weekday, Monday first.")
    hours: list[int] = Field(description="Listens per hour of the day.")
    genres: list[RewindGenre]
    genre_days: list[RewindGenreTime]
    genre_parts: list[RewindGenreTime]
    repeats: list[RewindRepeat]
    longest_repeat: RewindRepeat | None
    game: RewindGame | None
    app_seconds: int | None = Field(description="Time in Plekstender (recorded since Nov 2026).")


class AppTimeIn(BaseModel):
    seconds: int = Field(ge=1, le=120, description="Time in use since the last report.")


class NotificationOut(BaseModel):
    id: int
    level: Literal["warning", "error", "info"] = Field(
        description="warning / error: something needs a look; info: news (new gigs)."
    )
    title: str
    detail: str | None
    link: str | None
    instance: bool = Field(description="About this Plekstender (for admins), not just you.")
    first_at: dt.datetime
    updated_at: dt.datetime
    resolved_at: dt.datetime | None = Field(description="Fixed (it went away by itself).")
    read: bool


class NotificationsOut(BaseModel):
    items: list[NotificationOut]
    unread: int


class NotificationsReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, description="Left out: all of them.")


class ProfileServer(BaseModel):
    name: str
    is_owner: bool


class ProfileOut(BaseModel):
    username: str
    email: str | None
    thumb_url: str | None
    is_admin: bool
    joined_at: dt.datetime
    servers: list[ProfileServer]
    scrobbles: int
    first_listen_at: dt.datetime | None
    ratings: int
    gigs: int


class AchievementOut(BaseModel):
    id: str
    name: str
    group: Literal["library", "rating", "game", "gigs", "connections", "listening"]
    message: str
    hint: str
    goal: int
    progress: int = Field(description="Where you are now (may be past the goal).")
    unlocked_at: dt.datetime | None
    seen: bool


class AchievementsOut(BaseModel):
    enabled: bool = Field(description="On for you (and for everyone, by the admins).")
    items: list[AchievementOut]


class AchievementsSeenIn(BaseModel):
    ids: list[str] = Field(max_length=100)


class AchievementsSettings(BaseModel):
    enabled: bool = Field(
        description="Achievements on for everyone (each person can still turn them off)."
    )


class SuggestionSourceOut(BaseModel):
    name: str
    rating_key: int


class SuggestionArtOut(BaseModel):
    """fanart.tv artwork of a top suggestion (kept by Plekstender): where to load each."""

    background: str | None
    banner: str | None
    logo: str | None


class ArtistSuggestionOut(BaseModel):
    key: str
    name: str
    similar_to: int = Field(description="Library artists Plex names it as similar to.")
    sources: list[SuggestionSourceOut] = Field(description="Some of them, most played first.")
    listens: int = Field(description="Your listens to it (scrobbles count too).")
    art: SuggestionArtOut | None = Field(
        default=None, description="The top suggestions' artwork, once looked up (if any)."
    )


class ArtistSuggestionsOut(BaseModel):
    items: list[ArtistSuggestionOut]
    total: int
    hidden: int = Field(description="How many you've hidden.")


class HideSuggestionIn(BaseModel):
    key: str = Field(max_length=300)
    name: str = Field(max_length=255)


# --- Lidarr -------------------------------------------------------------------------------

LidarrAccess = Literal["admins", "everyone"]


class LidarrAdminOut(BaseModel):
    configured: bool
    url: str | None = None
    root_folder: str | None = None
    quality_profile_id: int | None = None
    metadata_profile_id: int | None = None
    access: LidarrAccess = "admins"


class LidarrCheckIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(
        default=None, max_length=100, description="Left out: the saved one."
    )


class LidarrFolderOut(BaseModel):
    path: str
    free_space: int | None = None


class LidarrProfileOut(BaseModel):
    id: int
    name: str


class LidarrOptionsOut(BaseModel):
    """What the Lidarr at that address offers, to choose from."""

    version: str
    root_folders: list[LidarrFolderOut]
    quality_profiles: list[LidarrProfileOut]
    metadata_profiles: list[LidarrProfileOut]


class LidarrIn(LidarrCheckIn):
    root_folder: str = Field(min_length=1, max_length=500)
    quality_profile_id: int
    metadata_profile_id: int
    access: LidarrAccess = "admins"


class LidarrStatusOut(BaseModel):
    available: bool = Field(description="Lidarr is connected and you may use it.")


LidarrAlbumState = Literal["absent", "monitored", "unmonitored", "downloaded"]


class LidarrStatesIn(BaseModel):
    mbids: list[str] = Field(max_length=300, description="Release group MBIDs.")


class LidarrAlbumIn(BaseModel):
    mbid: str = Field(pattern=r"^[0-9a-f-]{36}$", description="The release group's MBID.")
    search: bool = Field(
        default=False,
        description="Have Lidarr search its indexers and download the best now (else it's "
        "only monitored: grabbed when it turns up, or chosen by hand).",
    )


class LidarrAlbumOut(BaseModel):
    id: int
    title: str
    state: LidarrAlbumState


class LidarrReleaseOut(BaseModel):
    guid: str
    indexer_id: int
    indexer: str | None
    title: str
    size: int | None
    quality: str | None
    protocol: str | None = Field(description="torrent or usenet.")
    seeders: int | None
    leechers: int | None
    age_days: int | None
    approved: bool = Field(description="Lidarr would take it (its profile allows it).")
    rejections: list[str]


class LidarrGrabIn(BaseModel):
    guid: str = Field(min_length=1, max_length=2000)
    indexer_id: int


class LidarrArtistOut(BaseModel):
    mbid: str
    name: str
    disambiguation: str | None
    kind: str | None = Field(description="Group, Person, ... as MusicBrainz says.")
    overview: str | None = Field(description="The start of Lidarr's biography.")
    in_lidarr: bool


class LidarrArtistIn(BaseModel):
    mbid: str = Field(pattern=r"^[0-9a-f-]{36}$")
    monitor: Literal["none", "future", "latest", "all"] = Field(
        default="future",
        description="Which albums Lidarr gets: none yet, new ones, the latest and new ones, "
        "or all.",
    )
    search: bool = Field(default=False, description="Look for the monitored albums now.")


class LidarrArtistAddedOut(BaseModel):
    id: int
    name: str


class GigShowPlaylistOut(BaseModel):
    playlist_id: int | None = Field(description="None: none of the setlists' songs were found.")
    songs: int
    missing: int = Field(description="Setlist songs not in the library.")


class ArtistNeighbourOut(BaseModel):
    rating_key: int
    title: str


class ArtistNeighboursOut(BaseModel):
    """The artists before and after one, in the library's order by name."""

    previous: ArtistNeighbourOut | None
    next: ArtistNeighbourOut | None


class SkipAlbumsIn(BaseModel):
    album_keys: list[int] = Field(min_length=1, max_length=500)


# --- Jobs (Settings → Jobs) -----------------------------------------------------------------


class JobWaitingOut(BaseModel):
    what: str = Field(description="The job ahead of it, or why it hasn't started.")
    since: dt.datetime | None = Field(description="When that job started.")


class JobOut(BaseModel):
    kind: Literal["sync", "artwork", "files", "edits", "imports"]
    id: int
    what: str
    subkind: str | None = Field(description="scan, identify, lyrics, fanart, lastfm...")
    status: str = Field(description="queued, running, succeeded, failed, applied, partial...")
    server_id: int | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    progress: dict[str, Any]
    error: str | None
    stop_requested: str | None
    stoppable: bool = Field(description="Can be stopped from the jobs page.")
    waiting_for: JobWaitingOut | None


class JobsOut(BaseModel):
    active: list[JobOut] = Field(description="Running, then waiting (oldest first).")
    finished: list[JobOut] = Field(description="The latest finished, newest first.")


# --- automatic actions for new music ---------------------------------------------------------


class NewMusicLyricsWrite(BaseModel):
    sidecar: bool = Field(default=True, description="A .lrc (synced) or .txt next to each song.")
    lyricsfile: bool = Field(default=False, description="A .lyricsfile.yaml next to each song.")
    embedded: bool = Field(default=False, description="The plain lyrics in each song's tags.")
    overwrite: bool = Field(default=False, description="Replace lyrics the files already have.")


class NewMusicIn(BaseModel):
    logos: bool = Field(description="fanart.tv's most liked logo for new artists.")
    covers: bool = Field(description="The best cover match for new albums.")
    lyrics: bool = Field(description="Look up lyrics for new albums' songs.")
    lyrics_write: NewMusicLyricsWrite | None = Field(
        default=None,
        description="Where found lyrics are written (tag editing on); none: only looked up.",
    )


class NewMusicOut(NewMusicIn):
    since: dt.datetime | None = Field(description="Added to Plex after this counts as new.")
    fanart_ready: bool = Field(description="fanart.tv has an API key (logos need one).")
    folder_ready: bool = Field(description="The music folder is set up (lyrics need it).")
    tag_editing: bool = Field(description="Tag editing is on (writing lyrics needs it).")
