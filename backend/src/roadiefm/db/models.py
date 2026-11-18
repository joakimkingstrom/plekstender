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
    # their tracks, see roadiefm.sync.popularity.
    popularity: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_at: Mapped[dt.datetime]


class Artist(_LibraryItem, Base):
    __tablename__ = "artists"
    __table_args__ = (Index("ix_artists_server_section", "server_id", "section_key"),)

    country: Mapped[str | None] = mapped_column(String(128))


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
