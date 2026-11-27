"""Song previews (clips cut with ffmpeg, intro silence measured) and Guess the intro."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI

from plekstender.api.routes.game import title_ok
from plekstender.config import get_settings
from plekstender.previews import ffmpeg_exe, leading_silence_ms
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs


def audio(tmp: Path, name: str, silence: float, tone: float) -> bytes:
    """A FLAC file: `silence` seconds of silence, then a tone."""
    out = tmp / f"{name}.flac"
    subprocess.run(  # noqa: S603 - test fixture, fixed arguments
        [
            ffmpeg_exe(get_settings()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=stereo:d={silence}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={tone}",
            "-filter_complex",
            "[1]aformat=channel_layouts=stereo[t];[0][t]concat=n=2:v=0:a=1",
            "-c:a",
            "flac",
            "-y",
            str(out),
        ],
        check=True,
    )
    return out.read_bytes()


@pytest.fixture(scope="module")
def songs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    tmp = tmp_path_factory.mktemp("audio")
    return {
        "starts_soon": audio(tmp, "a", 2.0, 12.0),
        "silent_intro": audio(tmp, "b", 11.0, 4.0),
    }


def test_title_and_silence_rules() -> None:
    assert not title_ok("Up")
    assert not title_ok("4 2")
    assert title_ok("Lucky")
    log = (
        "[silencedetect] silence_start: 0\n[silencedetect] silence_end: 2.5 | silence_duration: 2.5"
    )
    assert leading_silence_ms(log) == 2500
    assert leading_silence_ms("silence_start: 3.1\nsilence_end: 4.0") == 0
    assert leading_silence_ms("silence_start: 0\n") == 40_000  # silent to the end
    assert leading_silence_ms("") == 0


@pytest.mark.db
async def test_previews(app: FastAPI, plex: FakePlex, songs: dict[str, bytes]) -> None:
    lib = plex.library
    lib.part_files = {300: songs["starts_soon"], 301: songs["silent_intro"]}
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/tracks"

    resp = await owner.get(f"{base}/300/preview")
    assert resp.status_code == 200, resp.text
    info = resp.json()
    assert info["url"] == f"{base}/300/preview.m4a"
    assert info["quiet_intro"] is False
    assert 1500 <= info["start_ms"] <= 2000  # just before the music starts at 2 s

    clip = await owner.get(info["url"])
    assert clip.status_code == 200
    assert clip.headers["content-type"] == "audio/mp4"
    assert clip.content[4:8] == b"ftyp"  # an MP4 file
    # Byte ranges (iOS Safari plays media only from servers that support them).
    part = await owner.get(info["url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    requests = lib.part_requests
    await owner.get(f"{base}/300/preview")
    assert lib.part_requests == requests  # cached: Plex isn't asked again

    quiet = (await owner.get(f"{base}/301/preview")).json()
    # A long silent intro is skipped (the music starts at 11 s).
    assert quiet["quiet_intro"] is True
    assert 10_000 <= quiet["start_ms"] <= 11_000

    missing = await owner.get(f"{base}/303/preview")
    assert missing.status_code == 502  # Plex has no file for it
    assert (await owner.get(f"{base}/999/preview")).status_code == 404

    # A shared user can't preview what their access doesn't cover.
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = await connect(friend)
    await run_queued_syncs()
    hidden = await friend.get(f"/api/servers/{friend_sid}/tracks/305/preview")
    assert hidden.status_code == 404


@pytest.mark.db
async def test_guess_the_intro(app: FastAPI, plex: FakePlex, songs: dict[str, bytes]) -> None:
    lib = plex.library
    lib.part_files = {300: songs["starts_soon"], 301: songs["silent_intro"]}
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/game"

    async def round_(mode: str, key: int) -> dict:
        resp = await owner.get(f"{base}/round", params={"mode": mode, "rating_key": key})
        assert resp.status_code == 200, resp.text
        body: dict = resp.json()
        return body

    song = await round_("track", 300)
    assert song["playable"] is True and song["preview"]["rating_key"] == 300
    assert await round_("track", 301) == {
        "playable": False,
        "reason": "quiet-intro",
        "preview": None,
        "song_title": None,
    }
    # OK Computer: Karma Police is its most popular song but has a silent intro, so Paranoid
    # Android plays.
    album = await round_("album", 200)
    assert album["playable"] is True and album["preview"]["rating_key"] == 300
    artist = await round_("artist", 100)
    assert artist["preview"]["rating_key"] == 300
    assert (await round_("artist", 101))["playable"] is False  # no files

    async def search(**params: object) -> list[tuple[int, str]]:
        resp = await owner.get(f"{base}/search", params=params)
        assert resp.status_code == 200, resp.text
        return [(i["rating_key"], i["title"]) for i in resp.json()]

    assert await search(kind="artist", q="head") == [(100, "Radiohead"), (101, "Portishead")]
    # One or two letters ("U2"): names that start with them, not every name containing them.
    assert await search(kind="artist", q="Po") == [(101, "Portishead")]
    assert await search(kind="artist", q="he") == []
    assert [t for _, t in await search(kind="album", q="ok")] == [
        "OK Computer",
        "OK Computer OKNOTOK",
    ]
    assert [t for _, t in await search(kind="album", q="ok comp")] == [
        "OK Computer",
        "OK Computer OKNOTOK",
    ]
    assert await search(kind="track", q="karma") == [(301, "Karma Police")]  # once
    assert await search(kind="track", q="") == []
    by_artist = await search(kind="track", artist=100)
    assert [k for k, _ in by_artist] == [301, 300, 303]  # most popular first
    assert await search(kind="track", q="luck", artist=101) == []

    async def check(mode: str, target: int, guess: int) -> bool:
        resp = await owner.post(
            f"{base}/check", json={"mode": mode, "target": target, "guess": guess}, headers=CSRF
        )
        assert resp.status_code == 200, resp.text
        correct: bool = resp.json()["correct"]
        return correct

    assert await check("track", 301, 301)
    assert await check("track", 301, 302)  # the remaster is the same song
    assert not await check("track", 301, 303)
    assert await check("album", 200, 200)
    assert not await check("album", 200, 202)
    assert await check("artist", 100, 100)
    assert not await check("artist", 100, 101)

    for correct, points, streak in [(True, 21, 1), (True, 9, 2), (False, 0, 0)]:
        resp = await owner.post(
            "/api/game/results",
            json={"mode": "track", "correct": correct, "points": points, "streak": streak},
            headers=CSRF,
        )
        assert resp.status_code == 200, resp.text
    stats = (await owner.get("/api/game/stats")).json()
    assert stats == [{"mode": "track", "rounds": 3, "correct": 2, "points": 30, "best_streak": 2}]


@pytest.mark.db
async def test_previews_survive_plex_errors(app: FastAPI, plex: FakePlex) -> None:
    plex.library.part_files = {300: b"this is not audio"}
    owner, sid = await owner_with_library(app, plex)
    resp = await owner.get(f"/api/servers/{sid}/tracks/300/preview")
    assert resp.status_code == 502
    assert "couldn't be read" in resp.json()["detail"]


@pytest.mark.db
async def test_songs_to_rate_by(app: FastAPI, plex: FakePlex, songs: dict[str, bytes]) -> None:
    """Rating cards can play their song too: the song itself from where the music starts,
    or an album's or artist's popular song without a silent intro."""
    owner, sid = await owner_with_library(app, plex)

    async def sample(mode: str, key: int) -> dict:
        resp = await owner.get(
            f"/api/servers/{sid}/rate/sample", params={"mode": mode, "rating_key": key}
        )
        assert resp.status_code == 200, resp.text
        body: dict = resp.json()
        return body

    # OK Computer with only Karma Police's file (a silent intro): played anyway, after the
    # silence, rather than nothing; the game would skip it.
    plex.library.part_files = {301: songs["silent_intro"]}
    only_quiet = await sample("album", 200)
    assert only_quiet["playable"] is True and only_quiet["preview"]["rating_key"] == 301
    assert only_quiet["preview"]["quiet_intro"] is True

    plex.library.part_files = {300: songs["starts_soon"], 301: songs["silent_intro"]}
    quiet = await sample("track", 301)  # the game skips it; rating plays it after the silence
    assert quiet["playable"] is True and quiet["preview"]["start_ms"] >= 10_000
    assert (await sample("album", 200))["preview"]["rating_key"] == 300
    assert (await sample("artist", 100))["preview"]["rating_key"] == 300
    assert (await sample("artist", 101))["reason"] == "no-file"


@pytest.mark.db
async def test_game_extras(app: FastAPI, plex: FakePlex, songs: dict[str, bytes]) -> None:
    """Right-artist feedback, the song title as a hint, settings, and the game playlist."""
    lib = plex.library
    lib.part_files = {300: songs["starts_soon"], 303: songs["starts_soon"]}
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/game"

    album = (await owner.get(f"{base}/round", params={"mode": "album", "rating_key": 200})).json()
    assert album["song_title"] == "Paranoid Android"

    async def check(mode: str, target: int, guess: int, kind: str | None = None) -> dict:
        body = {"mode": mode, "target": target, "guess": guess, "guess_kind": kind}
        resp = await owner.post(f"{base}/check", json=body, headers=CSRF)
        assert resp.status_code == 200, resp.text
        result: dict = resp.json()
        return result

    # Song mode: Lucky is by the right artist (Radiohead) but the wrong song.
    assert await check("track", 301, 303) == {"correct": False, "artist_correct": True}
    assert await check("track", 301, 304) == {"correct": False, "artist_correct": False}
    # Picking the artist on the way to the song.
    assert await check("track", 301, 100, "artist") == {"correct": False, "artist_correct": True}
    assert await check("track", 301, 101, "artist") == {"correct": False, "artist_correct": False}
    assert await check("album", 200, 201) == {"correct": False, "artist_correct": True}
    assert await check("artist", 100, 100) == {"correct": True, "artist_correct": None}

    # Game settings live in the account's preferences.
    settings = {
        "round_seconds": 20,
        "max_guesses": 0,
        "artist_feedback": False,
        "playlist": "Intro finds",
        "popularity": "deep",
    }
    resp = await owner.put("/api/auth/me/preferences", json={"game": settings}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    assert resp.json()["preferences"]["game"] == settings
    bad = await owner.put(
        "/api/auth/me/preferences", json={"game": {**settings, "round_seconds": 7}}, headers=CSRF
    )
    assert bad.status_code == 422
    # Deep cuts: Radiohead's least popular songs (Lucky, of Paranoid Android and Lucky).
    deep = (await owner.get(f"{base}/round", params={"mode": "artist", "rating_key": 100})).json()
    assert deep["song_title"] == "Lucky"

    # The playlist: made when missing, then only new songs added.
    resp = await owner.post(f"{base}/playlist", json={"mode": "track", "target": 301}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["playlist"] == "Intro finds" and first["already"] == 0
    assert first["added"] == ["Karma Police", "Paranoid Android", "Lucky"]
    [made] = lib.playlists.values()
    assert made["title"] == "Intro finds" and made["items"] == [301, 300, 303]
    again = (
        await owner.post(f"{base}/playlist", json={"mode": "album", "target": 200}, headers=CSRF)
    ).json()
    assert (again["added"], again["already"]) == ([], 3)
    assert (await owner.get(f"{base}/playlists")).json() == ["Intro finds"]

    # Points count for lost rounds too (the right-artist bonus).
    resp = await owner.post(
        "/api/game/results",
        json={"mode": "album", "correct": False, "points": 3, "streak": 0},
        headers=CSRF,
    )
    assert resp.json()["points"] == 3


@pytest.mark.db
async def test_leaderboard(app: FastAPI, plex: FakePlex) -> None:
    from sqlalchemy import text

    from plekstender.db import get_sessionmaker

    owner, _ = await owner_with_library(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")

    async def play(client, mode: str, results: list[tuple[bool, int, int]]) -> None:
        for correct, points, streak in results:
            resp = await client.post(
                "/api/game/results",
                json={"mode": mode, "correct": correct, "points": points, "streak": streak},
                headers=CSRF,
            )
            assert resp.status_code == 200, resp.text

    await play(owner, "track", [(True, 20, 1), (True, 25, 2), (True, 15, 3), (False, 3, 0)])
    await play(friend, "album", [(True, 28, 1), (True, 26, 2)])
    await play(friend, "track", [(False, 0, 0)])
    # The owner's first two rounds were yesterday: an earlier game.
    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "UPDATE game_rounds SET created_at = created_at - interval '1 day' "
                "WHERE id IN (SELECT id FROM game_rounds ORDER BY id LIMIT 2)"
            )
        )
        await db.commit()

    board = (await owner.get("/api/game/leaderboard")).json()
    assert [(r["username"], r["you"], r["points"], r["best_streak"]) for r in board] == [
        ("owner", True, 63, 3),
        ("friend", False, 54, 2),
    ]
    mine = board[0]
    assert (mine["rounds"], mine["correct"]) == (4, 3)
    assert (mine["last_score"], mine["last_rounds"]) == (18, 2)  # today's game only
    assert board[1]["last_score"] == 54  # one game, both modes
    assert board[1]["last_played_at"] is not None

    songs = (await friend.get("/api/game/leaderboard", params={"mode": "track"})).json()
    assert [(r["username"], r["points"], r["you"]) for r in songs] == [
        ("owner", 63, False),
        ("friend", 0, True),
    ]
    albums = (await owner.get("/api/game/leaderboard", params={"mode": "album"})).json()
    assert [r["username"] for r in albums] == ["friend"]


def test_versions_count_as_the_same_answer() -> None:
    from plekstender.api.routes.game import core_artist, core_title

    same = [
        "Don't Speak",
        "Don’t Speak (Remastered)",  # noqa: RUF001 - a curly apostrophe, as in tags
        "Don't Speak - Single Version",
        "Don't Speak [Live]",
    ]
    assert len({core_title(t) for t in same}) == 1
    assert core_title("Tragic Kingdom (Deluxe Edition)") == core_title("Tragic Kingdom")
    assert core_title("(What's the Story) Morning Glory?") != core_title("Morning Glory?")
    assert core_title("Spiderwebs") != core_title("Sunday Morning")
    assert core_artist("No Doubt feat. Bounty Killer") == core_artist("No Doubt")
    assert core_artist("The Beatles") == core_artist("Beatles")


@pytest.mark.db
async def test_a_compilation_version_is_right(app: FastAPI, plex: FakePlex) -> None:
    from sqlalchemy import text

    from plekstender.db import get_sessionmaker

    owner, sid = await owner_with_library(app, plex)
    # Karma Police again, on a "Various Artists" compilation, credited to the band.
    async with get_sessionmaker()() as db:
        await db.execute(text("CREATE TEMP TABLE t AS SELECT * FROM tracks WHERE rating_key = 301"))
        await db.execute(
            text(
                "UPDATE t SET rating_key = 990, artist_rating_key = 999, album_rating_key = 998, "
                "title = 'Karma Police - 2008 Remaster', original_title = 'Radiohead'"
            )
        )
        await db.execute(text("INSERT INTO tracks SELECT * FROM t"))
        await db.commit()

    async def check(target: int, guess: int) -> dict:
        resp = await owner.post(
            f"/api/servers/{sid}/game/check",
            json={"mode": "track", "target": target, "guess": guess},
            headers=CSRF,
        )
        assert resp.status_code == 200, resp.text
        result: dict = resp.json()
        return result

    assert await check(301, 990) == {"correct": True, "artist_correct": True}
    assert await check(990, 301) == {"correct": True, "artist_correct": True}
    assert await check(990, 303) == {"correct": False, "artist_correct": True}  # Lucky
