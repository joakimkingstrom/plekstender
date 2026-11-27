"""Putting back what an artwork job changed, when it's cancelled.

Before a job replaces an album's cover or an artist's picture in Plex, it notes the poster
Plex had selected (Plex keeps every poster an item has had, so it can be selected again)
and Plekstender's record of it. Cancelling selects those posters again and restores the
records. An artist.jpg already saved into an artist's folder stays.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import AlbumCover, ArtistArtwork, ArtworkJob
from plekstender.plex.server import PlexServerClient
from plekstender.plex.tv import PlexError

log = logging.getLogger(__name__)

_COVER_FIELDS = ("source", "source_url", "applied_at", "applied_by")
_ARTIST_FIELDS = (
    "status",
    "source",
    "source_url",
    "settings",
    "image_sha256",
    "applied_at",
    "updated_by",
)


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, dt.datetime) else value


def _restored(field: str, value: Any) -> Any:
    if field == "applied_at" and isinstance(value, str):
        return dt.datetime.fromisoformat(value)
    return value


async def selected_poster(client: PlexServerClient, rating_key: int) -> str | None:
    """The poster Plex has selected for an item (to select again later), if it says."""
    try:
        posters = await client.posters(rating_key)
    except PlexError:
        return None
    for poster in posters:
        if poster.get("selected"):
            ref = poster.get("ratingKey") or poster.get("key")
            return str(ref) if ref else None
    return None


async def remember(
    db: AsyncSession, client: PlexServerClient, job: ArtworkJob, kind: str, rating_key: int
) -> dict[str, Any]:
    """What an album ("albums") or artist ("artists") looks like before the job changes it.
    Pass the result to `keep` once the change is made."""
    model = AlbumCover if kind == "albums" else ArtistArtwork
    fields = _COVER_FIELDS if kind == "albums" else _ARTIST_FIELDS
    row = await db.get(model, (job.server_id, rating_key))
    return {
        "poster": await selected_poster(client, rating_key),
        "record": {f: _plain(getattr(row, f)) for f in fields} if row is not None else None,
    }


def keep(job: ArtworkJob, kind: str, rating_key: int, before: dict[str, Any]) -> None:
    """Note what an item was before the job changed it (the first time only)."""
    undo = dict(job.undo or {})
    items = dict(undo.get(kind) or {})
    items.setdefault(str(rating_key), before)
    undo[kind] = items
    job.undo = undo


async def put_back(db: AsyncSession, client: PlexServerClient, job: ArtworkJob) -> int:
    """Select the posters the job replaced again and restore their records. Returns how many
    items were put back. Caller commits."""
    restored = 0
    for kind, model, fields in (
        ("albums", AlbumCover, _COVER_FIELDS),
        ("artists", ArtistArtwork, _ARTIST_FIELDS),
    ):
        for key, before in ((job.undo or {}).get(kind) or {}).items():
            rating_key = int(key)
            if before.get("poster"):
                try:
                    await client.select_poster(rating_key, before["poster"])
                except PlexError as exc:
                    log.info("couldn't put back the poster of %s: %s", rating_key, exc)
                    continue
            row = await db.get(model, (job.server_id, rating_key))
            if row is not None:
                record = before.get("record") or {}
                for field in fields:
                    if field == "status" and "status" not in record:
                        setattr(row, field, "none")  # there was no record: not looked at
                    elif field == "settings" and "settings" not in record:
                        setattr(row, field, {})
                    else:
                        setattr(row, field, _restored(field, record.get(field)))
            restored += 1
    return restored
