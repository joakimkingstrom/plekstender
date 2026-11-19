"""Browse the mirrored library, and an image proxy for Plex artwork.

Every query joins the current user's `user_item_state` rows, so people only see items
their own Plex account can access, in the music libraries they selected.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import aliased

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import (
    AlbumDetail,
    AlbumSummary,
    ArtistDetail,
    ArtistPage,
    ArtistSummary,
    ItemListensOut,
    SimilarArtistOut,
    SongListens,
    TagOut,
    TrackOut,
    YearListens,
)
from plekstender.db.models import (
    Album,
    Artist,
    ItemTag,
    SimilarArtist,
    Tag,
    Track,
    UserItemState,
    UserServer,
)
from plekstender.history import stats
from plekstender.plex.tv import PlexError
from plekstender.sync.parse import normalise_artist, normalise_title

router = APIRouter(prefix="/api/servers/{server_id}", tags=["library"])

ArtistSort = Literal["title", "popularity", "plays", "rating", "added"]
IMAGE_PATH = re.compile(r"^/library/metadata/\d+/(?:thumb|art)(?:/\d+)?$")
IMAGE_SIZES = (64, 128, 256, 400, 600, 1000)


def visible(model: type[Artist] | type[Album] | type[Track], user_id: int, us: UserServer) -> Any:
    """Join condition: the item is in the user's libraries and their token can see it."""
    state = aliased(UserItemState)
    condition = and_(
        state.user_id == user_id,
        state.server_id == model.server_id,
        state.rating_key == model.rating_key,
    )
    where = (model.server_id == us.server_id, model.section_key.in_(us.music_section_keys))
    return state, condition, where


def plays_of(state: Any) -> Any:
    """Plays shown for an item: Plex's own count or the listens in the user's history
    (Plex plays plus imported Last.fm / ListenBrainz scrobbles), whichever is higher.
    Plex only knows plays from when it was in use; the history usually goes further back."""
    return func.greatest(state.view_count, state.listens)


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _album_count(user_id: int, us: UserServer) -> Any:
    state, cond, where = visible(Album, user_id, us)
    return (
        select(func.count())
        .select_from(Album)
        .join(state, cond)
        .where(*where, Album.artist_rating_key == Artist.rating_key)
        .scalar_subquery()
    )


def _artist_summary(row: Any) -> ArtistSummary:
    artist, user_rating, plays, album_count = row
    return ArtistSummary(
        rating_key=artist.rating_key,
        title=artist.title,
        thumb=artist.thumb,
        popularity=artist.popularity,
        user_rating=user_rating,
        plays=plays,
        album_count=album_count,
    )


@router.get("/library/artists", response_model=ArtistPage)
async def list_artists(
    server_id: int,
    db: DbDep,
    user: UserDep,
    q: str | None = Query(default=None, max_length=200),
    tag: int | None = None,
    sort: ArtistSort = "title",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=200),
) -> ArtistPage:
    us = await get_user_server(db, user, server_id)
    state, cond, where = visible(Artist, user.id, us)
    filters: list[ColumnElement[bool]] = list(where)
    if q:
        filters.append(Artist.title.ilike(_like(q), escape="\\"))
    if tag is not None:
        filters.append(
            Artist.rating_key.in_(
                select(ItemTag.rating_key).where(
                    ItemTag.server_id == us.server_id, ItemTag.tag_id == tag
                )
            )
        )

    orders: dict[str, list[Any]] = {
        "title": [func.lower(func.coalesce(Artist.title_sort, Artist.title))],
        "popularity": [Artist.popularity.desc().nulls_last()],
        "plays": [plays_of(state).desc()],
        "rating": [state.user_rating.desc().nulls_last()],
        "added": [Artist.added_at.desc().nulls_last()],
    }
    order = orders[sort]

    total = await db.scalar(
        select(func.count()).select_from(Artist).join(state, cond).where(*filters)
    )
    rows = await db.execute(
        select(Artist, state.user_rating, plays_of(state), _album_count(user.id, us))
        .join(state, cond)
        .where(*filters)
        .order_by(*order, Artist.rating_key)
        .offset(offset)
        .limit(limit)
    )
    return ArtistPage(
        items=[_artist_summary(r) for r in rows.all()],
        total=total or 0,
        offset=offset,
        limit=limit,
    )


@router.get("/library/tags", response_model=list[TagOut])
async def list_tags(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: Literal["genre", "mood", "style", "country"] = "genre",
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TagOut]:
    """Tags used by artists in your libraries, most common first."""
    us = await get_user_server(db, user, server_id)
    state, cond, where = visible(Artist, user.id, us)
    count = func.count(Artist.rating_key)
    rows = await db.execute(
        select(Tag.id, Tag.kind, Tag.name, count)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .join(
            Artist,
            and_(Artist.server_id == ItemTag.server_id, Artist.rating_key == ItemTag.rating_key),
        )
        .join(state, cond)
        .where(*where, Tag.server_id == us.server_id, Tag.kind == kind)
        .group_by(Tag.id)
        .order_by(count.desc(), Tag.name)
        .limit(limit)
    )
    return [TagOut(id=i, kind=k, name=n, count=c) for i, k, n, c in rows.all()]


RELEASE_KINDS = ("format", "subformat")


async def _tags_for(db: DbDep, server_id: int, rating_key: int) -> list[TagOut]:
    """Genres, moods, styles and countries (release formats are shown as a type instead)."""
    rows = await db.execute(
        select(Tag.id, Tag.kind, Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(
            ItemTag.server_id == server_id,
            ItemTag.rating_key == rating_key,
            Tag.kind.not_in(RELEASE_KINDS),
        )
        .order_by(Tag.kind, Tag.name)
    )
    return [TagOut(id=i, kind=k, name=n) for i, k, n in rows.all()]


async def _release_types(db: DbDep, server_id: int, keys: list[int]) -> dict[int, str]:
    """Each album's release type: its Subformat (Compilation, Live…) if Plex set one,
    else its Format (Album, EP, Single)."""
    if not keys:
        return {}
    rows = await db.execute(
        select(ItemTag.rating_key, Tag.kind, Tag.name)
        .join(Tag, ItemTag.tag_id == Tag.id)
        .where(
            ItemTag.server_id == server_id,
            ItemTag.rating_key.in_(keys),
            Tag.kind.in_(RELEASE_KINDS),
        )
        .order_by(Tag.name)
    )
    found: dict[int, dict[str, str]] = {}
    for key, kind, name in rows.all():
        found.setdefault(key, {}).setdefault(kind, name)
    return {
        key: tags.get("subformat") or tags.get("format") or "Album" for key, tags in found.items()
    }


async def _albums(db: DbDep, user_id: int, us: UserServer, *filters: Any) -> list[AlbumSummary]:
    state, cond, where = visible(Album, user_id, us)
    rows = await db.execute(
        select(Album, state.user_rating, plays_of(state))
        .join(state, cond)
        .where(*where, *filters)
        .order_by(Album.year.nulls_last(), Album.title)
    )
    found = rows.all()
    types = await _release_types(db, us.server_id, [a.rating_key for a, _, _ in found])
    return [
        AlbumSummary(
            rating_key=a.rating_key,
            title=a.title,
            year=a.year,
            thumb=a.thumb,
            popularity=a.popularity,
            critic_rating=a.critic_rating,
            track_count=a.track_count,
            user_rating=rating,
            plays=plays,
            release_type=types.get(a.rating_key, "Album"),
        )
        for a, rating, plays in found
    ]


def _track_out(track: Track, rating: float | None, plays: int, album: Album | None) -> TrackOut:
    return TrackOut(
        rating_key=track.rating_key,
        title=track.title,
        original_title=track.original_title,
        disc=track.disc,
        number=track.number,
        duration_ms=track.duration_ms,
        popularity=track.popularity,
        user_rating=rating,
        plays=plays,
        album_rating_key=track.album_rating_key,
        album_title=album.title if album else None,
        album_thumb=album.thumb if album else None,
    )


async def _top_tracks(
    db: DbDep, user_id: int, us: UserServer, artist_key: int, limit: int = 10
) -> list[TrackOut]:
    """Most popular songs, with releases of the same song (remasters...) listed once."""
    state, cond, where = visible(Track, user_id, us)
    ranked = (
        select(
            Track.rating_key.label("rating_key"),
            func.row_number()
            .over(
                partition_by=Track.title_norm,
                order_by=(Track.popularity.desc().nulls_last(), Track.rating_key),
            )
            .label("rn"),
        )
        .join(state, cond)
        .where(*where, Track.artist_rating_key == artist_key)
        .subquery()
    )
    state2, cond2, _ = visible(Track, user_id, us)
    rows = await db.execute(
        select(Track, state2.user_rating, plays_of(state2), Album)
        .join(ranked, and_(ranked.c.rating_key == Track.rating_key, ranked.c.rn == 1))
        .join(state2, cond2)
        .outerjoin(
            Album,
            and_(Album.server_id == Track.server_id, Album.rating_key == Track.album_rating_key),
        )
        .where(Track.server_id == us.server_id)
        .order_by(Track.popularity.desc().nulls_last(), Track.title)
        .limit(limit)
    )
    return [_track_out(t, r, p, a) for t, r, p, a in rows.all()]


TzQuery = Annotated[str, Query(max_length=64, description="IANA time zone for yearly counts")]


def _zone(name: str) -> ZoneInfo:
    try:
        return stats.zone(name)
    except stats.StatsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _listens_out(found: stats.ItemListens) -> ItemListensOut:
    return ItemListensOut(
        listens=found.listens,
        first_at=found.first_at,
        last_at=found.last_at,
        years=[YearListens(year=y, listens=n) for y, n in found.years],
        songs=[SongListens(name=name, listens=n) for name, n in found.songs],
    )


def _with_listens(
    tracks: list[TrackOut], titles: dict[int, str], found: stats.ItemListens
) -> list[TrackOut]:
    for track in tracks:
        track.listens = found.by_track_key.get(titles.get(track.rating_key, ""), 0)
    return tracks


async def _title_norms(db: DbDep, server_id: int, keys: list[int]) -> dict[int, str]:
    if not keys:
        return {}
    rows = await db.execute(
        select(Track.rating_key, Track.title_norm).where(
            Track.server_id == server_id, Track.rating_key.in_(keys)
        )
    )
    return {k: t for k, t in rows.all()}


@router.get("/library/artists/{rating_key}", response_model=ArtistDetail)
async def get_artist(
    server_id: int, rating_key: int, db: DbDep, user: UserDep, tz: TzQuery = "UTC"
) -> ArtistDetail:
    us = await get_user_server(db, user, server_id)
    state, cond, where = visible(Artist, user.id, us)
    row = (
        await db.execute(
            select(Artist, state.user_rating, plays_of(state), _album_count(user.id, us))
            .join(state, cond)
            .where(*where, Artist.rating_key == rating_key)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artist not found")
    artist = row[0]

    names = list(
        await db.scalars(
            select(SimilarArtist.name)
            .where(
                SimilarArtist.server_id == us.server_id,
                SimilarArtist.artist_rating_key == rating_key,
            )
            .order_by(SimilarArtist.position)
        )
    )
    in_library: dict[str, int] = {}
    if names:
        s2, c2, w2 = visible(Artist, user.id, us)
        matches = await db.execute(
            select(func.lower(Artist.title), Artist.rating_key)
            .join(s2, c2)
            .where(*w2, func.lower(Artist.title).in_([n.lower() for n in names]))
        )
        in_library = {name: key for name, key in matches.all()}

    listens = await stats.item_listens(db, user.id, _zone(tz), {normalise_artist(artist.title)})
    top_tracks = await _top_tracks(db, user.id, us, rating_key)
    titles = await _title_norms(db, us.server_id, [t.rating_key for t in top_tracks])
    summary = _artist_summary(row)
    summary.plays = max(summary.plays, listens.listens)
    return ArtistDetail(
        **summary.model_dump(),
        summary=artist.summary,
        art=artist.art,
        tags=await _tags_for(db, us.server_id, rating_key),
        similar=[SimilarArtistOut(name=n, rating_key=in_library.get(n.lower())) for n in names],
        albums=await _albums(db, user.id, us, Album.artist_rating_key == rating_key),
        top_tracks=_with_listens(top_tracks, titles, listens),
        your_listens=_listens_out(listens),
    )


@router.get("/library/albums/{rating_key}", response_model=AlbumDetail)
async def get_album(
    server_id: int, rating_key: int, db: DbDep, user: UserDep, tz: TzQuery = "UTC"
) -> AlbumDetail:
    us = await get_user_server(db, user, server_id)
    found = await _albums(db, user.id, us, Album.rating_key == rating_key)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Album not found")
    album = await db.get(Album, (us.server_id, rating_key))
    assert album is not None
    artist = (
        await db.get(Artist, (us.server_id, album.artist_rating_key))
        if album.artist_rating_key is not None
        else None
    )
    state, cond, where = visible(Track, user.id, us)
    rows = await db.execute(
        select(Track, state.user_rating, plays_of(state))
        .join(state, cond)
        .where(*where, Track.album_rating_key == rating_key)
        .order_by(Track.disc.nulls_last(), Track.number.nulls_last(), Track.title)
    )
    track_rows = rows.all()
    # Compilations: listens are stored under each track's own artist.
    artist_keys = {normalise_artist(artist.title)} if artist else set()
    artist_keys |= {
        normalise_artist(t.original_title) for t, _, _ in track_rows if t.original_title
    }
    listens = await stats.item_listens(
        db, user.id, _zone(tz), artist_keys, normalise_title(album.title)
    )
    tracks = [_track_out(t, r, p, album) for t, r, p in track_rows]
    found[0].plays = max(found[0].plays, listens.listens)
    return AlbumDetail(
        **found[0].model_dump(),
        summary=album.summary,
        released=album.released,
        studio=album.studio,
        artist_rating_key=album.artist_rating_key,
        artist_title=artist.title if artist else None,
        tags=await _tags_for(db, us.server_id, rating_key),
        tracks=_with_listens(
            tracks, {t.rating_key: t.title_norm for t, _, _ in track_rows}, listens
        ),
        your_listens=_listens_out(listens),
    )


@router.get(
    "/image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def image(
    server_id: int,
    db: DbDep,
    user: UserDep,
    cipher: CipherDep,
    settings: SettingsDep,
    http: HttpDep,
    path: str = Query(max_length=200),
    size: int = Query(default=256, ge=16, le=2000),
) -> Response:
    """Artwork from Plex, resized by Plex, fetched with the user's own token.

    Only Plex library artwork paths are accepted; this is not a general-purpose proxy.
    """
    if not IMAGE_PATH.match(path):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a Plex artwork path")
    us = await get_user_server(db, user, server_id)
    snapped = next((s for s in IMAGE_SIZES if s >= size), IMAGE_SIZES[-1])
    client = server_client(us, cipher, settings, http)
    try:
        content, content_type = await client.photo(path, snapped)
    except PlexError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, "Image unavailable") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Image unavailable") from exc
    return Response(
        content,
        media_type=content_type,
        # Plex artwork paths end in a version number, so a cached copy stays correct.
        headers={"Cache-Control": "private, max-age=604800"},
    )
