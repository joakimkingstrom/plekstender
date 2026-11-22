"""Typed accessors for instance-wide settings stored in `instance_settings`."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import AccessMode, Settings, get_settings
from plekstender.db.models import InstanceSetting
from plekstender.security.crypto import DecryptionError, TokenCipher, token_cipher

ACCESS_KEY = "access"
WORKER_HEARTBEAT_KEY = "worker_heartbeat"


async def _get(db: AsyncSession, key: str) -> dict[str, Any] | None:
    row = await db.scalar(select(InstanceSetting).where(InstanceSetting.key == key))
    return row.value if row else None


async def _put(db: AsyncSession, key: str, value: dict[str, Any]) -> None:
    stmt = insert(InstanceSetting).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[InstanceSetting.key],
        set_={"value": stmt.excluded.value, "updated_at": dt.datetime.now(dt.UTC)},
    )
    await db.execute(stmt)


class AccessSettings:
    def __init__(self, mode: AccessMode, allowlist: list[str]) -> None:
        self.mode = mode
        self.allowlist = allowlist


async def get_access_settings(db: AsyncSession) -> AccessSettings:
    stored = await _get(db, ACCESS_KEY) or {}
    mode = AccessMode(stored.get("mode", get_settings().access_mode))
    allowlist = [str(x) for x in stored.get("allowlist", [])]
    return AccessSettings(mode, allowlist)


async def set_access_settings(db: AsyncSession, mode: AccessMode, allowlist: list[str]) -> None:
    cleaned = sorted({x.strip().lower() for x in allowlist if x.strip()})
    await _put(db, ACCESS_KEY, {"mode": mode.value, "allowlist": cleaned})


async def record_worker_heartbeat(db: AsyncSession, now: dt.datetime | None = None) -> None:
    ts = (now or dt.datetime.now(dt.UTC)).isoformat()
    await _put(db, WORKER_HEARTBEAT_KEY, {"at": ts})


async def get_worker_heartbeat(db: AsyncSession) -> dt.datetime | None:
    stored = await _get(db, WORKER_HEARTBEAT_KEY)
    if not stored or "at" not in stored:
        return None
    return dt.datetime.fromisoformat(stored["at"])


# --- library settings -------------------------------------------------------------------

LIBRARY_KEY = "library"


class LibrarySettings(BaseModel):
    """Instance-wide library tuning, editable by admins on the Settings page."""

    album_top_n: int = Field(
        default=3, ge=1, le=20, description="Album popularity = sum of its top N tracks."
    )
    artist_top_n: int = Field(
        default=10, ge=1, le=50, description="Artist popularity = sum of their top N songs."
    )
    incremental_minutes: int = Field(
        default=30, ge=10, le=1440, description="How often to fetch plays, ratings and changes."
    )
    full_days: int = Field(
        default=7, ge=1, le=30, description="How often to re-read whole libraries."
    )


async def get_library_settings(db: AsyncSession) -> LibrarySettings:
    stored = await _get(db, LIBRARY_KEY) or {}
    defaults = LibrarySettings().model_dump()
    # Keep each stored value that's still valid, so a bad value (e.g. from an older
    # version with different limits) only resets that one setting.
    values = {}
    for name, default in defaults.items():
        candidate = {**defaults, name: stored.get(name, default)}
        try:
            values[name] = getattr(LibrarySettings.model_validate(candidate), name)
        except ValidationError:
            values[name] = default
    return LibrarySettings(**values)


async def set_library_settings(db: AsyncSession, settings: LibrarySettings) -> None:
    await _put(db, LIBRARY_KEY, settings.model_dump())


# --- Last.fm API key --------------------------------------------------------------------

LASTFM_KEY = "lastfm"
LASTFM_PURPOSE = "instance.lastfm_api_key"
LASTFM_SECRET_PURPOSE = "instance.lastfm_shared_secret"  # noqa: S105 - a label


def _cipher(settings: Settings) -> TokenCipher:
    return token_cipher(settings)


async def get_lastfm_api_key(
    db: AsyncSession, settings: Settings
) -> tuple[str | None, Literal["settings", "environment"] | None]:
    """The Last.fm API key and where it came from: Settings page first, then LASTFM_API_KEY."""
    stored = await _get(db, LASTFM_KEY) or {}
    if encrypted := stored.get("api_key_enc"):
        try:
            return _cipher(settings).decrypt(encrypted, purpose=LASTFM_PURPOSE), "settings"
        except DecryptionError:
            pass  # APP_SECRET_KEY changed; fall back to the environment
    if settings.lastfm_api_key and (env := settings.lastfm_api_key.get_secret_value().strip()):
        return env, "environment"
    return None, None


async def get_lastfm_shared_secret(db: AsyncSession, settings: Settings) -> str | None:
    """The shared secret that goes with the API key (scrobbling needs both): Settings page
    first, then LASTFM_SHARED_SECRET."""
    stored = await _get(db, LASTFM_KEY) or {}
    if encrypted := stored.get("shared_secret_enc"):
        try:
            return _cipher(settings).decrypt(encrypted, purpose=LASTFM_SECRET_PURPOSE)
        except DecryptionError:
            pass
    if settings.lastfm_shared_secret and (
        env := settings.lastfm_shared_secret.get_secret_value().strip()
    ):
        return env
    return None


async def set_lastfm_api_key(
    db: AsyncSession, settings: Settings, api_key: str | None, shared_secret: str | None = None
) -> None:
    """Store the key and/or its shared secret (a secret alone goes with the key from the
    environment); neither clears both."""
    value: dict[str, str] = {}
    if api_key:
        value["api_key_enc"] = _cipher(settings).encrypt(api_key, purpose=LASTFM_PURPOSE)
    if shared_secret:
        value["shared_secret_enc"] = _cipher(settings).encrypt(
            shared_secret, purpose=LASTFM_SECRET_PURPOSE
        )
    await _put(db, LASTFM_KEY, value)


# --- fanart.tv API key --------------------------------------------------------------------

FANART_KEY = "fanart"
FANART_PURPOSE = "instance.fanart_api_key"


async def get_fanart_api_key(
    db: AsyncSession, settings: Settings
) -> tuple[str | None, Literal["settings", "environment"] | None]:
    """The fanart.tv API key and where it came from: Settings page first, then FANART_API_KEY."""
    stored = await _get(db, FANART_KEY) or {}
    if encrypted := stored.get("api_key_enc"):
        try:
            return _cipher(settings).decrypt(encrypted, purpose=FANART_PURPOSE), "settings"
        except DecryptionError:
            pass
    if settings.fanart_api_key and (env := settings.fanart_api_key.get_secret_value().strip()):
        return env, "environment"
    return None, None


async def set_fanart_api_key(db: AsyncSession, settings: Settings, api_key: str | None) -> None:
    value = (
        {"api_key_enc": _cipher(settings).encrypt(api_key, purpose=FANART_PURPOSE)}
        if api_key
        else {}
    )
    await _put(db, FANART_KEY, value)


# --- AcoustID application key --------------------------------------------------------------

ACOUSTID_KEY = "acoustid"
ACOUSTID_PURPOSE = "instance.acoustid_api_key"


async def get_acoustid_api_key(
    db: AsyncSession, settings: Settings
) -> tuple[str | None, Literal["settings", "environment"] | None]:
    """The AcoustID key and where it came from: Settings page first, then ACOUSTID_API_KEY."""
    stored = await _get(db, ACOUSTID_KEY) or {}
    if encrypted := stored.get("api_key_enc"):
        try:
            return _cipher(settings).decrypt(encrypted, purpose=ACOUSTID_PURPOSE), "settings"
        except DecryptionError:
            pass
    if settings.acoustid_api_key and (env := settings.acoustid_api_key.get_secret_value().strip()):
        return env, "environment"
    return None, None


async def set_acoustid_api_key(db: AsyncSession, settings: Settings, api_key: str | None) -> None:
    value = (
        {"api_key_enc": _cipher(settings).encrypt(api_key, purpose=ACOUSTID_PURPOSE)}
        if api_key
        else {}
    )
    await _put(db, ACOUSTID_KEY, value)


# --- Tidal (album covers) ------------------------------------------------------------------

TIDAL_KEY = "tidal"
TIDAL_PURPOSE = "instance.tidal_client"


async def get_tidal_client(db: AsyncSession, settings: Settings) -> tuple[str, str] | None:
    """Tidal's client ID and secret (a free app on Tidal's developer platform), from the
    Settings page."""
    stored = await _get(db, TIDAL_KEY) or {}
    client_id, encrypted = stored.get("client_id"), stored.get("client_secret_enc")
    if not client_id or not encrypted:
        return None
    try:
        return str(client_id), _cipher(settings).decrypt(encrypted, purpose=TIDAL_PURPOSE)
    except DecryptionError:
        return None


async def set_tidal_client(
    db: AsyncSession, settings: Settings, client: tuple[str, str] | None
) -> None:
    value = (
        {
            "client_id": client[0],
            "client_secret_enc": _cipher(settings).encrypt(client[1], purpose=TIDAL_PURPOSE),
        }
        if client
        else {}
    )
    await _put(db, TIDAL_KEY, value)


# --- JamBase (Live) -------------------------------------------------------------------------

JAMBASE_KEY = "jambase"
JAMBASE_PURPOSE = "instance.jambase_api_key"
JAMBASE_USAGE_KEY = "jambase_usage"


async def get_jambase_api_key(
    db: AsyncSession, settings: Settings
) -> tuple[str | None, Literal["settings", "environment"] | None]:
    """The JamBase API key and where it came from: Settings page first, then JAMBASE_API_KEY."""
    stored = await _get(db, JAMBASE_KEY) or {}
    if encrypted := stored.get("api_key_enc"):
        try:
            return _cipher(settings).decrypt(encrypted, purpose=JAMBASE_PURPOSE), "settings"
        except DecryptionError:
            pass
    if settings.jambase_api_key and (env := settings.jambase_api_key.get_secret_value().strip()):
        return env, "environment"
    return None, None


async def set_jambase_api_key(db: AsyncSession, settings: Settings, api_key: str | None) -> None:
    value = (
        {"api_key_enc": _cipher(settings).encrypt(api_key, purpose=JAMBASE_PURPOSE)}
        if api_key
        else {}
    )
    await _put(db, JAMBASE_KEY, value)


async def _locked(db: AsyncSession, key: str) -> InstanceSetting:
    """The setting's row, locked until the transaction ends (created empty if missing)."""
    await db.execute(
        insert(InstanceSetting)
        .values(key=key, value={})
        .on_conflict_do_nothing(index_elements=[InstanceSetting.key])
    )
    row = await db.scalar(
        select(InstanceSetting).where(InstanceSetting.key == key).with_for_update()
    )
    assert row is not None
    return row


class JamBaseUsage(BaseModel):
    """Requests Plekstender sent to JamBase this calendar month (UTC), and JamBase's own
    count from GET /quota (which doesn't count against the quota) when last checked."""

    month: str = ""
    calls: int = 0
    quota: dict[str, Any] | None = None
    quota_checked_at: dt.datetime | None = None


def _month(now: dt.datetime) -> str:
    return now.astimezone(dt.UTC).strftime("%Y-%m")


async def get_jambase_usage(db: AsyncSession, now: dt.datetime | None = None) -> JamBaseUsage:
    stored = await _get(db, JAMBASE_USAGE_KEY) or {}
    try:
        usage = JamBaseUsage.model_validate(stored)
    except ValidationError:
        usage = JamBaseUsage()
    month = _month(now or dt.datetime.now(dt.UTC))
    if usage.month != month:
        usage.month, usage.calls = month, 0
    return usage


async def reserve_jambase_call(db: AsyncSession, cap: int, now: dt.datetime | None = None) -> bool:
    """Count one request before it's sent; False (nothing counted) once the cap is reached.
    The row is locked so concurrent refreshes can't both take the last request. Commits."""
    row = await _locked(db, JAMBASE_USAGE_KEY)
    usage = await get_jambase_usage(db, now)
    if usage.calls >= cap:
        await db.rollback()
        return False
    usage.calls += 1
    row.value = usage.model_dump(mode="json")
    await db.commit()
    return True


async def save_jambase_quota(
    db: AsyncSession, quota: dict[str, Any], now: dt.datetime | None = None
) -> None:
    usage = await get_jambase_usage(db, now)
    usage.quota, usage.quota_checked_at = quota, now or dt.datetime.now(dt.UTC)
    await _put(db, JAMBASE_USAGE_KEY, usage.model_dump(mode="json"))


JAMBASE_UNKNOWN_KEY = "jambase_unknown_ids"
# MusicBrainz IDs JamBase didn't know are searched by name, and tried again after this long.
JAMBASE_UNKNOWN_DAYS = 90


async def get_jambase_unknown_ids(db: AsyncSession, now: dt.datetime | None = None) -> set[str]:
    stored = (await _get(db, JAMBASE_UNKNOWN_KEY) or {}).get("ids") or {}
    since = ((now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=JAMBASE_UNKNOWN_DAYS)).date()
    return {mbid for mbid, day in stored.items() if str(day) >= since.isoformat()}


async def add_jambase_unknown_ids(
    db: AsyncSession, mbids: set[str], now: dt.datetime | None = None
) -> None:
    """Remember MusicBrainz IDs JamBase refused (older entries are dropped)."""
    if not mbids:
        return
    today = (now or dt.datetime.now(dt.UTC)).date()
    since = (today - dt.timedelta(days=JAMBASE_UNKNOWN_DAYS)).isoformat()
    stored = (await _get(db, JAMBASE_UNKNOWN_KEY) or {}).get("ids") or {}
    kept = {mbid: day for mbid, day in stored.items() if str(day) >= since}
    kept.update(dict.fromkeys(mbids, today.isoformat()))
    await _put(db, JAMBASE_UNKNOWN_KEY, {"ids": kept})


# --- Ticketmaster (Live) -------------------------------------------------------------------

TICKETMASTER_KEY = "ticketmaster"
TICKETMASTER_PURPOSE = "instance.ticketmaster_api_key"
TICKETMASTER_USAGE_KEY = "ticketmaster_usage"


async def get_ticketmaster_api_key(
    db: AsyncSession, settings: Settings
) -> tuple[str | None, Literal["settings", "environment"] | None]:
    """The Ticketmaster key: Settings page first, then TICKETMASTER_API_KEY."""
    stored = await _get(db, TICKETMASTER_KEY) or {}
    if encrypted := stored.get("api_key_enc"):
        try:
            return _cipher(settings).decrypt(encrypted, purpose=TICKETMASTER_PURPOSE), "settings"
        except DecryptionError:
            pass
    env = settings.ticketmaster_api_key
    if env and (value := env.get_secret_value().strip()):
        return value, "environment"
    return None, None


async def set_ticketmaster_api_key(
    db: AsyncSession, settings: Settings, api_key: str | None
) -> None:
    value = (
        {"api_key_enc": _cipher(settings).encrypt(api_key, purpose=TICKETMASTER_PURPOSE)}
        if api_key
        else {}
    )
    await _put(db, TICKETMASTER_KEY, value)


class TicketmasterUsage(BaseModel):
    """Requests Plekstender sent to Ticketmaster today (UTC)."""

    day: str = ""
    calls: int = 0


async def get_ticketmaster_usage(
    db: AsyncSession, now: dt.datetime | None = None
) -> TicketmasterUsage:
    try:
        usage = TicketmasterUsage.model_validate(await _get(db, TICKETMASTER_USAGE_KEY) or {})
    except ValidationError:
        usage = TicketmasterUsage()
    day = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).date().isoformat()
    if usage.day != day:
        usage.day, usage.calls = day, 0
    return usage


async def reserve_ticketmaster_call(
    db: AsyncSession, cap: int, now: dt.datetime | None = None
) -> bool:
    """Count one request before it's sent; False once today's cap is reached. Commits."""
    row = await _locked(db, TICKETMASTER_USAGE_KEY)
    usage = await get_ticketmaster_usage(db, now)
    if usage.calls >= cap:
        await db.rollback()
        return False
    usage.calls += 1
    row.value = usage.model_dump(mode="json")
    await db.commit()
    return True


# --- Data Thistle (Live) -------------------------------------------------------------------

DATATHISTLE_KEY = "datathistle"
DATATHISTLE_PURPOSE = "instance.datathistle_token"
DATATHISTLE_USAGE_KEY = "datathistle_usage"


class DataThistleTokens(BaseModel):
    access: str
    refresh: str
    access_expires: dt.datetime | None
    refresh_expires: dt.datetime | None
    renewed_at: dt.datetime | None = None
    error: str | None = None  # why renewing failed, the last time


async def get_datathistle_tokens(db: AsyncSession, settings: Settings) -> DataThistleTokens | None:
    stored = await _get(db, DATATHISTLE_KEY) or {}
    if not stored.get("access_enc") or not stored.get("refresh_enc"):
        return None
    cipher = _cipher(settings)
    try:
        access = cipher.decrypt(stored["access_enc"], purpose=DATATHISTLE_PURPOSE)
        refresh = cipher.decrypt(stored["refresh_enc"], purpose=DATATHISTLE_PURPOSE)
    except DecryptionError:
        return None

    def when(key: str) -> dt.datetime | None:
        return dt.datetime.fromisoformat(stored[key]) if stored.get(key) else None

    return DataThistleTokens(
        access=access,
        refresh=refresh,
        access_expires=when("access_expires"),
        refresh_expires=when("refresh_expires"),
        renewed_at=when("renewed_at"),
        error=stored.get("error"),
    )


async def set_datathistle_tokens(
    db: AsyncSession, settings: Settings, tokens: DataThistleTokens | None
) -> None:
    if tokens is None:
        await _put(db, DATATHISTLE_KEY, {})
        return
    cipher = _cipher(settings)

    def iso(value: dt.datetime | None) -> str | None:
        return value.isoformat() if value else None

    await _put(
        db,
        DATATHISTLE_KEY,
        {
            "access_enc": cipher.encrypt(tokens.access, purpose=DATATHISTLE_PURPOSE),
            "refresh_enc": cipher.encrypt(tokens.refresh, purpose=DATATHISTLE_PURPOSE),
            "access_expires": iso(tokens.access_expires),
            "refresh_expires": iso(tokens.refresh_expires),
            "renewed_at": iso(tokens.renewed_at),
            "error": tokens.error,
        },
    )


class DataThistleUsage(BaseModel):
    """Requests Plekstender sent today (UTC), and the month's quota as Data Thistle last
    reported it (every answer carries it)."""

    day: str = ""
    calls: int = 0
    limit: int | None = None
    remaining: int | None = None
    resets_at: dt.datetime | None = None


async def get_datathistle_usage(
    db: AsyncSession, now: dt.datetime | None = None
) -> DataThistleUsage:
    try:
        usage = DataThistleUsage.model_validate(await _get(db, DATATHISTLE_USAGE_KEY) or {})
    except ValidationError:
        usage = DataThistleUsage()
    day = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).date().isoformat()
    if usage.day != day:
        usage.day, usage.calls = day, 0
    return usage


async def reserve_datathistle_call(
    db: AsyncSession, cap: int, now: dt.datetime | None = None
) -> bool:
    """Count one request before it's sent; False once today's cap is reached, or when Data
    Thistle said none are left this month. Commits."""
    row = await _locked(db, DATATHISTLE_USAGE_KEY)
    usage = await get_datathistle_usage(db, now)
    moment = now or dt.datetime.now(dt.UTC)
    out_of_month = (
        usage.remaining is not None
        and usage.remaining <= 0
        and (usage.resets_at is None or usage.resets_at > moment)
    )
    if usage.calls >= cap or out_of_month:
        await db.rollback()
        return False
    usage.calls += 1
    row.value = usage.model_dump(mode="json")
    await db.commit()
    return True


async def save_datathistle_quota(
    db: AsyncSession, limit: int | None, remaining: int | None, resets_at: dt.datetime | None
) -> None:
    usage = await get_datathistle_usage(db)
    usage.limit, usage.remaining, usage.resets_at = limit, remaining, resets_at
    await _put(db, DATATHISTLE_USAGE_KEY, usage.model_dump(mode="json"))


# --- setlist.fm (gig imports) --------------------------------------------------------------

SETLISTFM_KEY = "setlistfm"
SETLISTFM_PURPOSE = "instance.setlistfm_api_key"


async def get_setlistfm_api_key(
    db: AsyncSession, settings: Settings
) -> tuple[str | None, Literal["settings", "environment"] | None]:
    """The setlist.fm key: Settings page first, then SETLISTFM_API_KEY."""
    stored = await _get(db, SETLISTFM_KEY) or {}
    if encrypted := stored.get("api_key_enc"):
        try:
            return _cipher(settings).decrypt(encrypted, purpose=SETLISTFM_PURPOSE), "settings"
        except DecryptionError:
            pass
    env = settings.setlistfm_api_key
    if env and (value := env.get_secret_value().strip()):
        return value, "environment"
    return None, None


async def set_setlistfm_api_key(db: AsyncSession, settings: Settings, api_key: str | None) -> None:
    value = (
        {"api_key_enc": _cipher(settings).encrypt(api_key, purpose=SETLISTFM_PURPOSE)}
        if api_key
        else {}
    )
    await _put(db, SETLISTFM_KEY, value)


# --- Spotify (playlist imports) ------------------------------------------------------------

SPOTIFY_KEY = "spotify"
SPOTIFY_PURPOSE = "instance.spotify_secret"


async def get_spotify_app(
    db: AsyncSession, settings: Settings
) -> tuple[str, str, Literal["settings", "environment"]] | None:
    """The Spotify app's client ID and secret: Settings page first, then the environment."""
    stored = await _get(db, SPOTIFY_KEY) or {}
    if stored.get("client_id") and stored.get("secret_enc"):
        try:
            secret = _cipher(settings).decrypt(stored["secret_enc"], purpose=SPOTIFY_PURPOSE)
            return str(stored["client_id"]), secret, "settings"
        except DecryptionError:
            pass
    env_secret = settings.spotify_client_secret
    if (
        settings.spotify_client_id
        and env_secret
        and (value := env_secret.get_secret_value().strip())
    ):
        return settings.spotify_client_id.strip(), value, "environment"
    return None


async def set_spotify_app(
    db: AsyncSession, settings: Settings, client_id: str | None, secret: str | None
) -> None:
    value = (
        {
            "client_id": client_id,
            "secret_enc": _cipher(settings).encrypt(secret, purpose=SPOTIFY_PURPOSE),
        }
        if client_id and secret
        else {}
    )
    await _put(db, SPOTIFY_KEY, value)


# --- text logo fonts ------------------------------------------------------------------------

FONTS_KEY = "fonts"


async def get_fonts(db: AsyncSession) -> dict[str, list[str]]:
    """The Google Fonts the text logo offers, by group (the default list until changed)."""
    from plekstender.artwork.text import FONTS, GROUPS

    stored = await _get(db, FONTS_KEY)
    if not stored or not isinstance(stored.get("groups"), dict):
        return {group: list(names) for group, names in FONTS.items()}
    groups = stored["groups"]
    return {g: sorted(str(n) for n in groups.get(g, [])) for g in GROUPS}


async def set_fonts(db: AsyncSession, groups: dict[str, list[str]] | None) -> None:
    """Save the font list (None restores the default)."""
    await _put(db, FONTS_KEY, {} if groups is None else {"groups": groups})
