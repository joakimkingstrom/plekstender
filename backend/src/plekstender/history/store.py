"""Writing listens."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Listen, ListenImport
from plekstender.history.lastfm import Scrobble
from plekstender.sync.parse import normalise_artist, normalise_title


def scrobble_row(user_id: int, import_id: int | None, source: str, s: Scrobble) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "import_id": import_id,
        "source": source,
        "listened_at": s.listened_at,
        "artist_name": s.artist,
        "album_name": s.album,
        "track_name": s.track,
        "artist_key": normalise_artist(s.artist),
        "album_key": normalise_title(s.album) if s.album else None,
        "track_key": normalise_title(s.track),
        "artist_mbid": s.artist_mbid,
        "release_mbid": s.release_mbid,
        "recording_mbid": s.recording_mbid,
    }


async def insert_listens(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Insert listens, skipping ones already stored. Returns how many were new."""
    if not rows:
        return 0
    stmt = (
        insert(Listen)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_listens_play")
        .returning(Listen.id)
    )
    return len((await db.execute(stmt)).all())


async def stopped_import(db: AsyncSession, job: ListenImport, cancel: bool) -> str:
    """Wind up an import asked to stop: cancelled, the plays it added are removed again (the
    ones it found already there stay). What to tell the user. Caller commits."""
    if not cancel:
        return "Stopped. What it imported is kept; resuming carries on from there."
    result = await db.execute(
        delete(Listen).where(Listen.user_id == job.user_id, Listen.import_id == job.id)
    )
    removed = int(getattr(result, "rowcount", 0) or 0)
    job.stats = {**job.stats, "added": 0}
    job.progress = {}
    return (
        f"Cancelled: the {removed} {'play' if removed == 1 else 'plays'} it imported were removed."
    )
