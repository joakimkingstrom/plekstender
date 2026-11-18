"""Exports of the mirrored library with the user's own ratings and plays.

- tracks.csv: one row per track, readable by spreadsheets and most importers.
- library.json: artists, albums and tracks with tags, similar artists and personal
  state; lossless (ratings keep Plex's 0-10 scale next to 0-5 stars).

Both stream from the database. They use their own database session because the
request's session is closed once the streaming response starts.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, and_, func, literal, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import aliased

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.routes.library import visible
from plekstender.api.routes.servers import get_user_server
from plekstender.db import get_sessionmaker
from plekstender.db.models import (
    Album,
    Artist,
    ItemTag,
    PlexServer,
    SimilarArtist,
    Tag,
    Track,
    User,
    UserServer,
)

router = APIRouter(prefix="/api/servers/{server_id}/export", tags=["export"])

CHUNK_ROWS = 500

CSV_COLUMNS = [
    "artist",
    "album_artist",
    "album",
    "year",
    "disc",
    "track",
    "title",
    "duration_seconds",
    "stars",
    "plays",
    "skips",
    "last_played",
    "last_rated",
    "popularity",
    "genres",
    "moods",
    "styles",
    "artist_mbid",
    "album_mbid",
    "track_mbid",
    "file",
]


@dataclass(frozen=True)
class Scope:
    """What an export covers, captured before the request's session closes."""

    user_id: int
    server_id: int
    server_name: str
    sections: tuple[str, ...]

    def as_user_server(self) -> UserServer:
        return UserServer(server_id=self.server_id, music_section_keys=list(self.sections))


async def _scope(db: DbDep, user: User, server_id: int) -> Scope:
    us = await get_user_server(db, user, server_id)
    server = await db.get(PlexServer, us.server_id)
    return Scope(
        user_id=user.id,
        server_id=us.server_id,
        server_name=server.name if server else "plex",
        sections=tuple(us.music_section_keys),
    )


def _filename(scope: Scope, name: str, ext: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", scope.server_name.lower()).strip("-") or "plex"
    return f"plekstender-{slug}-{name}-{dt.date.today().isoformat()}.{ext}"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _stars(rating: float | None) -> float | None:
    return round(rating / 2, 1) if rating is not None else None


def _album_tags(kind: str) -> Any:
    return (
        select(func.string_agg(Tag.name, aggregate_order_by(literal("; "), Tag.name)))
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(
            ItemTag.server_id == Track.server_id,
            ItemTag.rating_key == Track.album_rating_key,
            Tag.kind == kind,
        )
        .scalar_subquery()
    )


def _tracks_query(scope: Scope) -> Select[Any]:
    us = scope.as_user_server()
    state, cond, where = visible(Track, scope.user_id, us)
    album = aliased(Album)
    artist = aliased(Artist)
    return (
        select(
            Track,
            album,
            artist,
            state,
            _album_tags("genre"),
            _album_tags("mood"),
            _album_tags("style"),
        )
        .join(state, cond)
        .outerjoin(
            album,
            and_(album.server_id == Track.server_id, album.rating_key == Track.album_rating_key),
        )
        .outerjoin(
            artist,
            and_(
                artist.server_id == Track.server_id,
                artist.rating_key == Track.artist_rating_key,
            ),
        )
        .where(*where)
        .order_by(
            func.lower(func.coalesce(artist.title_sort, artist.title)),
            album.year.nulls_last(),
            album.title,
            Track.disc.nulls_last(),
            Track.number.nulls_last(),
        )
        .execution_options(yield_per=CHUNK_ROWS)
    )


async def _csv_rows(scope: Scope) -> AsyncIterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    async with get_sessionmaker()() as db:
        result = await db.stream(_tracks_query(scope))
        count = 0
        async for track, album, artist, state, genres, moods, styles in result:
            album_artist = artist.title if artist else ""
            writer.writerow(
                [
                    track.original_title or album_artist,
                    album_artist,
                    album.title if album else "",
                    album.year if album and album.year else "",
                    track.disc or "",
                    track.number or "",
                    track.title,
                    round(track.duration_ms / 1000) if track.duration_ms else "",
                    _stars(state.user_rating) if state.user_rating is not None else "",
                    state.view_count,
                    state.skip_count,
                    _iso(state.last_viewed_at) or "",
                    _iso(state.last_rated_at) or "",
                    track.popularity if track.popularity is not None else "",
                    genres or "",
                    moods or "",
                    styles or "",
                    artist.mbid if artist and artist.mbid else "",
                    album.mbid if album and album.mbid else "",
                    track.mbid or "",
                    track.file_path or "",
                ]
            )
            count += 1
            if count % CHUNK_ROWS == 0:
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate()
    yield buffer.getvalue()


@router.get("/tracks.csv", response_class=StreamingResponse)
async def export_tracks_csv(server_id: int, db: DbDep, user: UserDep) -> StreamingResponse:
    scope = await _scope(db, user, server_id)
    return StreamingResponse(
        _csv_rows(scope),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(scope, "tracks", "csv")}"'
        },
    )


def _personal(state: Any) -> dict[str, Any]:
    return {
        "user_rating": state.user_rating,
        "stars": _stars(state.user_rating),
        "plays": state.view_count,
        "skips": state.skip_count,
        "last_played": _iso(state.last_viewed_at),
        "last_rated": _iso(state.last_rated_at),
    }


def _base(item: Artist | Album | Track) -> dict[str, Any]:
    return {
        "plex_rating_key": item.rating_key,
        "plex_guid": item.guid,
        "mbid": item.mbid,
        "title": item.title,
        "title_sort": item.title_sort,
        "summary": item.summary,
        "added_at": _iso(item.added_at),
        "popularity": item.popularity,
    }


async def _json_chunks(scope: Scope) -> AsyncIterator[str]:
    us = scope.as_user_server()
    header = {
        "format": "plekstender-library",
        "version": 1,
        "exported_at": dt.datetime.now(dt.UTC).isoformat(),
        "server": scope.server_name,
        "notes": "user_rating uses Plex's 0-10 scale; stars = user_rating / 2.",
    }
    yield json.dumps(header)[:-1]  # keep the object open

    async with get_sessionmaker()() as db:
        tags: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for key, kind, name in (
            await db.execute(
                select(ItemTag.rating_key, Tag.kind, Tag.name)
                .join(Tag, Tag.id == ItemTag.tag_id)
                .where(ItemTag.server_id == scope.server_id)
                .order_by(Tag.name)
            )
        ).all():
            tags[key][kind].append(name)
        similar: dict[int, list[str]] = defaultdict(list)
        for key, name in (
            await db.execute(
                select(SimilarArtist.artist_rating_key, SimilarArtist.name)
                .where(SimilarArtist.server_id == scope.server_id)
                .order_by(SimilarArtist.artist_rating_key, SimilarArtist.position)
            )
        ).all():
            similar[key].append(name)

        def section(name: str) -> str:
            return f', "{name}": ['

        # Artists
        yield section("artists")
        state, cond, where = visible(Artist, scope.user_id, us)
        first = True
        async for artist, st in await db.stream(
            select(Artist, state).join(state, cond).where(*where).order_by(Artist.title)
        ):
            item = _base(artist) | {
                "tags": tags.get(artist.rating_key, {}),
                "similar": similar.get(artist.rating_key, []),
                "user": _personal(st),
            }
            yield ("" if first else ",") + json.dumps(item)
            first = False
        yield "]"

        # Albums
        yield section("albums")
        state, cond, where = visible(Album, scope.user_id, us)
        first = True
        async for album, st in await db.stream(
            select(Album, state).join(state, cond).where(*where).order_by(Album.title)
        ):
            item = _base(album) | {
                "artist_rating_key": album.artist_rating_key,
                "year": album.year,
                "released": album.released.isoformat() if album.released else None,
                "label": album.studio,
                "critic_rating": album.critic_rating,
                "tags": tags.get(album.rating_key, {}),
                "user": _personal(st),
            }
            yield ("" if first else ",") + json.dumps(item)
            first = False
        yield "]"

        # Tracks
        yield section("tracks")
        state, cond, where = visible(Track, scope.user_id, us)
        first = True
        async for track, st in await db.stream(
            select(Track, state)
            .join(state, cond)
            .where(*where)
            .order_by(Track.album_rating_key, Track.disc, Track.number)
            .execution_options(yield_per=CHUNK_ROWS)
        ):
            item = _base(track) | {
                "artist_rating_key": track.artist_rating_key,
                "album_rating_key": track.album_rating_key,
                "track_artist": track.original_title,
                "disc": track.disc,
                "track": track.number,
                "duration_ms": track.duration_ms,
                "file": track.file_path,
                "user": _personal(st),
            }
            yield ("" if first else ",") + json.dumps(item)
            first = False
        yield "]}"


@router.get("/library.json", response_class=StreamingResponse)
async def export_library_json(server_id: int, db: DbDep, user: UserDep) -> StreamingResponse:
    scope = await _scope(db, user, server_id)
    return StreamingResponse(
        _json_chunks(scope),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(scope, "library", "json")}"'
        },
    )
