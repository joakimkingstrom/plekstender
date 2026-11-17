"""Database models (SQLAlchemy 2.0, typed declarative)."""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    MetaData,
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
