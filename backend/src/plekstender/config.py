"""Application settings, loaded from environment variables (and `.env` in development)."""

from __future__ import annotations

import enum
import hashlib
import hmac
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AccessMode(enum.StrEnum):
    """Who may sign in to this Plekstender instance."""

    OPEN = "open"  # any Plex account
    SERVERS = "servers"  # accounts with access to an admin-approved Plex server
    ALLOWLIST = "allowlist"  # explicitly listed Plex usernames / emails


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database: either a full URL or the individual parts (as used by the compose file).
    database_url: str | None = None
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_user: str = "plekstender"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "plekstender"

    # Used to derive encryption keys, the Plex client identifier and signed cookies.
    app_secret_key: SecretStr = Field(min_length=32)
    # Public URL of the app, e.g. https://plekstender.example.com. Used for the Plex sign-in
    # redirect, CSRF origin checks and to decide whether cookies are marked Secure.
    app_url: str = "http://localhost:8080"

    # Default access mode until the instance admin changes it in the UI.
    access_mode: AccessMode = AccessMode.SERVERS
    # Plex username or email allowed to claim the instance admin role on first sign-in.
    # When unset, the first account that signs in becomes admin.
    initial_admin: str | None = None

    session_ttl_days: int = 30
    log_level: str = "INFO"
    port: int = 8080
    # Directory containing the built frontend (index.html + assets). None = API only.
    static_dir: Path | None = None

    plex_product: str = "Plekstender"
    plex_tv_url: str = "https://plex.tv"
    plex_clients_url: str = "https://clients.plex.tv"
    plex_app_url: str = "https://app.plex.tv"

    # Last.fm API key for importing listening history. Can also be set on Settings by an
    # admin (that value wins). From 1 Dec 2026 a key only works if its owner has Last.fm Pro.
    lastfm_api_key: SecretStr | None = None
    lastfm_api_url: str = "https://ws.audioscrobbler.com/2.0/"
    listenbrainz_api_url: str = "https://api.listenbrainz.org"

    # Artwork. fanart.tv key can also be set on Settings by an admin (that value wins).
    fanart_api_key: SecretStr | None = None
    fanart_api_url: str = "https://webservice.fanart.tv/v3"
    musicbrainz_api_url: str = "https://musicbrainz.org/ws/2"
    # Image search (SearXNG with JSON output), e.g. http://searxng:8080. None = off.
    searxng_url: str | None = None
    # Writable directory for downloaded fonts, uploads and exports.
    data_dir: Path = Path("data")
    # ffmpeg for song previews; by default the one bundled with the imageio-ffmpeg package.
    ffmpeg_path: str | None = None
    # Identifying music files by sound: Chromaprint's fpcalc (in the Docker image) and an
    # AcoustID application key (or set on Settings → Connections by an admin; that wins).
    fpcalc_path: str = "fpcalc"
    acoustid_api_key: SecretStr | None = None
    acoustid_api_url: str = "https://api.acoustid.org/v2"

    @field_validator("app_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("APP_URL must be an absolute http(s) URL")
        return v.rstrip("/")

    # --- derived values -------------------------------------------------------------

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy URL using the psycopg (v3) driver."""
        url = self.database_url or self._url_from_parts()
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        raise ValueError("DATABASE_URL must be a postgresql:// URL")

    @property
    def libpq_url(self) -> str:
        """Plain libpq connection string (used by procrastinate / psycopg directly)."""
        return self.sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def _url_from_parts(self) -> str:
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        auth = f"{user}:{password}" if password else user
        return f"postgresql://{auth}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def secure_cookies(self) -> bool:
        return self.app_url.startswith("https://")

    @property
    def app_origin(self) -> str:
        parts = urlsplit(self.app_url)
        return f"{parts.scheme}://{parts.netloc}"

    def derive_key(self, purpose: str) -> bytes:
        """Derive a stable 32-byte key for a given purpose from APP_SECRET_KEY."""
        secret = self.app_secret_key.get_secret_value().encode()
        return hmac.new(secret, f"plekstender:{purpose}".encode(), hashlib.sha256).digest()

    @property
    def plex_client_identifier(self) -> str:
        """Stable per-instance identifier sent to Plex as X-Plex-Client-Identifier."""
        return str(uuid.UUID(bytes=self.derive_key("plex-client-identifier")[:16], version=4))


@lru_cache
def get_settings() -> Settings:
    return Settings()
