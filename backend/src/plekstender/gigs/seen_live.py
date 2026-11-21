"""Songs seen live: the songs in the setlists of the gigs someone went to.

Matched by name: the performing artist and the song's title, both normalised (so a
remaster or "Live at…" release of the song counts too). Songs played from tape don't count.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Gig
from plekstender.sync.parse import normalise_artist, normalise_title


async def seen_live(db: AsyncSession, user_id: int) -> dict[str, Counter[str]]:
    """For each artist (normalised name), how many gigs each song (normalised title) was
    played at."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    rows = await db.scalars(
        select(Gig.setlists).where(Gig.user_id == user_id, Gig.setlists != text("'[]'::jsonb"))
    )
    for setlists in rows:
        for setlist in setlists or []:
            artist = normalise_artist(str(setlist.get("artist") or ""))
            titles = {
                normalise_title(str(song.get("name") or ""))
                for song in setlist.get("songs") or []
                if song.get("name") and not song.get("tape")
            }
            out[artist].update(titles)  # once per gig, however often it was played that night
    return out


def times_seen(seen: dict[str, Counter[str]], artists: list[str | None], title: str) -> int:
    """How many gigs a song was played at, by any of its artists (album or track artist)."""
    key = normalise_title(title)
    return max(
        (seen[normalise_artist(a)][key] for a in artists if a and normalise_artist(a) in seen),
        default=0,
    )
