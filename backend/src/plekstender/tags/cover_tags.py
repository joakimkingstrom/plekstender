"""Writing whose song a cover is into the music files: the "original artist" tag (ID3's
TOPE frame; ORIGINALARTIST in FLAC, Ogg and MP4), from the cover songs found (see
plekstender.cover_songs). Only files matched to a Plex track whose song is a cover with a
known original artist, and whose tag says something else; recorded as one edit, so it can
be undone.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import MediaFile, SongCover, Track
from plekstender.tags.plexsync import Planned
from plekstender.tags.read import unread_fields
from plekstender.tags.write import clean

FIELD = "originalartist"


async def plan(db: AsyncSession, server_id: int) -> list[Planned]:
    """Every file whose original artist tag should change."""
    rows = await db.execute(
        select(
            MediaFile.id,
            MediaFile.path,
            MediaFile.tags,
            MediaFile.reader,
            Track.rating_key,
            SongCover.original_artist,
        )
        .join(
            Track,
            (Track.server_id == MediaFile.server_id) & (Track.rating_key == MediaFile.rating_key),
        )
        .join(
            SongCover,
            (SongCover.artist_key == Track.artist_key) & (SongCover.title_norm == Track.title_norm),
        )
        .where(
            MediaFile.server_id == server_id,
            MediaFile.error.is_(None),
            SongCover.status == "cover",
            SongCover.original_artist.is_not(None),
        )
        .order_by(MediaFile.path)
    )
    out: list[Planned] = []
    for file_id, path, tags, reader, rating_key, original_artist in rows.all():
        new = clean(FIELD, original_artist)
        old = clean(FIELD, (tags or {}).get(FIELD))
        unknown = FIELD in unread_fields(reader)
        if new != old or unknown:
            out.append(Planned(file_id, path, rating_key, FIELD, old, new, unknown))
    return out
