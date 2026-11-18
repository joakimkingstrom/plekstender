"""Database models (SQLAlchemy 2.0, typed declarative)."""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    MetaData,
    PrimaryKeyConstraint,
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
    __table_args__ = (Index("ix_rating_events_user", "user_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    server_id: Mapped[int] = mapped_column(ForeignKey("plex_servers.id", ondelete="CASCADE"))
    rating_key: Mapped[int] = mapped_column(BigInteger)
    item_type: Mapped[str] = mapped_column(String(8))
    old_rating: Mapped[float | None] = mapped_column(Float)  # 0-10; None = unrated
    new_rating: Mapped[float | None] = mapped_column(Float)
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
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
