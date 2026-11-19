"""Guess the intro: a game in the Rate cards. A song plays with the card hidden; the player
guesses the song, album or artist from the library."""

from __future__ import annotations

import random
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from plekstender.api.deps import CipherDep, DbDep, HttpDep, SettingsDep, UserDep
from plekstender.api.ratelimit import rate_limit
from plekstender.api.routes.library import _like, visible
from plekstender.api.routes.previews import preview_out, visible_track
from plekstender.api.routes.servers import get_user_server, server_client
from plekstender.api.schemas import (
    GameCheckIn,
    GameCheckOut,
    GameMode,
    GameResultIn,
    GameRoundOut,
    GameSearchItem,
    GameStatsOut,
)
from plekstender.db.models import Album, Artist, GameStats, Track, UserServer
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
    """Most popular songs of an album or artist (one release per song)."""
    state, cond, where = visible(Track, user_id, us)
    rows = list(
        await db.scalars(
            select(Track)
            .join(state, cond)
            .where(*where, column == key, Track.part_key.is_not(None))
            .order_by(Track.popularity.desc().nulls_last(), Track.rating_key)
            .limit(limit * 4)
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
        candidates = await _top_tracks(
            db, user.id, us, column, rating_key, TOP_SONGS, short_titles=not for_game
        )
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
    return await pick_song(db, user, us, settings, http, cipher, mode, rating_key, for_game=True)


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


@router.post("/check", response_model=GameCheckOut)
async def game_check(server_id: int, body: GameCheckIn, db: DbDep, user: UserDep) -> GameCheckOut:
    """Whether a guess is right: the same song on any release, the same album (or a release
    of it with the same title), or the same artist."""
    us = await get_user_server(db, user, server_id)
    if body.target == body.guess:
        return GameCheckOut(correct=True)
    model = {"track": Track, "album": Album, "artist": Artist}[body.mode]
    target = await db.get(model, (us.server_id, body.target))
    guess = await db.get(model, (us.server_id, body.guess))
    if target is None or guess is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if isinstance(target, Artist) and isinstance(guess, Artist):
        correct = bool(target.name_key) and target.name_key == guess.name_key
    elif isinstance(target, Track) and isinstance(guess, Track):
        correct = (
            target.artist_rating_key == guess.artist_rating_key
            and target.title_norm == guess.title_norm
        )
    elif isinstance(target, Album) and isinstance(guess, Album):
        correct = (
            target.artist_rating_key == guess.artist_rating_key
            and bool(target.title_norm)
            and target.title_norm == guess.title_norm
        )
    else:
        correct = False
    return GameCheckOut(correct=correct)


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
        points=body.points if body.correct else 0,
        best_streak=body.streak,
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[GameStats.user_id, GameStats.mode],
            set_={
                "rounds": GameStats.rounds + 1,
                "correct": GameStats.correct + int(body.correct),
                "points": GameStats.points + (body.points if body.correct else 0),
                "best_streak": func.greatest(GameStats.best_streak, body.streak),
                "updated_at": func.now(),
            },
        )
    )
    await db.commit()
    row = await db.get(GameStats, (user.id, body.mode), populate_existing=True)
    assert row is not None
    return _stats_out(row)
