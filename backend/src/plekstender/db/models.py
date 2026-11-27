"""Database models (SQLAlchemy 2.0, typed declarative)."""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dt.datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[str]: JSONB,
    }


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    plex_account_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    plex_uuid: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    thumb_url: Mapped[str | None] = mapped_column(Text)
    # plex.tv account token, encrypted (purpose "user.plex_token").
    plex_token_enc: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[dt.datetime | None]
    # Per-user settings (see api.schemas.Preferences), e.g. the accent colour.
    preferences: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    servers: Mapped[list[UserServer]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Session(Base):
    __tablename__ = "sessions"

    # SHA-256 of the session token; the token itself only lives in the user's cookie.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(index=True)
    user_agent: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[User] = relationship(back_populates="sessions")


class PlexServer(TimestampMixin, Base):
    __tablename__ = "plex_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_identifier: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    # Approved servers: in "servers" access mode, users need access to one of these.
    approved: Mapped[bool] = mapped_column(default=False)
    # Saving artist images as artist.jpg in the artist folders (owner setting): Plex's
    # path prefix maps to where the music is mounted in Plekstender's containers.
    folder_save: Mapped[bool] = mapped_column(default=False, server_default="false")
    plex_path_prefix: Mapped[str | None] = mapped_column(Text)
    local_path_prefix: Mapped[str | None] = mapped_column(Text)
    # The worker's last check of the folder mapping (only the worker sees music files):
    # {"requested_at", "checked_at", "root_ok", "problem", "example": {...}}.
    folder_check: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")
    # Tag editing writes to music files: off until the owner turns it on.
    tag_editing: Mapped[bool] = mapped_column(default=False, server_default="false")
    # The server's public IP (from plex.tv), and where that is: home-network plays are
    # placed here on the listens map.
    public_address: Mapped[str | None] = mapped_column(String(64))

    users: Mapped[list[UserServer]] = relationship(back_populates="server")


class UserServer(TimestampMixin, Base):
    """A user's connection to a Plex server, using that user's own server access token."""

    __tablename__ = "user_servers"
    __table_args__ = (UniqueConstraint("user_id", "server_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("plex_servers.id", ondelete="CASCADE"), index=True
    )
    # Server access token, encrypted (purpose "user_server.access_token").
    access_token_enc: Mapped[str] = mapped_column(Text)
    is_owner: Mapped[bool] = mapped_column(default=False)
    connection_uri: Mapped[str] = mapped_column(Text)
    connection_kind: Mapped[str] = mapped_column(String(16))  # local | remote | relay
    music_section_keys: Mapped[list[str]] = mapped_column(default=list)
    last_connected_at: Mapped[dt.datetime | None]

    user: Mapped[User] = relationship(back_populates="servers")
    server: Mapped[PlexServer] = relationship(back_populates="users", lazy="joined")


class InstanceSetting(Base):
    """Key/value store for instance-wide settings and state (access mode, heartbeats...)."""

    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=utcnow)


# --- library mirror ------------------------------------------------------------------
#
# Metadata (artists, albums, tracks, tags) is stored once per Plex server and keyed by
# (server_id, rating_key). Everything personal lives in `user_item_state`, which also
# records *which* items each user's own token can see: library queries always go through
# it, so a shared user never sees items their Plex account has no access to.


class LibrarySection(Base):
    """Server-level sync state for one music library."""

    __tablename__ = "library_sections"
    __table_args__ = (PrimaryKeyConstraint("server_id", "section_key"),)

    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    section_key: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(255))
    # Last full sync run by the server owner (the only kind that may delete metadata).
    last_owner_full_sync_at: Mapped[dt.datetime | None]


class UserSectionSync(Base):
    """Per-user sync state for one music library (watermark for incremental syncs)."""

    __tablename__ = "user_section_syncs"
    __table_args__ = (PrimaryKeyConstraint("user_server_id", "section_key"),)

    user_server_id: Mapped[int] = mapped_column(ForeignKey("user_servers.id", ondelete="CASCADE"))
    section_key: Mapped[str] = mapped_column(String(16))
    last_full_sync_at: Mapped[dt.datetime | None]
    # Start time of the last successful sync of any kind; incremental syncs fetch what
    # changed since then.
    last_sync_started_at: Mapped[dt.datetime | None]


class _LibraryItem:
    server_id: Mapped[int] = mapped_column(
        ForeignKey("plex_servers.id", ondelete="CASCADE"), primary_key=True
    )
    rating_key: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    section_key: Mapped[str] = mapped_column(String(16))
    guid: Mapped[str | None] = mapped_column(Text)
    mbid: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    title_sort: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    thumb: Mapped[str | None] = mapped_column(Text)
    art: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[dt.datetime | None]
    plex_updated_at: Mapped[dt.datetime | None]
    # Tracks: Plex `ratingCount` (Last.fm listeners). Albums / artists: derived from
    # their tracks, see plekstender.sync.popularity.
    popularity: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_at: Mapped[dt.datetime]


class Artist(_LibraryItem, Base):
    __tablename__ = "artists"
    __table_args__ = (Index("ix_artists_server_section", "server_id", "section_key"),)

    country: Mapped[str | None] = mapped_column(String(128))
    # Normalised name, to match listens (see plekstender.sync.parse.normalise_artist).
    name_key: Mapped[str | None] = mapped_column(Text)


class Album(_LibraryItem, Base):
    __tablename__ = "albums"
    __table_args__ = (
        Index("ix_albums_server_section", "server_id", "section_key"),
        Index("ix_albums_server_artist", "server_id", "artist_rating_key"),
    )

    artist_rating_key: Mapped[int | None] = mapped_column(BigInteger)
    year: Mapped[int | None]
    released: Mapped[dt.date | None] = mapped_column(Date)
    studio: Mapped[str | None] = mapped_column(Text)
    critic_rating: Mapped[float | None] = mapped_column(Float)
    track_count: Mapped[int | None]
    # Normalised title, to match listens' album names.
    title_norm: Mapped[str | None] = mapped_column(Text)


class Track(_LibraryItem, Base):
    __tablename__ = "tracks"
    __table_args__ = (
        Index("ix_tracks_server_section", "server_id", "section_key"),
        Index("ix_tracks_server_album", "server_id", "album_rating_key"),
        Index("ix_tracks_server_artist", "server_id", "artist_rating_key"),
    )

    album_rating_key: Mapped[int | None] = mapped_column(BigInteger)
    artist_rating_key: Mapped[int | None] = mapped_column(BigInteger)
    disc: Mapped[int | None]
    number: Mapped[int | None]
    duration_ms: Mapped[int | None]
    # Track artist when it differs from the album artist (e.g. features, compilations).
    original_title: Mapped[str | None] = mapped_column(Text)
    # Lower-cased title without "(2011 Remaster)"-style suffixes; groups duplicates.
    title_norm: Mapped[str] = mapped_column(Text)
    # Normalised track artist (original title, else album artist), to match listens.
    artist_key: Mapped[str | None] = mapped_column(Text)
    part_key: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("server_id", "kind", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # genre | mood | style | country
    name: Mapped[str] = mapped_column(String(255))


class ItemTag(Base):
    __tablename__ = "item_tags"
    __table_args__ = (
        PrimaryKeyConstraint("server_id", "rating_key", "tag_id"),
        Index("ix_item_tags_tag", "tag_id"),
    )

    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"))


class ArtistArea(Base):
    """Where an artist is from, as MusicBrainz says (for artists Plex has no country for).
    Shared by every server: keyed by MusicBrainz ID ("mbid:…") or normalised name
    ("name:…"). A None country was looked up and not found (tried again after a while)."""

    __tablename__ = "artist_areas"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    country_code: Mapped[str | None] = mapped_column(String(2))
    checked_at: Mapped[dt.datetime]


class AlbumColour(Base):
    """An album cover's main colour (worker, from a small thumbnail). `thumb` is the cover it
    was taken from: a new cover is looked at again. No colour: the cover couldn't be read."""

    __tablename__ = "album_colours"
    __table_args__ = (PrimaryKeyConstraint("server_id", "rating_key"),)

    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    thumb: Mapped[str] = mapped_column(Text)
    hex: Mapped[str | None] = mapped_column(String(7))
    hue: Mapped[float | None] = mapped_column(Float)  # 0-360
    saturation: Mapped[float | None] = mapped_column(Float)  # 0-1
    lightness: Mapped[float | None] = mapped_column(Float)  # 0-1
    checked_at: Mapped[dt.datetime]


class AlbumCover(Base):
    """An album cover's size in Plex (measured by the worker, for Library → Artwork →
    Album covers), and what's been done about it."""

    __tablename__ = "album_covers"

    server_id: Mapped[int] = mapped_column(
        ForeignKey("plex_servers.id", ondelete="CASCADE"), primary_key=True
    )
    rating_key: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # The Plex image measured (a new one is measured again); null size: unreadable.
    thumb: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None]
    height: Mapped[int | None]
    checked_at: Mapped[dt.datetime | None]
    # MusicBrainz release group found by name (when Plex has no MBID for the album).
    mbid: Mapped[str | None] = mapped_column(String(64))
    # "This one's fine": left out of the lists of covers to improve.
    kept: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[dt.datetime | None]
    applied_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class SimilarArtist(Base):
    __tablename__ = "similar_artists"
    __table_args__ = (PrimaryKeyConstraint("server_id", "artist_rating_key", "position"),)

    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    artist_rating_key: Mapped[int] = mapped_column(BigInteger)
    position: Mapped[int]
    name: Mapped[str] = mapped_column(String(255))


class UserItemState(Base):
    """A user's own view of an item: rating, plays, and that their token can see it."""

    __tablename__ = "user_item_state"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "server_id", "rating_key"),
        Index("ix_user_item_state_type", "user_id", "server_id", "item_type"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    item_type: Mapped[str] = mapped_column(String(8))  # artist | album | track
    section_key: Mapped[str] = mapped_column(String(16))
    user_rating: Mapped[float | None] = mapped_column(Float)  # 0-10, like Plex
    view_count: Mapped[int] = mapped_column(default=0)
    skip_count: Mapped[int] = mapped_column(default=0)
    last_viewed_at: Mapped[dt.datetime | None]
    last_rated_at: Mapped[dt.datetime | None]
    seen_at: Mapped[dt.datetime]
    # The user's listens from Plekstender's history (Plex plays + imports), matched by name.
    listens: Mapped[int] = mapped_column(default=0, server_default="0")
    # Tracks: the user's latest listen in that history, matched the same way.
    last_listened_at: Mapped[dt.datetime | None]


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_user_server", "user_server_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_server_id: Mapped[int] = mapped_column(ForeignKey("user_servers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # full | incremental
    trigger: Mapped[str] = mapped_column(String(16))  # manual | schedule | setup
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # queued | running | succeeded | failed
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)


# --- listening history ------------------------------------------------------------------


class ListenImport(Base):
    """One import of a user's listening history from another service."""

    __tablename__ = "listen_imports"
    __table_args__ = (Index("ix_listen_imports_user", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(16))  # lastfm
    username: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # queued | running | succeeded | failed
    # Only listens in (window_from, window_to] are fetched: a repeat import picks up
    # where the last one ended, and listens made during the import don't shift pages.
    window_from: Mapped[dt.datetime | None]
    window_to: Mapped[dt.datetime]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # {"page": last page stored, "pages": n, "done": listens read, "total": n}
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # {"added": n, "existing": n, "skipped": n}
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    # Asked to stop from the app: "stop" (keep what's done) or "cancel" (also take it back,
    # where that's possible). The job checks between items.
    stop_requested: Mapped[str | None] = mapped_column(String(8))


class Listen(Base):
    """One play by one user, from Plex or an imported history.

    Listens keep the names they were played under (plus normalised keys), so plays of
    music that isn't, or is no longer, in a library still count.
    """

    __tablename__ = "listens"
    __table_args__ = (
        # Makes imports idempotent: the same play from the same source is stored once.
        UniqueConstraint(
            "user_id", "source", "listened_at", "artist_key", "track_key", name="uq_listens_play"
        ),
        Index("ix_listens_user_time", "user_id", "listened_at"),
        Index("ix_listens_user_track", "user_id", "artist_key", "track_key"),
        # A Plex history entry is stored once, however often it's read.
        Index(
            "uq_listens_source_ref",
            "user_id",
            "server_id",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # When the track started playing (Last.fm and ListenBrainz convention).
    listened_at: Mapped[dt.datetime]
    source: Mapped[str] = mapped_column(String(16))  # lastfm | plex | listenbrainz
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("listen_imports.id", ondelete="SET NULL")
    )
    artist_name: Mapped[str] = mapped_column(Text)
    album_name: Mapped[str | None] = mapped_column(Text)
    track_name: Mapped[str] = mapped_column(Text)
    artist_key: Mapped[str] = mapped_column(Text)
    album_key: Mapped[str | None] = mapped_column(Text)
    track_key: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int | None]
    artist_mbid: Mapped[str | None] = mapped_column(String(36))
    release_mbid: Mapped[str | None] = mapped_column(String(36))
    recording_mbid: Mapped[str | None] = mapped_column(String(36))
    # Plex plays: the server and track played, and Plex's `historyKey`.
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("plex_servers.id", ondelete="SET NULL")
    )
    rating_key: Mapped[int | None] = mapped_column(BigInteger)
    source_ref: Mapped[str | None] = mapped_column(Text)
    # Plex plays seen live by the session watcher: the player and where it played from.
    client: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(String(64))
    network: Mapped[str | None] = mapped_column(String(8))  # lan | wan
    location: Mapped[str | None] = mapped_column(Text)  # "Oslo, Norway" (from the IP database)
    # ISO country and continent codes of `location` (home-network plays: the server's).
    country_code: Mapped[str | None] = mapped_column(String(2))
    continent_code: Mapped[str | None] = mapped_column(String(2))
    # Same play recorded by another source (e.g. a Plexamp play also scrobbled to Last.fm).
    duplicate_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("listens.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class HistoryCursor(Base):
    """How far Plex's play history has been read, per server connection and music library."""

    __tablename__ = "history_cursors"
    __table_args__ = (PrimaryKeyConstraint("user_server_id", "section_key"),)

    user_server_id: Mapped[int] = mapped_column(ForeignKey("user_servers.id", ondelete="CASCADE"))
    section_key: Mapped[str] = mapped_column(String(16))
    # Newest `viewedAt` read so far; the next read starts a little before it.
    viewed_through: Mapped[dt.datetime | None]
    backfilled_at: Mapped[dt.datetime | None]  # when all older history had been read
    last_run_at: Mapped[dt.datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)


class ListenBrainzLink(TimestampMixin, Base):
    """A user's ListenBrainz account, for scrobbling their new Plex plays."""

    __tablename__ = "listenbrainz_links"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # ListenBrainz user token, encrypted (purpose "user.listenbrainz_token").
    token_enc: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(String(255))
    # Only plays that started from this time on are sent.
    enabled_at: Mapped[dt.datetime]
    # Listens up to this id have been handled (sent or skipped).
    submitted_through_id: Mapped[int] = mapped_column(BigInteger, default=0)
    sent_count: Mapped[int] = mapped_column(default=0)
    last_submitted_at: Mapped[dt.datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    # ListenBrainz rejected the token: nothing is sent until it's replaced.
    paused: Mapped[bool] = mapped_column(default=False)


class RatingEvent(Base):
    """A rating set in Plekstender: an append-only log for undo and history."""

    __tablename__ = "rating_events"
    __table_args__ = (
        Index("ix_rating_events_user", "user_id", "id"),
        Index("ix_rating_events_item", "user_id", "server_id", "rating_key", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    item_type: Mapped[str] = mapped_column(String(8))
    old_rating: Mapped[float | None] = mapped_column(Float)  # 0-10; None = unrated
    new_rating: Mapped[float | None] = mapped_column(Float)
    # When the old rating had been set: an undo puts that back too (so the item returns to
    # its place in "your ratings" instead of looking newly rated).
    old_rated_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    undone_at: Mapped[dt.datetime | None]


class ArtistArtwork(Base):
    """Artwork work-in-progress per artist: a to-do status and what was applied."""

    __tablename__ = "artist_artwork"
    __table_args__ = (PrimaryKeyConstraint("server_id", "rating_key"),)

    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    # none: not looked at | custom: needs a custom image | done: happy with it
    status: Mapped[str] = mapped_column(String(8), default="none")
    # What was applied last: fanart | plex | search | upload | text
    source: Mapped[str | None] = mapped_column(String(16))
    source_url: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # MusicBrainz ID found by name when Plex has none.
    mbid: Mapped[str | None] = mapped_column(String(36))
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    applied_at: Mapped[dt.datetime | None]
    # artist.jpg in the artist's folder (written by the worker): where, or why not.
    folder_path: Mapped[str | None] = mapped_column(Text)
    folder_error: Mapped[str | None] = mapped_column(Text)
    folder_saved_at: Mapped[dt.datetime | None]
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ImageSearchCache(Base):
    """Image search results, cached so repeated lookups don't hit search engines."""

    __tablename__ = "image_search_cache"

    query: Mapped[str] = mapped_column(Text, primary_key=True)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ArtworkJob(Base):
    """A background artwork job on one server: bulk fanart.tv logos, export or import."""

    __tablename__ = "artwork_jobs"
    __table_args__ = (Index("ix_artwork_jobs_server", "server_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    # The owner's connection the job uses (and who started it).
    user_server_id: Mapped[int] = mapped_column(ForeignKey("user_servers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # fanart | export | import
    status: Mapped[str] = mapped_column(String(16), default="queued")
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"done": n, "total": n}
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    result_path: Mapped[str | None] = mapped_column(Text)  # export: the zip under DATA_DIR
    # What it replaced, to put back when it's cancelled (see artwork/undo.py); not shown.
    undo: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # Asked to stop from the app: "stop" (keep what's done) or "cancel" (also take it back,
    # where that's possible). The job checks between items.
    stop_requested: Mapped[str | None] = mapped_column(String(8))


class PlaySession(Base):
    """A track seen playing in Plex (polled from /status/sessions), to tell finished plays
    from skips and to know the player and where it played from."""

    __tablename__ = "play_sessions"
    __table_args__ = (
        UniqueConstraint("server_id", "session_key", "rating_key", "started_at"),
        Index("ix_play_sessions_user_track", "user_id", "rating_key", "last_seen_at"),
        Index("ix_play_sessions_open", "server_id", "ended_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_key: Mapped[str] = mapped_column(String(64))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[dt.datetime]
    last_seen_at: Mapped[dt.datetime]
    ended_at: Mapped[dt.datetime | None]
    # Furthest position reached, and the track's length (ms).
    max_offset_ms: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int | None]
    client: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(String(64))
    network: Mapped[str | None] = mapped_column(String(8))
    location: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(2))
    continent_code: Mapped[str | None] = mapped_column(String(2))


class Playlist(Base):
    """A smart playlist: a definition (see plekstender.playlists.definition) that the worker
    turns into a regular Plex playlist under the user's own account, and can rebuild."""

    __tablename__ = "playlists"
    __table_args__ = (
        Index("ix_playlists_user_server", "user_server_id"),
        Index("ix_playlists_next_run", "next_run_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_server_id: Mapped[int] = mapped_column(ForeignKey("user_servers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    definition: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # How the builder page shows the definition (chosen genres, names), not used to build.
    display: Mapped[dict[str, Any]] = mapped_column(default=dict)
    schedule: Mapped[str] = mapped_column(
        String(16), default="manual"
    )  # manual|daily|weekly|monthly
    plex_playlist_key: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|queued|running|ok|error
    error: Mapped[str | None] = mapped_column(Text)
    last_result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # The songs (rating keys, in order) of the last build: what's in Plex, for M3U exports.
    song_keys: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default="[]")
    last_built_at: Mapped[dt.datetime | None]
    next_run_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class TrackIntro(Base):
    """How a track starts, measured when its preview clip is first made."""

    __tablename__ = "track_intros"
    __table_args__ = (PrimaryKeyConstraint("server_id", "rating_key"),)

    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    # Silence before the music starts (ms, below -40 dB).
    leading_silence_ms: Mapped[int]
    analysed_at: Mapped[dt.datetime]


class Notification(Base):
    """Something in the bell (Phase 17): a problem (something someone relies on stopped
    working or nears a limit; resolved when it goes away) or news (level "info": new gigs,
    top-10 changes). One row per `key`: repeats update it. `user_id` None: about the
    instance, for admins."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(Text, unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(8))  # warning | error | info
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    first_at: Mapped[dt.datetime]
    updated_at: Mapped[dt.datetime]
    resolved_at: Mapped[dt.datetime | None]
    hidden_at: Mapped[dt.datetime | None]  # an admin dismissed it for everyone


class NotificationState(Base):
    """A person's read / dismissed state of a notification."""

    __tablename__ = "notification_states"
    __table_args__ = (PrimaryKeyConstraint("notification_id", "user_id"),)

    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    read_at: Mapped[dt.datetime | None]
    dismissed_at: Mapped[dt.datetime | None]


class TopSeen(Base):
    """A person's all-time top 10 artists, albums or songs as last seen, to tell them when
    one enters it or there's a new #1 (see plekstender.history.top_watch)."""

    __tablename__ = "top_seen"
    __table_args__ = (PrimaryKeyConstraint("user_id", "kind"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))  # artist | album | track
    # [{"key": "artist key/album key", "name", "artist", "listens"}], #1 first.
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Worked out from the listens up to this one (nothing to do until there are newer).
    last_listen_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[dt.datetime]


class HiddenSuggestion(Base):
    """An artist someone doesn't want suggested for their library (Library → Artists to add)."""

    __tablename__ = "hidden_suggestions"
    __table_args__ = (PrimaryKeyConstraint("user_id", "artist_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    artist_key: Mapped[str] = mapped_column(Text)  # normalise_artist(name)
    name: Mapped[str] = mapped_column(String(255))
    hidden_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ReleaseGroup(Base):
    """An artist's release group on MusicBrainz (album, EP, single...), for Library -> Releases.
    Shared by every server, by the artist's MBID; the worker refreshes them (`releases`)."""

    __tablename__ = "release_groups"
    __table_args__ = (
        PrimaryKeyConstraint("artist_mbid", "mbid"),
        Index("ix_release_groups_mbid", "mbid"),
        Index("ix_release_groups_released", "released"),
    )

    artist_mbid: Mapped[str] = mapped_column(String(36))
    mbid: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(Text)
    # normalise_title(title), to match library albums named the same.
    title_norm: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # album|ep|single|live|compilation|other
    primary_type: Mapped[str | None] = mapped_column(String(32))
    secondary_types: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # As MusicBrainz gives it: "2019", "2019-05" or "2019-05-21".
    first_release_date: Mapped[str | None] = mapped_column(String(10))
    # The same as a date (the 1st of the month / year when only those are known).
    released: Mapped[dt.date | None] = mapped_column(Date)


class ArtistReleaseCheck(Base):
    """When an artist's release groups were last read from MusicBrainz."""

    __tablename__ = "artist_release_checks"

    artist_mbid: Mapped[str] = mapped_column(String(36), primary_key=True)
    checked_at: Mapped[dt.datetime]
    # False: MusicBrainz doesn't know the MBID (merged or deleted there).
    found: Mapped[bool] = mapped_column(default=True)


class HiddenSong(Base):
    """A song someone doesn't want in History -> Rediscover (or its forgotten favourites
    playlist). Any release of the song hides it."""

    __tablename__ = "hidden_songs"
    __table_args__ = (PrimaryKeyConstraint("user_id", "server_id", "rating_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    hidden_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class HiddenRelease(Base):
    """A release group someone doesn't want to see in Library -> Releases."""

    __tablename__ = "hidden_releases"
    __table_args__ = (PrimaryKeyConstraint("user_id", "mbid"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mbid: Mapped[str] = mapped_column(String(36))
    hidden_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class UserAchievement(Base):
    """An achievement a person unlocked (see plekstender.achievements), and when they saw
    its celebration."""

    __tablename__ = "user_achievements"
    __table_args__ = (PrimaryKeyConstraint("user_id", "achievement"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    achievement: Mapped[str] = mapped_column(String(64))
    unlocked_at: Mapped[dt.datetime]
    seen_at: Mapped[dt.datetime | None]


class AppTime(Base):
    """Time spent with Plekstender open and in use, per person and day (for Rewind)."""

    __tablename__ = "app_time"
    __table_args__ = (PrimaryKeyConstraint("user_id", "day"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    day: Mapped[dt.date] = mapped_column(Date)
    seconds: Mapped[int] = mapped_column(default=0)


class GameStats(Base):
    """A user's "Guess the intro" record per mode (track | album | artist)."""

    __tablename__ = "game_stats"
    __table_args__ = (PrimaryKeyConstraint("user_id", "mode"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(8))
    rounds: Mapped[int] = mapped_column(default=0)
    correct: Mapped[int] = mapped_column(default=0)
    points: Mapped[int] = mapped_column(default=0)
    best_streak: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class GameRound(Base):
    """One finished round of Guess the intro (for the scores page: latest games)."""

    __tablename__ = "game_rounds"
    __table_args__ = (Index("ix_game_rounds_user_time", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(8))
    correct: Mapped[bool]
    points: Mapped[int]
    streak: Mapped[int]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class MediaFile(Base):
    """A music file in the mounted music folder, with its tags (read by the worker).

    `path` is relative to the server's mapped folder (local_path_prefix). Files are re-read
    only when their size or modification time changes.
    """

    __tablename__ = "media_files"
    __table_args__ = (
        UniqueConstraint("server_id", "path"),
        Index("ix_media_files_track", "server_id", "rating_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[float]
    format: Mapped[str | None] = mapped_column(String(16))
    duration_ms: Mapped[int | None]
    # Normalised tags: {"title": "...", "artist": "...", "genre": ["..."], ...}.
    tags: Mapped[dict[str, Any]] = mapped_column(default=dict)
    has_cover: Mapped[bool] = mapped_column(default=False)
    # The tag reader's version when the file was read (older: re-read on the next scan).
    reader: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    # The embedded front cover: {"sha1", "mime", "bytes", "width", "height"}.
    cover: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Identified by sound: {"key": "size:mtime", "fingerprint", "duration", "results": [...],
    # "error", "at"} (AcoustID's matches, condensed; see tags/identify.py).
    identity: Mapped[dict[str, Any] | None]
    # The Plex track this file is (matched by its path), if any.
    rating_key: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    # Lyrics in its tags, and the best it has anywhere (a .lrc, .txt or .lyricsfile.yaml next
    # to it too): word | synced | plain | instrumental, None: none (see tags/lyrics_kinds.py).
    embedded_lyrics: Mapped[str | None] = mapped_column(String(12))
    lyrics: Mapped[str | None] = mapped_column(String(12))


class NfoFile(Base):
    """A Kodi-style artist.nfo or album.nfo in the music folder, with the fields read from it
    (worker scans). `path` is relative to the mapped folder; its folder says what it's about."""

    __tablename__ = "nfo_files"
    __table_args__ = (UniqueConstraint("server_id", "path"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(8))  # artist | album
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[float]
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class FolderImage(Base):
    """A cover.jpg / folder.jpg / front.jpg / album.jpg or artist.jpg in the music folder
    (worker scans), described like embedded covers."""

    __tablename__ = "folder_images"
    __table_args__ = (UniqueConstraint("server_id", "path"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(8))  # cover | artist
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[float]
    sha1: Mapped[str | None] = mapped_column(String(40))
    width: Mapped[int | None]
    height: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)


class FileScan(Base):
    """A worker job over a server's music folder: a scan, or identifying files by sound."""

    __tablename__ = "file_scans"
    __table_args__ = (Index("ix_file_scans_server", "server_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(
        String(8), default="scan", server_default="scan"
    )  # | identify | rename | lyrics | dupes
    # What an identify job covers: {"file_ids": [...], "again": bool}.
    params: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(
        String(16), default="queued"
    )  # queued|running|succeeded|failed
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # Asked to stop from the app: "stop" (keep what's done) or "cancel" (also take it back,
    # where that's possible). The job checks between items.
    stop_requested: Mapped[str | None] = mapped_column(String(8))


class TrackLyrics(Base):
    """Lyrics found for a music file (LRCLIB, looked up by the worker), and the lyrics the file
    already had when it was looked at: a `.lrc` or `.txt` next to it, or in its tags."""

    __tablename__ = "track_lyrics"
    __table_args__ = (Index("ix_track_lyrics_server", "server_id"),)

    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="CASCADE"), primary_key=True
    )
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    # synced | plain | instrumental | none (not found) | untagged (no artist or title) | error
    status: Mapped[str] = mapped_column(String(16))
    synced: Mapped[str | None] = mapped_column(Text)  # LRC, with [mm:ss.xx] times
    plain: Mapped[str | None] = mapped_column(Text)
    # LRCLIB's Lyricsfile (YAML), and whether it times each word.
    lyricsfile: Mapped[str | None] = mapped_column(Text)
    word_synced: Mapped[bool] = mapped_column(default=False, server_default="false")
    lrclib_id: Mapped[int | None] = mapped_column(BigInteger)
    existing_lrc: Mapped[str | None] = mapped_column(Text)
    existing_txt: Mapped[str | None] = mapped_column(Text)
    existing_lyricsfile: Mapped[str | None] = mapped_column(Text)
    existing_embedded: Mapped[str | None] = mapped_column(Text)
    # Whether lyrics can be written into this file's tags (MP3, FLAC, Ogg, MP4 / M4A).
    embeddable: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    looked_up_at: Mapped[dt.datetime]


class FileFingerprint(Base):
    """A music file's raw Chromaprint fingerprint (first two minutes), to find files that are
    the same recording. `file_key` is the file's size and modification time when it was made:
    a changed file is fingerprinted again. Numbers are stored signed (Postgres integers)."""

    __tablename__ = "file_fingerprints"

    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="CASCADE"), primary_key=True
    )
    file_key: Mapped[str] = mapped_column(String(64))
    values: Mapped[list[int]] = mapped_column(ARRAY(Integer))


class TagChangeSet(Base):
    """One edit of music files' tags (a user's edit, or an undo of earlier changes), applied
    by the worker. Its changes are recorded before anything is written."""

    __tablename__ = "tag_change_sets"
    __table_args__ = (Index("ix_tag_change_sets_server", "server_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(8), default="edit")  # edit | undo
    description: Mapped[str] = mapped_column(Text)
    # queued | running | applied | partial (some skipped or failed) | failed
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    applied_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # Asked to stop from the app: "stop" (keep what's done) or "cancel" (also take it back,
    # where that's possible). The job checks between items.
    stop_requested: Mapped[str | None] = mapped_column(String(8))


class TagChange(Base):
    """One field of one file: what it was, what it becomes. `old_value` is also what the
    file must still hold when the worker writes (otherwise it's a conflict, not written)."""

    __tablename__ = "tag_changes"
    __table_args__ = (
        Index("ix_tag_changes_set", "change_set_id"),
        Index("ix_tag_changes_file", "media_file_id"),
        Index("ix_tag_changes_undoes", "undoes_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    change_set_id: Mapped[int] = mapped_column(ForeignKey("tag_change_sets.id", ondelete="CASCADE"))
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL")
    )
    path: Mapped[str] = mapped_column(Text)  # the file at the time (relative)
    rating_key: Mapped[int | None] = mapped_column(BigInteger)  # its Plex track
    field: Mapped[str] = mapped_column(String(32))
    old_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # pending | applied | conflict (the file changed since) | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    # For an undo: the change it puts back.
    undoes_id: Mapped[int | None] = mapped_column(ForeignKey("tag_changes.id", ondelete="SET NULL"))


class TagSnapshot(Base):
    """A file's tags the first time Plekstender changed them (what was there before)."""

    __tablename__ = "tag_snapshots"

    media_file_id: Mapped[int] = mapped_column(
        ForeignKey("media_files.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text)
    tags: Mapped[dict[str, Any]] = mapped_column(default=dict)
    taken_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class LiveSettings(Base):
    """A user's Live tab: where they are, how far they'd go, and how many artists to follow."""

    __tablename__ = "live_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    place_name: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    radius_km: Mapped[int] = mapped_column(default=100, server_default="100")
    # Follow the top N% of the artists the user has listened to (at most MAX_ARTISTS).
    top_percent: Mapped[int] = mapped_column(SmallInteger, default=10, server_default="10")
    # Rank artists by the listens of the past N months (None: all time).
    period_months: Mapped[int | None] = mapped_column(SmallInteger)
    # The address of the user's calendar of starred shows (iCal) ends in a secret token; only
    # its SHA-256 is kept (like sessions), so the address can only be shown when it's made.
    calendar_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    calendar_created_at: Mapped[dt.datetime | None]
    # Last refresh: queued | running | done | failed; when; how many artists and requests.
    refresh_status: Mapped[str | None] = mapped_column(String(8))
    refresh_error: Mapped[str | None] = mapped_column(Text)
    refreshed_at: Mapped[dt.datetime | None]
    refresh_requested_at: Mapped[dt.datetime | None]
    # Data Thistle is looked up when the Live tab is opened (its listings may be kept a day).
    datathistle_at: Mapped[dt.datetime | None]
    datathistle_error: Mapped[str | None] = mapped_column(Text)
    artist_count: Mapped[int | None]
    request_count: Mapped[int | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=utcnow)


class LiveShow(Base):
    """An upcoming show by an artist the user follows, near them, from one source."""

    __tablename__ = "live_shows"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_id"),
        Index("ix_live_shows_user_date", "user_id", "start_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(16))  # jambase
    source_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    festival: Mapped[bool] = mapped_column(default=False)
    # The venue's local date and time ("20:00"), as the source gives them.
    start_date: Mapped[dt.date] = mapped_column(Date)
    start_time: Mapped[str | None] = mapped_column(String(5))
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    venue: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    distance_km: Mapped[float | None] = mapped_column(Float)
    ticket_url: Mapped[str | None] = mapped_column(Text)
    seller: Mapped[str | None] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(Text)
    # Ticket prices, when the source has them (0 is free).
    price_min: Mapped[float | None] = mapped_column(Float)
    price_max: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))
    # Everyone on the bill: [{"name", "headliner"}]; the user's artists among them:
    # [{"name", "headliner", "server_id", "rating_key"}].
    performers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    artists: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    fetched_at: Mapped[dt.datetime]
    # Starred by the user: kept (and in their calendar) even when no longer found.
    starred_at: Mapped[dt.datetime | None]
    # The user has a ticket (only for starred shows).
    ticket_at: Mapped[dt.datetime | None]


class LiveFeed(Base):
    """A calendar link (iCal) a user added as a source of shows, e.g. their Songkick one."""

    __tablename__ = "live_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The address, encrypted (purpose "live_feed.url": it may hold a private token).
    url_enc: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)  # the calendar's own name, if it has one
    added_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    fetched_at: Mapped[dt.datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
    show_count: Mapped[int | None]


class LiveSeen(Base):
    """When each show was first seen, to list new ones. Only a fingerprint of the source's
    ID is kept (SHA-256 of "source:id"), not the listing: Data Thistle's listings may only
    be kept a day, but a listing fetched again is still recognised."""

    __tablename__ = "live_seen"
    __table_args__ = (PrimaryKeyConstraint("user_id", "key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    first_seen_at: Mapped[dt.datetime]


class Gig(Base):
    """A gig the user went to: added by hand, from a starred Live gig, or imported."""

    __tablename__ = "gigs"
    __table_args__ = (
        Index("ix_gigs_user_date", "user_id", "date"),
        Index(
            "uq_gigs_source_ref",
            "user_id",
            "source",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    date: Mapped[dt.date] = mapped_column(Date)
    # A festival's or tour's name, if it had one.
    name: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    # How exact the place is: venue | city (None: not looked up yet, or not found).
    place_precision: Mapped[str | None] = mapped_column(String(8))
    notes: Mapped[str | None] = mapped_column(Text)
    # On the bill, the first ones the headliners: [{"name", "server_id", "rating_key"}].
    artists: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # What was played, per artist (from setlist.fm): [{"artist", "url", "songs": [{"name",
    # "cover", "tape"}]}]. Songs in the library played here are "seen live".
    setlists: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Where it came from: manual | live | setlistfm | csv | ical; and its ID there.
    source: Mapped[str] = mapped_column(String(16), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=utcnow)


class GigPlaylist(Base):
    """A playlist made for a gig: before a starred one ("before", `ref` the Live gig's
    source:id), or after one the user went to ("after", `ref` the gig's id). Kept when the
    playlist is deleted, so it isn't made again."""

    __tablename__ = "gig_playlists"
    __table_args__ = (PrimaryKeyConstraint("user_id", "kind", "ref"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))  # before | after
    ref: Mapped[str] = mapped_column(String(128))
    playlist_id: Mapped[int | None] = mapped_column(ForeignKey("playlists.id", ondelete="SET NULL"))
    gig_date: Mapped[dt.date] = mapped_column(Date)
    songs: Mapped[int] = mapped_column(default=0)  # matched in the library, last time
    made_at: Mapped[dt.datetime]
    refreshed_at: Mapped[dt.datetime]


class GigPlace(Base):
    """Where a venue is (looked up once, shared by everyone's gigs)."""

    __tablename__ = "gig_places"

    # "venue|city|country", folded.
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    precision: Mapped[str | None] = mapped_column(String(8))  # venue | city | None (not found)
    looked_up_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ScrobbleTarget(Base):
    """Another service a user's new Plex plays are sent to: Last.fm, a ListenBrainz-compatible
    server (Maloja, Koito, Multi-Scrobbler…) or Libre.fm (or another GNU FM server)."""

    __tablename__ = "scrobble_targets"
    __table_args__ = (UniqueConstraint("user_id", "kind", "api_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # listenbrainz | librefm | lastfm (protocol)
    # What was picked: maloja | koito | multi-scrobbler | listenbrainz-other | librefm | lastfm
    service: Mapped[str] = mapped_column(String(24))
    api_url: Mapped[str] = mapped_column(Text)
    # The token (ListenBrainz-compatible) or session key (Libre.fm, Last.fm), encrypted.
    token_enc: Mapped[str] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(String(255))
    # Only plays that started from this time on are sent.
    enabled_at: Mapped[dt.datetime]
    submitted_through_id: Mapped[int] = mapped_column(BigInteger, default=0)
    sent_count: Mapped[int] = mapped_column(default=0)
    last_submitted_at: Mapped[dt.datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    paused: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
