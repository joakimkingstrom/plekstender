"""Guess the intro: a game in the Rate cards. A song plays with the card hidden; the player
guesses the song, album or artist from the library."""

from __future__ import annotations

import random
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.auth import load_preferences
from plekstender.api.routes.library import _like, visible
from plekstender.api.routes.previews import preview_out, visible_track
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import (
    GameCheckIn,
    GameCheckOut,
    GameMode,
    GamePlaylistIn,
    GamePlaylistOut,
    GameResultIn,
    GameRoundOut,
    GameSearchItem,
    GameStatsOut,
)
from plekstender.db.models import Album, Artist, GameStats, PlexServer, Track, UserServer
from plekstender.plex.tv import PlexError
from plekstender.previews import PreviewError, get_clip
from plekstender.sync.parse import fold

router = APIRouter(prefix="/api/servers/{server_id}/game", tags=["game"])
stats_router = APIRouter(prefix="/api/game", tags=["game"])
sample_router = APIRouter(prefix="/api/servers/{server_id}/rate", tags=["ratings"])

# Titles shorter than this (letters and digits) are too easy to stumble on: not played.
MIN_TITLE = 4
# Album / artist rounds: one of their most popular songs.
TOP_SONGS = 3
# Songs tried for a round before giving up (each may need a clip made).
MAX_TRIES = 3
VARIOUS = ("various artists", "various", "va")


# Album / artist rounds by game setting: how deep into their songs to go.
POPULARITY_POOLS = {"popular": (0, TOP_SONGS), "mixed": (0, 10), "deep": (10, 60)}


def by_popularity(songs: list[Track], popularity: str) -> list[Track]:
    """The songs to choose from: the top 3, the top 10, or the rest (deep cuts; the
    least popular half when there are 10 or fewer)."""
    start, end = POPULARITY_POOLS.get(popularity, POPULARITY_POOLS["popular"])
    if popularity == "deep" and len(songs) <= start:
        return songs[len(songs) // 2 :] or songs
    return songs[start:end] or songs[:TOP_SONGS]


def title_ok(title: str) -> bool:
    return sum(c.isalnum() for c in fold(title)) >= MIN_TITLE


async def _top_tracks(
    db: DbDep,
    user_id: int,
    us: UserServer,
    column: Any,
    key: int,
    limit: int,
    *,
    short_titles: bool = False,
) -> list[Track]:
    """An album's or artist's songs, most popular first (one release per song)."""
    state, cond, where = visible(Track, user_id, us)
    rows = list(
        await db.scalars(
            select(Track)
            .join(state, cond)
            .where(*where, column == key, Track.part_key.is_not(None))
            .order_by(Track.popularity.desc().nulls_last(), Track.rating_key)
            .limit(limit * 3)
        )
    )
    seen: set[str] = set()
    out = []
    for track in rows:
        if track.title_norm in seen or not (short_titles or title_ok(track.title)):
            continue
        seen.add(track.title_norm)
        out.append(track)
    return out[:limit]


async def pick_song(
    db: DbDep,
    user: Any,
    us: UserServer,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
    mode: GameMode,
    rating_key: int,
    *,
    for_game: bool,
    popularity: str = "popular",
) -> GameRoundOut:
    """The song to play for a card: the song itself, or one of an album's or artist's most
    popular songs, avoiding silent intros. For the game, songs with very short titles or a
    silent intro aren't played at all; for rating, the silence is skipped instead."""
    if mode == "track":
        track = await visible_track(db, user, us, rating_key)
        if for_game and not title_ok(track.title):
            return GameRoundOut(playable=False, reason="short-title", preview=None)
        candidates = [track]
    else:
        column = Track.album_rating_key if mode == "album" else Track.artist_rating_key
        songs = await _top_tracks(
            db, user.id, us, column, rating_key, 60, short_titles=not for_game
        )
        candidates = by_popularity(songs, popularity) if for_game else songs[:TOP_SONGS]
        random.shuffle(candidates)
    if not candidates:
        return GameRoundOut(playable=False, reason="no-file", preview=None)
    client = server_client(us, cipher, settings, http)
    reason = "no-file"
    fallback: GameRoundOut | None = None
    for track in candidates[:MAX_TRIES]:
        try:
            clip = await get_clip(db, settings, client, track)
        except PreviewError:
            continue
        found = GameRoundOut(
            playable=True,
            reason=None,
            preview=preview_out(us.id, track.rating_key, clip),
            song_title=track.title,
        )
        if not clip.quiet_intro:
            return found
        reason = "quiet-intro"
        fallback = fallback or found
    if fallback is not None and not for_game:
        return fallback  # every candidate starts quietly: play one, from where it starts
    return GameRoundOut(playable=False, reason=reason, preview=None)


@router.get(
    "/round",
    response_model=GameRoundOut,
    dependencies=[rate_limit("game-round", limit=60, window=60)],
)
async def game_round(
    server_id: int,
    mode: GameMode,
    rating_key: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> GameRoundOut:
    """The song to play for a game round (not played: very short titles, silent intros)."""
    us = await get_user_server(db, user, server_id)
    popularity = load_preferences(user).game.popularity
    return await pick_song(
        db, user, us, settings, http, cipher, mode, rating_key, for_game=True, popularity=popularity
    )


@sample_router.get(
    "/sample",
    response_model=GameRoundOut,
    dependencies=[rate_limit("rate-sample", limit=90, window=60)],
)
async def rate_sample(
    server_id: int,
    mode: GameMode,
    rating_key: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> GameRoundOut:
    """A song to listen to while rating a card: the song itself (from where the music
    starts), or one of an album's or artist's most popular songs without a silent intro."""
    us = await get_user_server(db, user, server_id)
    return await pick_song(db, user, us, settings, http, cipher, mode, rating_key, for_game=False)


@router.get("/search", response_model=list[GameSearchItem])
async def game_search(
    server_id: int,
    db: DbDep,
    user: UserDep,
    kind: GameMode,
    q: Annotated[str, Query(max_length=100)] = "",
    artist: Annotated[int | None, Query(description="Songs by this artist only.")] = None,
) -> list[GameSearchItem]:
    """Guess suggestions from the library: artists, albums or songs whose name contains
    `q`. With `artist`, that artist's songs (their most popular ones when `q` is empty)."""
    us = await get_user_server(db, user, server_id)
    q = q.strip()
    if kind != "track" and len(q) < 1:
        return []
    if kind == "artist":
        state, cond, where = visible(Artist, user.id, us)
        rows = await db.scalars(
            select(Artist)
            .join(state, cond)
            .where(
                *where,
                Artist.title.ilike(_like(q), escape="\\"),
                func.lower(func.btrim(Artist.title)).not_in(VARIOUS),
            )
            .order_by(
                (func.lower(Artist.title) == q.lower()).desc(),
                Artist.popularity.desc().nulls_last(),
                Artist.title,
            )
            .limit(8)
        )
        return [
            GameSearchItem(
                rating_key=a.rating_key, kind="artist", title=a.title, subtitle=None, thumb=a.thumb
            )
            for a in rows
        ]
    if kind == "album":
        state, cond, where = visible(Album, user.id, us)
        owner = aliased(Artist)
        album_rows = await db.execute(
            select(Album, owner.title)
            .join(state, cond)
            .outerjoin(
                owner,
                (owner.server_id == Album.server_id)
                & (owner.rating_key == Album.artist_rating_key),
            )
            .where(*where, Album.title.ilike(_like(q), escape="\\"))
            .order_by(Album.popularity.desc().nulls_last(), Album.title)
            .limit(8)
        )
        return [
            GameSearchItem(
                rating_key=a.rating_key, kind="album", title=a.title, subtitle=name, thumb=a.thumb
            )
            for a, name in album_rows.all()
        ]
    if not q and artist is None:
        return []
    state, cond, where = visible(Track, user.id, us)
    owner = aliased(Artist)
    filters = [*where]
    if q:
        filters.append(Track.title.ilike(_like(q), escape="\\"))
    if artist is not None:
        filters.append(Track.artist_rating_key == artist)
    track_rows = await db.execute(
        select(Track, func.coalesce(Track.original_title, owner.title), Album.thumb)
        .join(state, cond)
        .outerjoin(
            owner,
            (owner.server_id == Track.server_id) & (owner.rating_key == Track.artist_rating_key),
        )
        .outerjoin(
            Album,
            (Album.server_id == Track.server_id) & (Album.rating_key == Track.album_rating_key),
        )
        .where(*filters)
        .order_by(Track.popularity.desc().nulls_last(), Track.rating_key)
        .limit(60)
    )
    seen: set[tuple[int | None, str]] = set()
    out = []
    for track, artist_name, thumb in track_rows.all():
        key = (track.artist_rating_key, track.title_norm)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            GameSearchItem(
                rating_key=track.rating_key,
                kind="track",
                title=track.title,
                subtitle=artist_name,
                thumb=thumb,
            )
        )
        if len(out) == (15 if artist is not None else 10):
            break
    return out


async def _artist_of(db: DbDep, server_id: int, item: Track | Album | Artist) -> Artist | None:
    if isinstance(item, Artist):
        return item
    if item.artist_rating_key is None:
        return None
    return await db.get(Artist, (server_id, item.artist_rating_key))


def _same_artist(a: Artist | None, b: Artist | None) -> bool:
    if a is None or b is None:
        return False
    return a.rating_key == b.rating_key or (bool(a.name_key) and a.name_key == b.name_key)


MODELS: dict[str, Any] = {  # song, album or artist model by game mode
    "track": Track,
    "album": Album,
    "artist": Artist,
}


@router.post("/check", response_model=GameCheckOut)
async def game_check(server_id: int, body: GameCheckIn, db: DbDep, user: UserDep) -> GameCheckOut:
    """Whether a guess is right: the same song on any release, the same album (or a release
    of it with the same title), or the same artist. In song and album modes, also whether
    the guess (or the artist picked) is by the right artist."""
    us = await get_user_server(db, user, server_id)
    guess_kind = body.guess_kind or body.mode
    target = await db.get(MODELS[body.mode], (us.server_id, body.target))
    guess = await db.get(MODELS[guess_kind], (us.server_id, body.guess))
    if target is None or guess is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if isinstance(target, Artist) and isinstance(guess, Artist):
        return GameCheckOut(correct=_same_artist(target, guess), artist_correct=None)
    artist_correct = _same_artist(
        await _artist_of(db, us.server_id, target), await _artist_of(db, us.server_id, guess)
    )
    if isinstance(target, Track) and isinstance(guess, Track):
        correct = target.rating_key == guess.rating_key or (
            target.artist_rating_key == guess.artist_rating_key
            and target.title_norm == guess.title_norm
        )
    elif isinstance(target, Album) and isinstance(guess, Album):
        correct = target.rating_key == guess.rating_key or (
            target.artist_rating_key == guess.artist_rating_key
            and bool(target.title_norm)
            and target.title_norm == guess.title_norm
        )
    else:
        correct = False  # an artist picked while guessing a song or album
    return GameCheckOut(correct=correct, artist_correct=artist_correct or correct)


PLAYLIST_SONGS = 5


@router.get("/playlists", response_model=list[str])
async def game_playlists(
    server_id: int,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> list[str]:
    """Names of your regular audio playlists in Plex (for the game's settings)."""
    us = await get_user_server(db, user, server_id)
    try:
        found = await server_client(us, cipher, settings, http).audio_playlists()
    except (PlexError, httpx.HTTPError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Plex couldn't be reached.") from exc
    return sorted({str(p.get("title")) for p in found if p.get("title") and not p.get("smart")})


@router.post("/playlist", response_model=GamePlaylistOut)
async def add_to_game_playlist(
    server_id: int,
    body: GamePlaylistIn,
    db: DbDep,
    user: UserDep,
    settings: SettingsDep,
    http: HttpDep,
    cipher: CipherDep,
) -> GamePlaylistOut:
    """Add the card's artist's five most popular songs to your game playlist in Plex (made
    when it doesn't exist yet). Songs already in it are left out."""
    us = await get_user_server(db, user, server_id)
    target = await db.get(MODELS[body.mode], (us.server_id, body.target))
    artist = await _artist_of(db, us.server_id, target) if target is not None else None
    if artist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artist not found")
    songs = await _top_tracks(
        db,
        user.id,
        us,
        Track.artist_rating_key,
        artist.rating_key,
        PLAYLIST_SONGS,
        short_titles=True,
    )
    songs = songs[:PLAYLIST_SONGS]
    if not songs:
        raise HTTPException(status.HTTP_409_CONFLICT, f"No songs by {artist.title} to add.")
    name = load_preferences(user).game.playlist.strip()
    server = await db.get(PlexServer, us.server_id)
    assert server is not None
    client = server_client(us, cipher, settings, http)
    try:
        existing = next(
            (
                p
                for p in await client.audio_playlists()
                if not p.get("smart") and str(p.get("title", "")).casefold() == name.casefold()
            ),
            None,
        )
        if existing is None:
            await client.create_playlist(
                name, server.machine_identifier, [s.rating_key for s in songs]
            )
            return GamePlaylistOut(playlist=name, added=[s.title for s in songs], already=0)
        key = int(existing["ratingKey"])
        have = set(await client.playlist_rating_keys(key))
        new = [s for s in songs if s.rating_key not in have]
        if new:
            await client.add_to_playlist(
                key, server.machine_identifier, [s.rating_key for s in new]
            )
        return GamePlaylistOut(
            playlist=str(existing.get("title") or name),
            added=[s.title for s in new],
            already=len(songs) - len(new),
        )
    except (PlexError, httpx.HTTPError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Plex couldn't update the playlist."
        ) from exc


def _stats_out(row: GameStats) -> GameStatsOut:
    return GameStatsOut(
        mode=row.mode,
        rounds=row.rounds,
        correct=row.correct,
        points=row.points,
        best_streak=row.best_streak,
    )


@stats_router.get("/stats", response_model=list[GameStatsOut])
async def game_stats(db: DbDep, user: UserDep) -> list[GameStatsOut]:
    rows = await db.scalars(select(GameStats).where(GameStats.user_id == user.id))
    return [_stats_out(r) for r in rows]


@stats_router.post("/results", response_model=GameStatsOut)
async def game_result(body: GameResultIn, db: DbDep, user: UserDep) -> GameStatsOut:
    """Record a finished round: totals and the best streak per mode."""
    stmt = insert(GameStats).values(
        user_id=user.id,
        mode=body.mode,
        rounds=1,
        correct=int(body.correct),
        points=body.points,
        best_streak=body.streak,
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[GameStats.user_id, GameStats.mode],
            set_={
                "rounds": GameStats.rounds + 1,
                "correct": GameStats.correct + int(body.correct),
                "points": GameStats.points + body.points,
                "best_streak": func.greatest(GameStats.best_streak, body.streak),
                "updated_at": func.now(),
            },
        )
    )
    await db.commit()
    row = await db.get(GameStats, (user.id, body.mode), populate_existing=True)
    assert row is not None
    return _stats_out(row)
