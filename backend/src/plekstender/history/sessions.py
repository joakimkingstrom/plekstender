"""The session watcher: what's playing in Plex, polled every few seconds.

Plex's play history only records finished plays. Watching `/status/sessions` shows how
far each track got (a short play followed straight away by another song is a skip), which
player it was on, and from where — and that's attached to the matching listen once Plex
records the play.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import PlaySession, UserServer
from plekstender.history.geo import geo_lookup
from plekstender.history.places import Placer
from plekstender.history.plex_capture import account_map
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError
from plekstender.security.crypto import TokenCipher

log = logging.getLogger(__name__)

POLL_INTERVAL = 10.0  # seconds
POLLS_PER_RUN = 5  # one run per minute (the scheduler's resolution)
# A track picked up again where it stopped (paused long enough for Plex to drop the session)
# carries on the same play, so a pause never looks like a skip.
RESUME_WITHIN = dt.timedelta(minutes=30)
RESUME_FROM_MS = 15_000  # nearer the start than this, it's played again from the top


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def describe_player(player: dict[str, Any]) -> str | None:
    """ "Joakim's iPhone · Plexamp" (device name and app)."""
    parts = [str(player[k]) for k in ("title", "product") if player.get(k)]
    if len(parts) == 2 and parts[0] == parts[1]:
        parts = parts[:1]
    return " · ".join(parts) or None


def client_for(settings: Settings, http: httpx.AsyncClient, us: UserServer) -> PlexServerClient:
    cipher = TokenCipher(settings.derive_key("token-encryption"))
    token = cipher.decrypt(us.access_token_enc, purpose="user_server.access_token")
    return PlexServerClient(settings, http, us.connection_uri, token)


async def poll_once(
    db: AsyncSession,
    client: PlexServerClient,
    settings: Settings,
    us: UserServer,
    accounts: dict[int, int],
    now: dt.datetime,
) -> int:
    """Record what's playing now and close sessions that stopped. Returns tracks seen."""
    try:
        playing = [p for p in await client.sessions() if p.get("type") == "track"]
    except (httpx.HTTPError, PlexError) as exc:
        log.debug("sessions on server %s unavailable: %s", us.server_id, exc)
        return 0
    geo = geo_lookup(settings)
    placer = Placer(db, geo)
    seen: set[int] = set()
    for item in playing:
        user_id = accounts.get(_int((item.get("User") or {}).get("id")) or -1)
        rating_key = _int(item.get("ratingKey"))
        session_key = str(item.get("sessionKey") or "")
        if user_id is None or rating_key is None or not session_key:
            continue
        offset = _int(item.get("viewOffset")) or 0
        player = item.get("Player") or {}
        session = item.get("Session") or {}
        row = await db.scalar(
            select(PlaySession).where(
                PlaySession.server_id == us.server_id,
                PlaySession.session_key == session_key,
                PlaySession.rating_key == rating_key,
                PlaySession.ended_at.is_(None),
            )
        )
        if row is None and offset >= RESUME_FROM_MS:
            row = await db.scalar(
                select(PlaySession)
                .where(
                    PlaySession.server_id == us.server_id,
                    PlaySession.user_id == user_id,
                    PlaySession.rating_key == rating_key,
                    PlaySession.client.is_not_distinct_from(describe_player(player)),
                    PlaySession.ended_at >= now - RESUME_WITHIN,
                    PlaySession.max_offset_ms <= offset + 5_000,
                )
                .order_by(PlaySession.ended_at.desc())
                .limit(1)
            )
            if row is not None:
                row.ended_at = None
                row.session_key = session_key
        if row is None:
            address = player.get("remotePublicAddress") or player.get("address")
            network = session.get("location") or ("lan" if player.get("local") else "wan")
            row = PlaySession(
                server_id=us.server_id,
                user_id=user_id,
                session_key=session_key,
                rating_key=rating_key,
                started_at=now - dt.timedelta(milliseconds=offset),
                last_seen_at=now,
                max_offset_ms=offset,
                duration_ms=_int(item.get("duration")),
                client=describe_player(player),
                address=str(address)[:64] if address else None,
                network=str(network)[:8] if network else None,
                location="Local network" if network == "lan" else geo.lookup(address),
            )
            place = await placer.place(us.server_id, row.address, row.network)
            if place is not None:
                row.country_code = place.country_code
                row.continent_code = place.continent_code
            db.add(row)
            await db.flush()
        else:
            row.last_seen_at = now
            row.max_offset_ms = max(row.max_offset_ms, offset)
        seen.add(row.id)
    # Anything no longer playing has ended (at the last time it was seen).
    await db.execute(
        update(PlaySession)
        .where(
            PlaySession.server_id == us.server_id,
            PlaySession.ended_at.is_(None),
            PlaySession.id.not_in(seen) if seen else PlaySession.id.is_not(None),
        )
        .values(ended_at=PlaySession.last_seen_at)
    )
    await db.commit()
    return len(seen)


async def watch(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    connections: list[UserServer],
    polls: int,
    sleep: Any,
) -> None:
    """Poll every connection `polls` times, POLL_INTERVAL seconds apart."""
    targets = [
        (us, client_for(settings, http, us), await account_map(db, us)) for us in connections
    ]
    for n in range(polls):
        for us, client, accounts in targets:
            try:
                await poll_once(db, client, settings, us, accounts, dt.datetime.now(dt.UTC))
            except Exception:
                log.exception("watching sessions on server %s failed", us.server_id)
                await db.rollback()
        if n < polls - 1:
            await sleep(POLL_INTERVAL)


# Give captured Plex listens the player and place of the matching session.
_ATTACH = text(
    """
    UPDATE listens l SET (client, address, network, location, country_code, continent_code) = (
        SELECT ps.client, ps.address, ps.network, ps.location, ps.country_code,
               ps.continent_code
        FROM play_sessions ps
        WHERE ps.user_id = l.user_id AND ps.server_id = l.server_id
          AND ps.rating_key = l.rating_key
          AND ps.started_at BETWEEN l.listened_at - interval '15 minutes'
                                AND l.listened_at + interval '15 minutes'
        ORDER BY abs(extract(epoch FROM ps.started_at - l.listened_at))
        LIMIT 1
    )
    WHERE l.user_id = :user_id AND l.source = 'plex' AND l.client IS NULL
      AND l.listened_at >= :since
      AND EXISTS (
          SELECT 1 FROM play_sessions ps
          WHERE ps.user_id = l.user_id AND ps.server_id = l.server_id
            AND ps.rating_key = l.rating_key
            AND ps.started_at BETWEEN l.listened_at - interval '15 minutes'
                                  AND l.listened_at + interval '15 minutes'
      )
    """
)


async def attach_sessions(db: AsyncSession, user_id: int, since: dt.datetime) -> None:
    await db.execute(_ATTACH, {"user_id": user_id, "since": since - dt.timedelta(minutes=15)})
