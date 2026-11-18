"""Writing listens."""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from roadiefm.db.models import Listen
from roadiefm.history.lastfm import Scrobble
from roadiefm.sync.parse import normalise_artist, normalise_title


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
