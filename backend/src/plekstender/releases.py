"""Artists' releases from MusicBrainz, for Library -> Releases: what's new from the artists
someone follows, and what's missing from the library.

The worker reads each library artist's release groups (by MusicBrainz ID) into
`release_groups`: artists people follow every day, the rest every two weeks. Like the
country lookups it goes at half MusicBrainz's pace, leaving room for the tag editor.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import musicbrainz
from plekstender.db.models import ArtistReleaseCheck, ReleaseGroup
from plekstender.sync.parse import normalise_title

log = logging.getLogger(__name__)

BATCH = 300  # artists a run (about ten minutes)
PAUSE = 1.0  # seconds, on top of MusicBrainz's own one-a-second spacing
FOLLOWED_EVERY = dt.timedelta(days=1)
OTHERS_EVERY = dt.timedelta(days=14)
# Someone follows the artists they rated FOLLOW_RATING or more, and their FOLLOW_TOP most
# played: unless they've chosen otherwise (Library -> Releases settings, stored with their
# preferences; 0 turns either off).
FOLLOW_RATING = 8
FOLLOW_TOP = 50
KINDS = ("album", "ep", "single", "live", "compilation", "other")
_DATE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")

# Artists someone follows: (user_id, server_id, rating_key) of library artists.
FOLLOWED = """
    followed AS (
        SELECT user_id, server_id, rating_key FROM (
            SELECT s.user_id, s.server_id, s.rating_key, s.user_rating,
                   row_number() OVER (
                       PARTITION BY s.user_id
                       ORDER BY greatest(s.view_count, s.listens) DESC, s.rating_key
                   ) AS rank,
                   greatest(s.view_count, s.listens) AS plays,
                   coalesce(
                       (u.preferences #>> '{releases,follow_rating}')::int, :follow_rating
                   ) AS min_rating,
                   coalesce((u.preferences #>> '{releases,follow_top}')::int, :follow_top) AS top
            FROM user_item_state s
            JOIN users u ON u.id = s.user_id
            WHERE s.item_type = 'artist'
        ) ranked
        WHERE (min_rating > 0 AND user_rating >= min_rating) OR (rank <= top AND plays > 0)
    )
"""


def classify(primary: str | None, secondary: list[str]) -> str:
    """One kind per release group: live and compilations first (a live album is "live")."""
    if "Live" in secondary:
        return "live"
    if "Compilation" in secondary:
        return "compilation"
    if secondary or primary not in ("Album", "EP", "Single"):
        return "other"
    return primary.lower()


def release_date(value: str | None) -> dt.date | None:
    """ "2019-05-21", "2019-05" or "2019" as a date (the 1st when the day or month is unknown)."""
    match = _DATE.match(value or "")
    if not match:
        return None
    year, month, day = (int(part) if part else 1 for part in match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def rows_for(artist_mbid: str, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for g in groups:
        mbid, title = g.get("id"), g.get("title")
        if not isinstance(mbid, str) or not musicbrainz.MBID.fullmatch(mbid) or not title:
            continue
        secondary = [str(t) for t in g.get("secondary-types") or []]
        primary = g.get("primary-type")
        date = (g.get("first-release-date") or "")[:10] or None
        rows[mbid] = {
            "artist_mbid": artist_mbid,
            "mbid": mbid,
            "title": str(title),
            "title_norm": normalise_title(str(title)),
            "kind": classify(primary, secondary),
            "primary_type": primary,
            "secondary_types": secondary,
            "first_release_date": date,
            "released": release_date(date),
        }
    return list(rows.values())


_TO_CHECK = text(
    f"""
    WITH {FOLLOWED},
    artist_mbids AS (
        SELECT ar.mbid,
               bool_or(f.user_id IS NOT NULL) AS followed
        FROM artists ar
        LEFT JOIN followed f ON f.server_id = ar.server_id AND f.rating_key = ar.rating_key
        WHERE ar.mbid ~ '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
          AND lower(btrim(ar.title)) NOT IN ('various artists', 'various', 'va')
        GROUP BY ar.mbid
    )
    SELECT a.mbid
    FROM artist_mbids a
    LEFT JOIN artist_release_checks c ON c.artist_mbid = a.mbid
    WHERE c.checked_at IS NULL
       OR c.checked_at < :now - CASE WHEN a.followed THEN :followed_every ELSE :others_every END
    ORDER BY c.checked_at IS NOT NULL, a.followed DESC, c.checked_at, a.mbid
    LIMIT :limit
    """  # noqa: S608 - constant SQL fragment
)


async def to_check(db: AsyncSession, now: dt.datetime, limit: int = BATCH) -> list[str]:
    """Artist MBIDs due a look: never read first, then those people follow."""
    rows = await db.scalars(
        _TO_CHECK,
        {
            "now": now,
            "followed_every": FOLLOWED_EVERY,
            "others_every": OTHERS_EVERY,
            "follow_rating": FOLLOW_RATING,
            "follow_top": FOLLOW_TOP,
            "limit": limit,
        },
    )
    return list(rows.all())


# Rows saved per statement: some artists have thousands of release groups (live recordings),
# and a statement takes at most 65,535 values.
STORE_CHUNK = 1000
# MusicBrainz errors in a row before a run stops (it's down or busy); one artist's error only
# skips that artist.
MAX_ERRORS = 3


async def store(db: AsyncSession, artist_mbid: str, groups: list[dict[str, Any]] | None) -> None:
    """Replace an artist's release groups (None: MusicBrainz doesn't know the artist)."""
    await db.execute(delete(ReleaseGroup).where(ReleaseGroup.artist_mbid == artist_mbid))
    rows = rows_for(artist_mbid, groups or [])
    for start in range(0, len(rows), STORE_CHUNK):
        await db.execute(insert(ReleaseGroup).values(rows[start : start + STORE_CHUNK]))
    stmt = insert(ArtistReleaseCheck).values(
        artist_mbid=artist_mbid, checked_at=dt.datetime.now(dt.UTC), found=groups is not None
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["artist_mbid"],
            set_={"checked_at": stmt.excluded.checked_at, "found": stmt.excluded.found},
        )
    )


async def look_up(db: AsyncSession, http: httpx.AsyncClient, limit: int = BATCH) -> int:
    """Read due artists' release groups from MusicBrainz; returns how many artists were read.
    An artist that fails is skipped (tried again next run); the run stops when MusicBrainz
    fails a few times in a row (unreachable or busy: the rest wait for the next run)."""
    done = 0
    errors = 0
    for n, artist_mbid in enumerate(await to_check(db, dt.datetime.now(dt.UTC), limit)):
        if n:
            await asyncio.sleep(PAUSE)
        try:
            groups: list[dict[str, Any]] | None = await musicbrainz.release_groups(
                http, artist_mbid
            )
        except musicbrainz.NotFound:
            groups = None
        except musicbrainz.MusicBrainzError as exc:
            errors += 1
            log.info("MusicBrainz releases of %s: %s", artist_mbid, exc)
            if errors >= MAX_ERRORS:
                log.info("MusicBrainz release lookups paused after %d errors in a row", errors)
                break
            continue
        errors = 0
        try:
            await store(db, artist_mbid, groups)
            await db.commit()
        except Exception:
            log.exception("couldn't save the releases of %s", artist_mbid)
            await db.rollback()
            continue
        done += 1
    return done


# --- what people see -----------------------------------------------------------------------

NEW_FOR = dt.timedelta(days=90)  # how long a release counts as new


@dataclass
class Release:
    mbid: str
    title: str
    kind: str
    first_release_date: str | None
    released: dt.date | None
    in_library: bool
    hidden: bool


@dataclass
class ArtistReleases:
    rating_key: int
    name: str
    thumb: str | None
    releases: list[Release] = field(default_factory=list)


@dataclass
class Coverage:
    artists: int  # library artists you can see (not "Various Artists")
    with_mbid: int  # ... that Plex has a MusicBrainz ID for
    checked: int  # ... whose releases have been read


# A server's albums, by MusicBrainz release group (Plex's, or the one Library -> Artwork
# found by name) and by artist and normalised title.
_LIBRARY = """
    lib AS (
        SELECT mbid FROM albums WHERE server_id = :server_id AND mbid IS NOT NULL
        UNION
        SELECT mbid FROM album_covers WHERE server_id = :server_id AND mbid IS NOT NULL
    ),
    lib_titles AS (
        SELECT DISTINCT artist_rating_key, title_norm FROM albums WHERE server_id = :server_id
    )
"""

_RELEASE_COLUMNS = """
    rg.mbid, rg.title, rg.kind, rg.first_release_date, rg.released,
    (lib.mbid IS NOT NULL OR lt.title_norm IS NOT NULL) AS in_library,
    EXISTS (
        SELECT 1 FROM hidden_releases h WHERE h.user_id = :user_id AND h.mbid = rg.mbid
    ) AS hidden
"""

_RELEASE_JOINS = """
    LEFT JOIN lib ON lib.mbid = rg.mbid
    LEFT JOIN lib_titles lt
      ON lt.artist_rating_key = a.rating_key AND lt.title_norm = rg.title_norm
"""

_MINE = """
    mine AS (
        SELECT ar.rating_key, ar.title, ar.thumb, ar.mbid,
               greatest(s.view_count, s.listens) AS plays
        FROM artists ar
        JOIN user_item_state s
          ON s.user_id = :user_id AND s.server_id = ar.server_id AND s.rating_key = ar.rating_key
        WHERE ar.server_id = :server_id AND ar.section_key = ANY(:sections)
          AND ar.mbid IS NOT NULL
          AND (CAST(:q AS text) IS NULL OR ar.title ILIKE :q ESCAPE '\\')
    )
"""

_NEW = text(
    f"""
    WITH {FOLLOWED}, {_LIBRARY},
    a AS (
        SELECT DISTINCT ar.rating_key, ar.title, ar.thumb, ar.mbid
        FROM followed f
        JOIN artists ar ON ar.server_id = f.server_id AND ar.rating_key = f.rating_key
        WHERE f.user_id = :user_id AND f.server_id = :server_id
          AND ar.section_key = ANY(:sections) AND ar.mbid IS NOT NULL
    )
    SELECT DISTINCT ON (rg.mbid) {_RELEASE_COLUMNS},
           a.rating_key AS artist_rating_key, a.title AS artist, a.thumb AS artist_thumb
    FROM release_groups rg
    JOIN a ON a.mbid = rg.artist_mbid
    {_RELEASE_JOINS}
    WHERE rg.released >= :since AND rg.kind = ANY(:kinds)
    ORDER BY rg.mbid, a.rating_key
    """  # noqa: S608 - constant SQL fragments
)

_MISSING = text(
    f"""
    WITH {_LIBRARY}, {_MINE},
    missing AS (
        SELECT a.rating_key AS artist_rating_key, a.title AS artist, a.thumb AS artist_thumb,
               a.plays, {_RELEASE_COLUMNS}
        FROM mine a
        JOIN release_groups rg ON rg.artist_mbid = a.mbid
        {_RELEASE_JOINS}
        WHERE rg.kind = ANY(:kinds) AND (rg.released IS NULL OR rg.released <= :today)
    ),
    shown AS (
        SELECT * FROM missing
        WHERE NOT in_library AND (:include_hidden OR NOT hidden)
    ),
    page AS (
        SELECT artist_rating_key, artist, plays, count(*) OVER () AS total
        FROM shown
        GROUP BY artist_rating_key, artist, plays
        ORDER BY plays DESC, artist, artist_rating_key
        LIMIT :limit OFFSET :offset
    )
    SELECT shown.*, page.total
    FROM shown JOIN page USING (artist_rating_key)
    ORDER BY page.plays DESC, page.artist, page.artist_rating_key,
             shown.released NULLS LAST, shown.title
    """  # noqa: S608 - constant SQL fragments
)

_COVERAGE = text(
    """
    SELECT count(*) AS artists,
           count(*) FILTER (
               WHERE ar.mbid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           ) AS with_mbid,
           count(c.artist_mbid) AS checked
    FROM artists ar
    JOIN user_item_state s
      ON s.user_id = :user_id AND s.server_id = ar.server_id AND s.rating_key = ar.rating_key
    LEFT JOIN artist_release_checks c ON c.artist_mbid = ar.mbid
    WHERE ar.server_id = :server_id AND ar.section_key = ANY(:sections)
      AND lower(btrim(ar.title)) NOT IN ('various artists', 'various', 'va')
    """
)


def _release(r: Any) -> Release:
    return Release(
        mbid=r.mbid,
        title=r.title,
        kind=r.kind,
        first_release_date=r.first_release_date,
        released=r.released,
        in_library=r.in_library,
        hidden=r.hidden,
    )


def _like(q: str | None) -> str | None:
    if not q or not q.strip():
        return None
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def new_releases(
    db: AsyncSession,
    user_id: int,
    server_id: int,
    sections: list[str],
    kinds: list[str],
    today: dt.date,
    include_hidden: bool = False,
) -> list[tuple[Release, ArtistReleases]]:
    """Releases from the last 90 days and coming up, by artists the user follows: newest
    first (coming ones after)."""
    rows = await db.execute(
        _NEW,
        {
            "user_id": user_id,
            "server_id": server_id,
            "sections": sections,
            "kinds": kinds,
            "since": today - NEW_FOR,
            "follow_rating": FOLLOW_RATING,
            "follow_top": FOLLOW_TOP,
        },
    )
    out = [
        (_release(r), ArtistReleases(r.artist_rating_key, r.artist, r.artist_thumb))
        for r in rows.all()
        if include_hidden or not r.hidden
    ]
    out.sort(key=lambda pair: (pair[0].released or today, pair[0].title), reverse=True)
    return out


async def missing_releases(
    db: AsyncSession,
    user_id: int,
    server_id: int,
    sections: list[str],
    kinds: list[str],
    today: dt.date,
    *,
    q: str | None = None,
    include_hidden: bool = False,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[ArtistReleases], int]:
    """Released release groups of the user's artists that the library doesn't have, by
    artist (most played first). Returns a page of artists and how many have any."""
    rows = (
        await db.execute(
            _MISSING,
            {
                "user_id": user_id,
                "server_id": server_id,
                "sections": sections,
                "kinds": kinds,
                "today": today,
                "q": _like(q),
                "include_hidden": include_hidden,
                "offset": offset,
                "limit": limit,
            },
        )
    ).all()
    artists: dict[int, ArtistReleases] = {}
    for r in rows:
        artist = artists.setdefault(
            r.artist_rating_key, ArtistReleases(r.artist_rating_key, r.artist, r.artist_thumb)
        )
        artist.releases.append(_release(r))
    return list(artists.values()), rows[0].total if rows else 0


async def coverage(db: AsyncSession, user_id: int, server_id: int, sections: list[str]) -> Coverage:
    r = (
        await db.execute(
            _COVERAGE, {"user_id": user_id, "server_id": server_id, "sections": sections}
        )
    ).one()
    return Coverage(artists=r.artists, with_mbid=r.with_mbid, checked=r.checked)


_FOLLOWING = text(
    f"""
    WITH {FOLLOWED}
    SELECT count(*) FROM followed f
    JOIN artists ar ON ar.server_id = f.server_id AND ar.rating_key = f.rating_key
    WHERE f.user_id = :user_id AND f.server_id = :server_id AND ar.section_key = ANY(:sections)
    """  # noqa: S608 - constant SQL fragment
)


async def following(db: AsyncSession, user_id: int, server_id: int, sections: list[str]) -> int:
    """How many of the user's artists on this server count as followed."""
    return int(
        await db.scalar(
            _FOLLOWING,
            {
                "user_id": user_id,
                "server_id": server_id,
                "sections": sections,
                "follow_rating": FOLLOW_RATING,
                "follow_top": FOLLOW_TOP,
            },
        )
        or 0
    )
