"""Where artists are from: Plex's country tag, else MusicBrainz (looked up by the worker and
kept in `artist_areas`). The worker goes at half MusicBrainz's pace, one artist every two
seconds, leaving room for the web app's own MusicBrainz requests (the tag editor's)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Iterable
from dataclasses import dataclass

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import musicbrainz
from plekstender.countries import country_code
from plekstender.db.models import Artist, ArtistArea, ItemTag, Tag

log = logging.getLogger(__name__)

# How long "not found" is believed before looking again.
RETRY_AFTER = dt.timedelta(days=90)
# Artists looked up per run (about ten minutes at one every two seconds).
BATCH = 300
PAUSE = 1.0  # seconds, on top of MusicBrainz's own one-a-second spacing
VARIOUS = {"various artists", "various", "va"}


@dataclass(frozen=True)
class ArtistKey:
    rating_key: int
    title: str
    mbid: str | None
    name_key: str | None

    @property
    def area_key(self) -> str | None:
        if self.mbid and musicbrainz.MBID.fullmatch(self.mbid):
            return f"mbid:{self.mbid}"
        if self.name_key:
            return f"name:{self.name_key}"
        return None


async def plex_countries(db: AsyncSession, server_id: int) -> dict[int, str]:
    """Artists' countries from Plex's country tags (the first one Plekstender knows)."""
    rows = await db.execute(
        select(ItemTag.rating_key, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.server_id == server_id, Tag.kind == "country")
        .order_by(ItemTag.rating_key, Tag.name)
    )
    out: dict[int, str] = {}
    for key, name in rows.all():
        if key not in out and (code := country_code(name)):
            out[key] = code
    return out


async def countries_for(
    db: AsyncSession, server_id: int, artists: Iterable[ArtistKey]
) -> tuple[dict[int, str], int]:
    """Each artist's country (Plex's, else MusicBrainz's), and how many are still waiting
    for a MusicBrainz lookup."""
    artists = list(artists)
    plex = await plex_countries(db, server_id)
    keys = {a.area_key for a in artists if a.rating_key not in plex and a.area_key}
    areas: dict[str, str | None] = {}
    key_list = list(keys)
    for start in range(0, len(key_list), 5000):
        chunk = key_list[start : start + 5000]
        rows = await db.execute(
            select(ArtistArea.key, ArtistArea.country_code).where(ArtistArea.key.in_(chunk))
        )
        areas.update(dict(rows.all()))
    out: dict[int, str] = {}
    pending = 0
    for a in artists:
        if a.rating_key in plex:
            out[a.rating_key] = plex[a.rating_key]
        elif a.area_key in areas:
            if (code := areas[a.area_key]) is not None:
                out[a.rating_key] = code
        elif a.area_key and a.title.strip().casefold() not in VARIOUS:
            pending += 1
    return out, pending


async def to_look_up(db: AsyncSession, now: dt.datetime, limit: int = BATCH) -> list[ArtistKey]:
    """Artists (on any server) with no country from Plex, not looked up yet (or not found
    a while ago); those with a MusicBrainz ID first."""
    has_country = (
        select(ItemTag.rating_key)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(
            ItemTag.server_id == Artist.server_id,
            ItemTag.rating_key == Artist.rating_key,
            Tag.kind == "country",
        )
        .exists()
    )
    rows = await db.execute(
        select(Artist.rating_key, Artist.title, Artist.mbid, Artist.name_key)
        .where(~has_country)
        .order_by(Artist.mbid.is_(None), Artist.server_id, Artist.rating_key)
    )
    candidates: dict[str, ArtistKey] = {}
    for key, title, mbid, name_key in rows.all():
        artist = ArtistKey(key, title, mbid, name_key)
        if artist.area_key and title.strip().casefold() not in VARIOUS:
            candidates.setdefault(artist.area_key, artist)
    if not candidates:
        return []
    known: set[str] = set()
    key_list = list(candidates)
    for start in range(0, len(key_list), 5000):
        chunk = key_list[start : start + 5000]
        known.update(
            await db.scalars(
                select(ArtistArea.key).where(
                    ArtistArea.key.in_(chunk),
                    or_(
                        ArtistArea.country_code.is_not(None),
                        and_(
                            ArtistArea.country_code.is_(None),
                            ArtistArea.checked_at > now - RETRY_AFTER,
                        ),
                    ),
                )
            )
        )
    return [a for k, a in candidates.items() if k not in known][:limit]


async def look_up(db: AsyncSession, http: httpx.AsyncClient, limit: int = BATCH) -> int:
    """Look up artists' countries on MusicBrainz; returns how many were
    looked up. Stops early when MusicBrainz is unreachable or busy (tried next run)."""
    now = dt.datetime.now(dt.UTC)
    done = 0
    for artist in await to_look_up(db, now, limit):
        if done:
            await asyncio.sleep(PAUSE)
        try:
            if artist.mbid and artist.area_key and artist.area_key.startswith("mbid:"):
                code = await musicbrainz.artist_country(http, artist.mbid)
            else:
                code = await musicbrainz.find_artist_country(http, artist.title)
        except musicbrainz.NotFound:
            code = None
        except musicbrainz.MusicBrainzError as exc:
            log.info("MusicBrainz artist lookups paused: %s", exc)
            break
        stmt = insert(ArtistArea).values(
            key=artist.area_key, country_code=code, checked_at=dt.datetime.now(dt.UTC)
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "country_code": stmt.excluded.country_code,
                    "checked_at": stmt.excluded.checked_at,
                },
            )
        )
        await db.commit()
        done += 1
    return done
