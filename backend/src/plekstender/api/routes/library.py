"""Browse the mirrored library, and an image proxy for Plex artwork.

Every query joins the current user's `user_item_state` rows, so people only see items
their own Plex account can access, in the music libraries they selected.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import ColumnElement, and_, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import aliased

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import (
    AlbumDetail,
    AlbumLyricsOut,
    AlbumSummary,
    ArtistDetail,
    ArtistNeighbourOut,
    ArtistNeighboursOut,
    ArtistPage,
    ArtistSummary,
    CoverOf,
    ItemListensOut,
    LibrarySearchOut,
    RecentItem,
    SearchHit,
    SimilarArtistOut,
    SongListens,
    SongVersion,
    SongVersionsOut,
    TagAlbum,
    TagDetail,
    TagOut,
    TrackOut,
    YearListens,
)
from plekstender.artist_countries import ArtistKey, artist_country
from plekstender.db.models import (
    Album,
    Artist,
    ItemTag,
    MediaFile,
    SimilarArtist,
    SongCover,
    Tag,
    Track,
    User,
    UserItemState,
    UserServer,
)
from plekstender.gigs.seen_live import seen_live, times_seen
from plekstender.history import stats
from plekstender.plex.tv import PlexError
from plekstender.sync.parse import core_title, normalise_artist, normalise_title
from plekstender.tags.lyrics_kinds import Kind, best
from plekstender.tags.read import LYRICS_READER

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
        filters.append(artist_has_tag(us.server_id, tag))

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


def artist_has_tag(server_id: int, tag_id: int) -> ColumnElement[bool]:
    """The artist has the tag, or one of their albums has it. Plex puts most styles and
    moods on albums, so an artist's own tags alone would miss them."""
    tagged = select(ItemTag.rating_key).where(
        ItemTag.server_id == server_id, ItemTag.tag_id == tag_id
    )
    return Artist.rating_key.in_(tagged) | Artist.rating_key.in_(
        select(Album.artist_rating_key).where(
            Album.server_id == server_id, Album.rating_key.in_(tagged)
        )
    )


TagKind = Literal["genre", "mood", "style", "country"]
TAG_KINDS: tuple[str, ...] = ("genre", "style", "mood", "country")


@router.get("/library/tags", response_model=list[TagOut])
async def list_tags(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: TagKind = "genre",
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TagOut]:
    """Tags of the artists in your libraries (on the artist or one of their albums),
    counted by artist, most common first."""
    us = await get_user_server(db, user, server_id)
    state, cond, where = visible(Artist, user.id, us)
    # Each artist's own tags, plus their albums' tags, as (artist, tag) pairs.
    own = select(ItemTag.rating_key.label("artist_key"), ItemTag.tag_id).where(
        ItemTag.server_id == us.server_id
    )
    via_albums = (
        select(Album.artist_rating_key.label("artist_key"), ItemTag.tag_id)
        .join(
            ItemTag,
            and_(ItemTag.server_id == Album.server_id, ItemTag.rating_key == Album.rating_key),
        )
        .where(Album.server_id == us.server_id, Album.artist_rating_key.is_not(None))
    )
    pairs = own.union(via_albums).subquery()
    count = func.count(Artist.rating_key)
    rows = await db.execute(
        select(Tag.id, Tag.kind, Tag.name, count)
        .join(pairs, pairs.c.tag_id == Tag.id)
        .join(
            Artist, and_(Artist.server_id == Tag.server_id, Artist.rating_key == pairs.c.artist_key)
        )
        .join(state, cond)
        .where(*where, Tag.server_id == us.server_id, Tag.kind == kind)
        .group_by(Tag.id)
        .order_by(count.desc(), Tag.name)
        .limit(limit)
    )
    return [TagOut(id=i, kind=k, name=n, count=c) for i, k, n, c in rows.all()]


@router.get("/library/recent", response_model=list[RecentItem])
async def recently_added(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: Literal["album", "artist"] = "album",
    limit: int = Query(default=12, ge=1, le=60),
) -> list[RecentItem]:
    """Albums or artists most recently added to Plex (that you can see)."""
    us = await get_user_server(db, user, server_id)
    if kind == "artist":
        state, cond, where = visible(Artist, user.id, us)
        artists = await db.scalars(
            select(Artist)
            .join(state, cond)
            .where(*where, Artist.added_at.is_not(None))
            .order_by(Artist.added_at.desc(), Artist.rating_key.desc())
            .limit(limit)
        )
        return [
            RecentItem(
                rating_key=a.rating_key,
                title=a.title,
                subtitle=None,
                thumb=a.thumb,
                added_at=a.added_at,
            )
            for a in artists
        ]
    state, cond, where = visible(Album, user.id, us)
    owner = aliased(Artist)
    rows = await db.execute(
        select(Album, owner.title)
        .join(state, cond)
        .outerjoin(
            owner,
            and_(owner.server_id == Album.server_id, owner.rating_key == Album.artist_rating_key),
        )
        .where(*where, Album.added_at.is_not(None))
        .order_by(Album.added_at.desc(), Album.rating_key.desc())
        .limit(limit)
    )
    return [
        RecentItem(
            rating_key=a.rating_key,
            title=a.title,
            subtitle=artist,
            thumb=a.thumb,
            added_at=a.added_at,
        )
        for a, artist in rows.all()
    ]


TagSort = Literal["plays", "rating", "year", "name"]
TAG_PAGE_LIMIT = 240


@router.get("/library/tags/{tag_id}", response_model=TagDetail)
async def get_tag(
    server_id: int, tag_id: int, db: DbDep, user: UserDep, sort: TagSort = "plays"
) -> TagDetail:
    """A genre's, style's or mood's page: the artists and albums with it, and the tags
    most often found with it."""
    us = await get_user_server(db, user, server_id)
    tag = await db.get(Tag, tag_id)
    if tag is None or tag.server_id != us.server_id or tag.kind not in TAG_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")

    # Artists: tagged themselves or on an album.
    state, cond, where = visible(Artist, user.id, us)
    artist_filters = (*where, artist_has_tag(us.server_id, tag_id))
    name = func.lower(func.coalesce(Artist.title_sort, Artist.title))
    artist_orders: dict[str, list[Any]] = {
        "plays": [plays_of(state).desc(), Artist.popularity.desc().nulls_last()],
        "rating": [state.user_rating.desc().nulls_last(), plays_of(state).desc()],
        "year": [name],
        "name": [name],
    }
    artist_total = await db.scalar(
        select(func.count()).select_from(Artist).join(state, cond).where(*artist_filters)
    )
    artist_rows = await db.execute(
        select(Artist, state.user_rating, plays_of(state), _album_count(user.id, us))
        .join(state, cond)
        .where(*artist_filters)
        .order_by(*artist_orders[sort], Artist.rating_key)
        .limit(TAG_PAGE_LIMIT)
    )

    # Albums: tagged themselves.
    a_state, a_cond, a_where = visible(Album, user.id, us)
    album_filters = (
        *a_where,
        Album.rating_key.in_(
            select(ItemTag.rating_key).where(
                ItemTag.server_id == us.server_id, ItemTag.tag_id == tag_id
            )
        ),
    )
    album_orders: dict[str, list[Any]] = {
        "plays": [plays_of(a_state).desc(), Album.popularity.desc().nulls_last()],
        "rating": [a_state.user_rating.desc().nulls_last(), plays_of(a_state).desc()],
        "year": [Album.year.desc().nulls_last(), Album.title],
        "name": [func.lower(func.coalesce(Album.title_sort, Album.title))],
    }
    album_total = await db.scalar(
        select(func.count()).select_from(Album).join(a_state, a_cond).where(*album_filters)
    )
    owner = aliased(Artist)
    album_rows = (
        await db.execute(
            select(Album, a_state.user_rating, plays_of(a_state), owner.title)
            .join(a_state, a_cond)
            .outerjoin(
                owner,
                and_(
                    owner.server_id == Album.server_id,
                    owner.rating_key == Album.artist_rating_key,
                ),
            )
            .where(*album_filters)
            .order_by(*album_orders[sort], Album.rating_key)
            .limit(TAG_PAGE_LIMIT)
        )
    ).all()
    types = await _release_types(db, us.server_id, [a.rating_key for a, *_ in album_rows])

    return TagDetail(
        id=tag.id,
        kind=tag.kind,
        name=tag.name,
        artist_count=artist_total or 0,
        album_count=album_total or 0,
        artists=[_artist_summary(r) for r in artist_rows.all()],
        albums=[
            TagAlbum(
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
                artist_rating_key=a.artist_rating_key,
                artist_title=artist_title,
            )
            for a, rating, plays, artist_title in album_rows
        ],
        related=await _related_tags(db, user.id, us, tag_id),
    )


async def _related_tags(
    db: DbDep, user_id: int, us: UserServer, tag_id: int, limit: int = 16
) -> list[TagOut]:
    """Genres, styles and moods most often on the same artists and albums as this tag
    (the ones you can see), with how many items share them."""
    other = aliased(ItemTag)
    mine = aliased(ItemTag)
    shared = func.count(other.rating_key)
    rows = await db.execute(
        select(Tag.id, Tag.kind, Tag.name, shared)
        .select_from(mine)
        .join(
            other,
            and_(other.server_id == mine.server_id, other.rating_key == mine.rating_key),
        )
        .join(Tag, Tag.id == other.tag_id)
        .join(
            UserItemState,
            and_(
                UserItemState.user_id == user_id,
                UserItemState.server_id == mine.server_id,
                UserItemState.rating_key == mine.rating_key,
                UserItemState.section_key.in_(us.music_section_keys),
            ),
        )
        .where(
            mine.server_id == us.server_id,
            mine.tag_id == tag_id,
            other.tag_id != tag_id,
            Tag.kind.in_(("genre", "style", "mood")),
        )
        .group_by(Tag.id)
        .order_by(shared.desc(), Tag.name)
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


# --- which songs have lyrics (a preference: small icons on rows) ---------------------------

# The music files that have been read for lyrics (the same as the Lyrics page counts).
_READ_FOR_LYRICS = (MediaFile.reader >= LYRICS_READER, MediaFile.error.is_(None))


def wants_lyrics_icons(user: User) -> bool:
    return (user.preferences or {}).get("lyrics_icons") is True


async def _with_lyrics(
    db: DbDep, user: User, server_id: int, tracks: list[TrackOut]
) -> list[TrackOut]:
    """Marks each song with the best lyrics its files have (when the user wants the icons).
    Songs without a file read for lyrics are left unmarked."""
    if not tracks or not wants_lyrics_icons(user):
        return tracks
    rows = await db.execute(
        select(MediaFile.rating_key, MediaFile.lyrics).where(
            MediaFile.server_id == server_id,
            MediaFile.rating_key.in_([t.rating_key for t in tracks]),
            *_READ_FOR_LYRICS,
        )
    )
    found: dict[int, Kind | None] = {}
    for key, kind in rows.all():
        if key is not None:
            found[key] = best(found.get(key), cast("Kind | None", kind))
    for track in tracks:
        if track.rating_key in found:
            track.lyrics = found[track.rating_key] or "none"
    return tracks


async def _albums_with_lyrics(
    db: DbDep, user: User, server_id: int, albums: list[AlbumSummary]
) -> list[AlbumSummary]:
    """How many of each album's songs have lyrics in their files (when the user wants the
    icons). Albums none of whose files have been read are left unmarked."""
    if not albums or not wants_lyrics_icons(user):
        return albums
    read = (
        select(MediaFile.rating_key, func.bool_or(MediaFile.lyrics.is_not(None)).label("has"))
        .where(MediaFile.server_id == server_id, *_READ_FOR_LYRICS)
        .group_by(MediaFile.rating_key)
        .subquery()
    )
    rows = await db.execute(
        select(
            Track.album_rating_key,
            func.count(),
            func.count(read.c.rating_key),
            func.count().filter(read.c.has),
        )
        .outerjoin(read, read.c.rating_key == Track.rating_key)
        .where(
            Track.server_id == server_id,
            Track.album_rating_key.in_([a.rating_key for a in albums]),
        )
        .group_by(Track.album_rating_key)
    )
    counts = {key: (songs, known, has) for key, songs, known, has in rows.all()}
    for album in albums:
        songs, known, has = counts.get(album.rating_key, (0, 0, 0))
        if known:
            album.lyrics = AlbumLyricsOut(songs=songs, with_lyrics=has)
    return albums


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
        artist_keys=found.artist_keys,
        album_key=found.album_key,
    )


def _with_listens(
    tracks: list[TrackOut], titles: dict[int, str], found: stats.ItemListens
) -> list[TrackOut]:
    for track in tracks:
        track.listens = found.by_track_key.get(titles.get(track.rating_key, ""), 0)
    return tracks


async def _with_covers(db: DbDep, server_id: int, tracks: list[TrackOut]) -> None:
    """Marks the songs that are covers (SecondHandSongs or MusicBrainz: looked up by the
    worker when an admin turned it on), with whose song they are."""
    if not tracks:
        return
    rows = await db.execute(
        select(Track.rating_key, SongCover)
        .join(
            SongCover,
            and_(
                SongCover.artist_key == Track.artist_key, SongCover.title_norm == Track.title_norm
            ),
        )
        .where(
            Track.server_id == server_id,
            Track.rating_key.in_([t.rating_key for t in tracks]),
            SongCover.status == "cover",
        )
    )
    covers = {key: cover for key, cover in rows.all()}
    for track in tracks:
        if cover := covers.get(track.rating_key):
            track.cover_of = CoverOf(
                artist=cover.original_artist,
                title=cover.original_title,
                url=cover.original_url,
                cover_url=cover.performance_url,
                source=cover.source,
            )


async def _with_seen_live(
    db: DbDep, user_id: int, tracks: list[TrackOut], artist: str | None
) -> list[TrackOut]:
    """Marks the songs played at gigs the user went to (by the album's or the song's artist)."""
    seen = await seen_live(db, user_id)
    if seen:
        for track in tracks:
            track.seen_live = times_seen(seen, [artist, track.original_title], track.title)
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


SEARCH_EACH = 6  # results of each kind


@router.get("/library/search", response_model=LibrarySearchOut)
async def search_library(
    server_id: int,
    db: DbDep,
    user: UserDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
) -> LibrarySearchOut:
    """Artists, albums and songs whose names contain the text (names starting with it first,
    then the most popular), for the header's search."""
    us = await get_user_server(db, user, server_id)
    like = _like(q.strip())
    starts = f"{like[1:]}"  # the text, then anything

    def ranked(column: Any, popularity: Any) -> tuple[Any, ...]:
        return (
            (func.lower(column) == q.strip().lower()).desc(),
            column.ilike(starts, escape="\\").desc(),
            popularity.desc().nulls_last(),
            column,
        )

    a_state, a_cond, a_where = visible(Artist, user.id, us)
    artists = (
        await db.scalars(
            select(Artist)
            .join(a_state, a_cond)
            .where(*a_where, Artist.title.ilike(like, escape="\\"))
            .order_by(*ranked(Artist.title, Artist.popularity))
            .limit(SEARCH_EACH)
        )
    ).all()
    owner = aliased(Artist)
    al_state, al_cond, al_where = visible(Album, user.id, us)
    albums = (
        await db.execute(
            select(Album, owner.title)
            .join(al_state, al_cond)
            .outerjoin(
                owner,
                and_(
                    owner.server_id == Album.server_id, owner.rating_key == Album.artist_rating_key
                ),
            )
            .where(*al_where, Album.title.ilike(like, escape="\\"))
            .order_by(*ranked(Album.title, Album.popularity))
            .limit(SEARCH_EACH)
        )
    ).all()
    t_state, t_cond, t_where = visible(Track, user.id, us)
    song_artist = aliased(Artist)
    song_album = aliased(Album)
    # One release a song (the most popular).
    songs = (
        await db.execute(
            select(Track, song_artist.title, song_album)
            .ext(distinct_on(Track.artist_key, Track.title_norm))
            .join(t_state, t_cond)
            .outerjoin(
                song_artist,
                and_(
                    song_artist.server_id == Track.server_id,
                    song_artist.rating_key == Track.artist_rating_key,
                ),
            )
            .outerjoin(
                song_album,
                and_(
                    song_album.server_id == Track.server_id,
                    song_album.rating_key == Track.album_rating_key,
                ),
            )
            .where(*t_where, Track.title.ilike(like, escape="\\"))
            .order_by(Track.artist_key, Track.title_norm, Track.popularity.desc().nulls_last())
            .limit(200)
        )
    ).all()
    songs = sorted(
        songs,
        key=lambda r: (
            r[0].title.lower() != q.strip().lower(),
            not r[0].title.lower().startswith(q.strip().lower()),
            -(r[0].popularity or 0),
            r[0].title,
        ),
    )[:SEARCH_EACH]
    return LibrarySearchOut(
        artists=[
            SearchHit(rating_key=a.rating_key, title=a.title, thumb=a.thumb, subtitle=None)
            for a in artists
        ],
        albums=[
            SearchHit(rating_key=a.rating_key, title=a.title, thumb=a.thumb, subtitle=artist)
            for a, artist in albums
        ],
        songs=[
            SearchHit(
                rating_key=t.rating_key,
                title=t.title,
                thumb=album.thumb if album else None,
                subtitle=" · ".join(
                    x for x in (t.original_title or artist, album.title if album else None) if x
                )
                or None,
                album_rating_key=t.album_rating_key,
            )
            for t, artist, album in songs
        ],
    )


@router.get("/library/artists/{rating_key}/neighbours", response_model=ArtistNeighboursOut)
async def artist_neighbours(
    server_id: int, rating_key: int, db: DbDep, user: UserDep
) -> ArtistNeighboursOut:
    """The artists just before and after this one, by name as the library lists them (to
    step through artist pages)."""
    us = await get_user_server(db, user, server_id)
    state, cond, where = visible(Artist, user.id, us)
    name = func.lower(func.coalesce(Artist.title_sort, Artist.title))
    here = (
        await db.execute(
            select(name, Artist.rating_key)
            .join(state, cond)
            .where(*where, Artist.rating_key == rating_key)
        )
    ).first()
    if here is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artist not found")
    key = tuple_(name, Artist.rating_key)
    at = tuple_(literal(here[0]), literal(here[1]))

    async def one(before: bool) -> ArtistNeighbourOut | None:
        order = (name.desc(), Artist.rating_key.desc()) if before else (name, Artist.rating_key)
        row = (
            await db.execute(
                select(Artist.rating_key, Artist.title)
                .join(state, cond)
                .where(*where, key < at if before else key > at)
                .order_by(*order)
                .limit(1)
            )
        ).first()
        return ArtistNeighbourOut(rating_key=row[0], title=row[1]) if row else None

    return ArtistNeighboursOut(previous=await one(True), next=await one(False))


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
    await _with_covers(db, us.server_id, top_tracks)
    summary = _artist_summary(row)
    summary.plays = max(summary.plays, listens.listens)
    return ArtistDetail(
        **summary.model_dump(),
        summary=artist.summary,
        art=artist.art,
        country=await artist_country(
            db,
            us.server_id,
            ArtistKey(artist.rating_key, artist.title, artist.mbid, artist.name_key),
        ),
        tags=await _tags_for(db, us.server_id, rating_key),
        similar=[SimilarArtistOut(name=n, rating_key=in_library.get(n.lower())) for n in names],
        albums=await _albums_with_lyrics(
            db,
            user,
            us.server_id,
            await _albums(db, user.id, us, Album.artist_rating_key == rating_key),
        ),
        top_tracks=await _with_lyrics(
            db,
            user,
            us.server_id,
            await _with_seen_live(
                db, user.id, _with_listens(top_tracks, titles, listens), artist.title
            ),
        ),
        your_listens=_listens_out(listens),
    )


@router.get("/library/albums/{rating_key}", response_model=AlbumDetail)
async def get_album(
    server_id: int, rating_key: int, db: DbDep, user: UserDep, tz: TzQuery = "UTC"
) -> AlbumDetail:
    us = await get_user_server(db, user, server_id)
    found = await _albums_with_lyrics(
        db, user, us.server_id, await _albums(db, user.id, us, Album.rating_key == rating_key)
    )
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
    await _with_covers(db, us.server_id, tracks)
    found[0].plays = max(found[0].plays, listens.listens)
    return AlbumDetail(
        **found[0].model_dump(),
        summary=album.summary,
        released=album.released,
        studio=album.studio,
        artist_rating_key=album.artist_rating_key,
        artist_title=artist.title if artist else None,
        tags=await _tags_for(db, us.server_id, rating_key),
        tracks=await _with_lyrics(
            db,
            user,
            us.server_id,
            await _with_seen_live(
                db,
                user.id,
                _with_listens(
                    tracks, {t.rating_key: t.title_norm for t, _, _ in track_rows}, listens
                ),
                artist.title if artist else None,
            ),
        ),
        your_listens=_listens_out(listens),
    )


@router.get("/library/tracks/{rating_key}/versions", response_model=SongVersionsOut)
async def song_versions(
    server_id: int, rating_key: int, db: DbDep, user: UserDep
) -> SongVersionsOut:
    """Every release of the song by the same artist: remasters, live, demo and other
    versions (the same title without its bracketed or dashed suffix), oldest first."""
    us = await get_user_server(db, user, server_id)
    state, cond, where = visible(Track, user.id, us)
    track = await db.scalar(
        select(Track).join(state, cond).where(*where, Track.rating_key == rating_key)
    )
    if track is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Song not found")
    core = core_title(track.title)
    by_artist = (
        Track.artist_key == track.artist_key
        if track.artist_key
        else Track.artist_rating_key == track.artist_rating_key
    )
    rows = [
        row
        for row in (
            await db.execute(
                select(Track, state.user_rating, plays_of(state), Album)
                .join(state, cond)
                .outerjoin(
                    Album,
                    and_(
                        Album.server_id == Track.server_id,
                        Album.rating_key == Track.album_rating_key,
                    ),
                )
                .where(*where, by_artist)
            )
        ).all()
        if core_title(row[0].title) == core
    ]
    types = await _release_types(
        db, us.server_id, [a.rating_key for _, _, _, a in rows if a is not None]
    )
    rows.sort(
        key=lambda r: (
            r[3].year if r[3] is not None and r[3].year is not None else 9999,
            r[3].title if r[3] is not None else "",
            r[0].disc or 0,
            r[0].number or 0,
        )
    )
    artist = track.original_title
    if artist is None and track.artist_rating_key is not None:
        found = await db.get(Artist, (us.server_id, track.artist_rating_key))
        artist = found.title if found else None
    return SongVersionsOut(
        title=track.title,
        artist=artist,
        versions=[
            SongVersion(
                rating_key=t.rating_key,
                title=t.title,
                album_rating_key=t.album_rating_key,
                album_title=a.title if a else None,
                album_thumb=a.thumb if a else None,
                year=a.year if a else None,
                release_type=types.get(a.rating_key, "Album") if a else None,
                duration_ms=t.duration_ms,
                user_rating=rating,
                plays=plays,
                current=t.rating_key == rating_key,
            )
            for t, rating, plays, a in rows
        ],
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
