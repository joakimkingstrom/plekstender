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
    assert quiet["quiet_intro"] is True and quiet["start_ms"] == 0

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
