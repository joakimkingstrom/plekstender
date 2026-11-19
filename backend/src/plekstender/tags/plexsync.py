"""Writing Plex's metadata to music files: working out which tags differ from Plex.

Only files matched to a Plex track (and readable) are considered. A field is only changed
where Plex has a value and the file's differs in a way that matters: "2/12" already is track
2, "1997-05-21" already is 1997, and genres in another order are the same genres.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from plekstender.db.models import Album, Artist, ItemTag, MediaFile, Tag, Track
from plekstender.tags.write import clean

Scope = Literal["library", "artist", "album"]
SYNC_FIELDS = (
    "title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date", "genre",
)  # fmt: skip


@dataclass
class Planned:
    media_file_id: int
    path: str
    rating_key: int
    field: str
    old_value: Any
    new_value: Any


def _number(value: Any) -> tuple[int | None, str | None]:
    """ "2/12" -> (2, "12")."""
    if not value:
        return None, None
    head, _, total = str(value).partition("/")
    try:
        return int(head), (total or None)
    except ValueError:
        return None, None


def plan_file(tags: dict[str, Any], plex: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    """The new values for one file's tags (only the fields that differ)."""
    out: dict[str, Any] = {}
    for field in fields:
        old = clean(field, tags.get(field))
        if field in ("tracknumber", "discnumber"):
            number = plex.get("track" if field == "tracknumber" else "disc")
            if number is None:
                continue
            mine, total = _number(old)
            if mine == number:
                continue
            if field == "discnumber" and old is None and number == 1:
                continue  # a one-disc album needs no disc number
            new: Any = f"{number}/{total}" if total else str(number)
        elif field == "date":
            year = plex.get("year")
            if not year or (old and str(old).startswith(str(year))):
                continue
            new = str(year)
        elif field == "genre":
            genres = plex.get("genre") or []
            if not genres or sorted(old or []) == sorted(genres):
                continue
            new = list(genres)
        else:
            new = clean(field, plex.get(field))
            if new is None or new == old:
                continue
        out[field] = clean(field, new)
    return out


async def plan(
    db: AsyncSession, server_id: int, scope: Scope, key: int | None, fields: set[str]
) -> list[Planned]:
    """Every change that would make the files' tags match Plex."""
    track_artist = aliased(Artist)
    album_artist = aliased(Artist)
    query = (
        select(
            MediaFile.id,
            MediaFile.path,
            MediaFile.tags,
            Track.rating_key,
            Track.title,
            Track.original_title,
            Track.number,
            Track.disc,
            track_artist.title,
            Album.rating_key,
            Album.title,
            Album.year,
            album_artist.title,
        )
        .join(
            Track,
            (Track.server_id == MediaFile.server_id) & (Track.rating_key == MediaFile.rating_key),
        )
        .outerjoin(
            track_artist,
            (track_artist.server_id == Track.server_id)
            & (track_artist.rating_key == Track.artist_rating_key),
        )
        .outerjoin(
            Album,
            (Album.server_id == Track.server_id) & (Album.rating_key == Track.album_rating_key),
        )
        .outerjoin(
            album_artist,
            (album_artist.server_id == Album.server_id)
            & (album_artist.rating_key == Album.artist_rating_key),
        )
        .where(MediaFile.server_id == server_id, MediaFile.error.is_(None))
        .order_by(MediaFile.path)
    )
    if scope == "artist":
        query = query.where((Track.artist_rating_key == key) | (Album.artist_rating_key == key))
    elif scope == "album":
        query = query.where(Track.album_rating_key == key)
    rows = (await db.execute(query)).all()
    genres: dict[int, list[str]] = {}
    if "genre" in fields:
        album_keys = {row[9] for row in rows if row[9] is not None}
        conditions = [ItemTag.server_id == server_id, Tag.kind == "genre"]
        if len(album_keys) < 30_000:  # otherwise all of them, filtered below
            conditions.append(ItemTag.rating_key.in_(album_keys))
        tag_rows = await db.execute(
            select(ItemTag.rating_key, Tag.name)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(*conditions)
            .order_by(Tag.name)
        )
        for album_key, name in tag_rows.all():
            if album_key in album_keys:
                genres.setdefault(album_key, []).append(name)
    out: list[Planned] = []
    for (
        file_id, path, tags, rating_key, title, original_title, number, disc,
        artist_name, album_key, album_title, year, album_artist_name,
    ) in rows:  # fmt: skip
        plex = {
            "title": title,
            "artist": original_title or artist_name,
            "album": album_title,
            "albumartist": album_artist_name,
            "track": number,
            "disc": disc,
            "year": year,
            "genre": genres.get(album_key, []),
        }
        for field, new in plan_file(tags or {}, plex, fields).items():
            out.append(
                Planned(
                    file_id, path, rating_key, field, clean(field, (tags or {}).get(field)), new
                )
            )
    return out
