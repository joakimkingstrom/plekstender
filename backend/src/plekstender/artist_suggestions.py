"""Artists to add: the artists Plex names as "similar" to many of the library's artists,
that the library doesn't have yet.

The more library artists name one, the more likely it fits the collection; the person's
own listens to them (scrobbles count even for artists not in the library) say so too.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.api.routes.library import plays_of, visible
from plekstender.db.models import Artist, HiddenSuggestion, Listen, SimilarArtist, UserServer
from plekstender.sync.parse import normalise_artist

# Library artists listed with each suggestion (the ones played most first).
SOURCES_SHOWN = 8


@dataclass
class Source:
    name: str
    rating_key: int
    plays: int


@dataclass
class Suggestion:
    key: str  # normalise_artist(name)
    name: str
    sources: list[Source] = field(default_factory=list)
    listens: int = 0

    @property
    def similar_to(self) -> int:
        return len(self.sources)


async def suggestions(
    db: AsyncSession, user_id: int, us: UserServer, *, hidden: bool = False
) -> list[Suggestion]:
    """Artists not in the library, by how many library artists they're similar to (then by
    the person's listens to them). `hidden`: the ones the person has hidden instead."""
    state, cond, where = visible(Artist, user_id, us)
    library = {
        rating_key: (title, int(plays or 0))
        for rating_key, title, plays in await db.execute(
            select(Artist.rating_key, Artist.title, plays_of(state)).join(state, cond).where(*where)
        )
    }
    in_library = {normalise_artist(title) for title, _ in library.values() if title}
    hidden_keys = set(
        await db.scalars(
            select(HiddenSuggestion.artist_key).where(HiddenSuggestion.user_id == user_id)
        )
    )

    found: dict[str, Suggestion] = {}
    spellings: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    rows = await db.execute(
        select(SimilarArtist.artist_rating_key, SimilarArtist.name).where(
            SimilarArtist.server_id == us.server_id
        )
    )
    for artist_key, name in rows.all():
        source = library.get(artist_key)
        key = normalise_artist(name or "")
        if source is None or not key or key in in_library or (key in hidden_keys) != hidden:
            continue
        suggestion = found.setdefault(key, Suggestion(key, name))
        if all(s.rating_key != artist_key for s in suggestion.sources):
            suggestion.sources.append(Source(source[0], artist_key, source[1]))
        spellings[key][name] += 1
    if not found:
        return []

    listens = await db.execute(
        select(Listen.artist_key, func.count())
        .where(
            Listen.user_id == user_id,
            Listen.duplicate_of.is_(None),
            Listen.artist_key.in_(list(found)),
        )
        .group_by(Listen.artist_key)
    )
    for key, count in listens.all():
        found[key].listens = int(count)
    for key, suggestion in found.items():
        # The spelling Plex uses most (capitalised over not, when as common); the best-loved
        # sources first.
        suggestion.name = max(
            spellings[key].items(), key=lambda x: (x[1], not x[0].islower(), x[0])
        )[0]
        suggestion.sources.sort(key=lambda s: (-s.plays, s.name.lower()))
    return sorted(found.values(), key=lambda s: (-s.similar_to, -s.listens, s.name.lower()))
