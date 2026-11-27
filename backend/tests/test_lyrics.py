"""Lyrics for music files: LRCLIB lookups, lyrics in each format's tags, and writing them as
an edit (next to the songs and in their tags) that undo takes out again."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from mutagen.flac import FLAC

from plekstender.tags import embedded_lyrics, lrclib
from plekstender.tags import lyrics as lyrics_mod
from plekstender.tags import lyrics_kinds as kinds
from plekstender.tags.scan import lyrics_beside
from tests.fakes import CSRF, FakePlex
from tests.test_files import make_audio, music  # noqa: F401 - the music folder fixture
from tests.test_sync import owner_with_library

LRCLIB = "https://lrclib.net"
SYNCED = "[ar:Radiohead]\n[00:12.30] Rain down, rain down\n[00:15.80] Come on rain down on me"
WORDS = """version: '1.0'
metadata:
  title: Paranoid Android
  artist: Radiohead
lines:
  - text: Rain down
    start_ms: 12300
    words:
      - text: 'Rain '
        start_ms: 12300
      - text: down
        start_ms: 12900
plain: |
  Rain down
"""


def test_kinds_of_lyrics(tmp_path: Path) -> None:
    assert kinds.of_text(SYNCED) == "synced"
    assert kinds.of_text("[00:12.30] <00:12.30> Rain <00:12.90> down") == "word"
    assert kinds.of_text("Just words\nand more") == "plain"
    assert kinds.of_text("  \n") is None
    assert kinds.of_lyricsfile(WORDS) == "word"
    lines_only = WORDS.replace("    words:\n      - text: 'Rain '\n        start_ms: 12300\n"
                               "      - text: down\n        start_ms: 12900\n", "")  # fmt: skip
    assert kinds.of_lyricsfile(lines_only) == "synced"
    assert (
        kinds.of_lyricsfile("version: '1.0'\nmetadata: {title: a, artist: b}\nplain: hi") == "plain"
    )
    instrumental = "version: '1.0'\nmetadata: {title: a, artist: b, instrumental: true}"
    assert kinds.of_lyricsfile(instrumental) == "instrumental"
    assert kinds.of_lyricsfile(WORDS.replace("'1.0'", "'2.0'")) is None  # unknown version
    assert kinds.of_lyricsfile("version: '1.0'\nmetadata: &m {title: a}\nplain: *m") is None
    assert kinds.of_lyricsfile("not: [valid") is None
    assert kinds.best("plain", None, "word", "synced") == "word"
    assert kinds.best(None, None) is None
    assert kinds.sidecar_path("A/01 Song.flac", ".lyricsfile.yaml") == "A/01 Song.lyricsfile.yaml"
    (tmp_path / "a.lrc").write_text(SYNCED)
    (tmp_path / "a.lyricsfile.yaml").write_text(WORDS)
    (tmp_path / "b.txt").write_text("words")
    found = lyrics_beside(
        tmp_path, ["a.flac", "b.mp3", "c.flac"], {"a.lrc", "a.lyricsfile.yaml", "b.txt"}
    )
    assert found == {"a.flac": "word", "b.mp3": "plain"}


async def test_lrclib_requests_are_spaced(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(lrclib.asyncio, "sleep", sleep)
    monkeypatch.setattr(lrclib, "_last", 0.0)
    with respx.mock() as web:
        web.get(f"{LRCLIB}/api/search").mock(return_value=httpx.Response(200, json=[]))
        async with httpx.AsyncClient() as http:
            for _ in range(2):
                await lrclib.find(http, LRCLIB, artist="A", title="B", album=None, duration_ms=None)
    assert len(waits) == 1 and 0.5 < waits[0] <= lrclib.INTERVAL


def test_lyrics_text() -> None:
    assert lyrics_mod.without_times(SYNCED) == "Rain down, rain down\nCome on rain down on me"
    assert lyrics_mod.normalise("  a\r\nb \n") == "a\nb"
    assert lyrics_mod.normalise("  ") is None
    assert lyrics_mod.sidecar("Radiohead/01 Airbag.flac", ".lrc") == "Radiohead/01 Airbag.lrc"
    assert lyrics_mod.sidecar("a.b/song", ".txt") == "a.b/song.txt"


@pytest.mark.parametrize("fmt", ["mp3", "flac", "m4a"])
def test_lyrics_in_each_format(tmp_path: Path, fmt: str) -> None:
    path = tmp_path / f"song.{fmt}"
    make_audio(path, fmt)
    assert embedded_lyrics.embeddable(path)
    assert embedded_lyrics.read(path) is None
    embedded_lyrics.write(path, None, "Line one\nLine two")
    assert embedded_lyrics.read(path) == "Line one\nLine two"
    # Only over what was expected.
    with pytest.raises(embedded_lyrics.LyricsConflict):
        embedded_lyrics.write(path, "Something else", "New")
    embedded_lyrics.write(path, "Line one\nLine two", None)
    assert embedded_lyrics.read(path) is None
    broken = tmp_path / "broken.flac"
    broken.write_bytes(b"not audio")
    assert not embedded_lyrics.embeddable(broken)


async def test_lrclib_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(lrclib.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(lrclib, "INTERVAL", 0.0)
    with respx.mock(assert_all_called=False) as web:
        get = web.get(f"{LRCLIB}/api/get")
        search = web.get(f"{LRCLIB}/api/search")
        async with httpx.AsyncClient() as http:
            get.mock(return_value=httpx.Response(200, json={"id": 7, "syncedLyrics": SYNCED}))
            found = await lrclib.find(
                http, LRCLIB, artist="Radiohead", title="X", album="Y", duration_ms=241_400
            )
            assert found == lrclib.Found(7, SYNCED, None, False)
            assert get.calls.last.request.url.params["duration"] == "241"
            # Not found by length: a search result about as long, synced ones first.
            get.mock(return_value=httpx.Response(404))
            search.mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {"id": 1, "duration": 100, "syncedLyrics": "[00:01] wrong length"},
                        {"id": 2, "duration": 240, "plainLyrics": "plain words"},
                        {"id": 3, "duration": 242, "syncedLyrics": "[00:01] synced words"},
                    ],
                )
            )
            found = await lrclib.find(
                http, LRCLIB, artist="Radiohead", title="X", album=None, duration_ms=241_000
            )
            assert found is not None and found.id == 3
            search.mock(return_value=httpx.Response(200, json=[]))
            assert (
                await lrclib.find(http, LRCLIB, artist="A", title="B", album=None, duration_ms=None)
                is None
            )
            search.mock(return_value=httpx.Response(200, json=[{"instrumental": True, "id": 9}]))
            found = await lrclib.find(
                http, LRCLIB, artist="A", title="B", album=None, duration_ms=None
            )
            assert found == lrclib.Found(9, None, None, True)
            # Busy twice: an error (the job stops after a few of these).
            search.mock(return_value=httpx.Response(429))
            with pytest.raises(lrclib.LrclibError):
                await lrclib.find(http, LRCLIB, artist="A", title="B", album=None, duration_ms=None)


@pytest.mark.db
async def test_looking_up_and_writing_lyrics(
    app: FastAPI,
    plex: FakePlex,
    music: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plekstender.worker import apply_tag_changes, look_up_lyrics, scan_music_files

    monkeypatch.setattr(lrclib, "INTERVAL", 0.0)

    # Karma Police already has lyrics in its tags.
    audio = FLAC(music / "301.flac")
    audio["LYRICS"] = "Old words"
    audio.save()

    def get(request: httpx.Request) -> httpx.Response:
        if request.url.params["track_name"] == "Paranoid Android":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "syncedLyrics": SYNCED,
                    "plainLyrics": None,
                    "lyricsfile": WORDS,
                    "hasWordSync": True,
                },
            )
        return httpx.Response(404)

    def search(request: httpx.Request) -> httpx.Response:
        if request.url.params["track_name"] == "Karma Police (Live)":
            return httpx.Response(200, json=[{"id": 2, "duration": 1, "plainLyrics": "Karma"}])
        return httpx.Response(200, json=[])

    plex.router.get(f"{LRCLIB}/api/get").mock(side_effect=get)
    plex.router.get(f"{LRCLIB}/api/search").mock(side_effect=search)

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    assert (await owner.post(f"{base}/lyrics", json={}, headers=CSRF)).status_code == 409
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])
    # Which songs have lyrics: Karma Police's are in its tags (the broken file isn't counted).
    coverage = f"{base}/lyrics/coverage"
    assert (await owner.get(coverage)).json() == {
        "files": 3, "word": 0, "synced": 0, "plain": 1, "instrumental": 0, "none": 2,
        "unchecked": 0,
    }  # fmt: skip
    assert (await owner.get(f"{base}/lyrics/latest")).json() is None

    job = (await owner.post(f"{base}/lyrics", json={}, headers=CSRF)).json()
    assert (await owner.get(f"{base}/lyrics/latest")).json()["id"] == job["id"]
    plan = (await owner.get(f"{base}/lyrics/{job['id']}")).json()
    assert (plan["job"]["status"], plan["looked_up"]) == ("queued", 0)
    await look_up_lyrics.func(job_id=job["id"])
    plan = (await owner.get(f"{base}/lyrics/{job['id']}")).json()
    assert plan["job"]["status"] == "succeeded", plan
    files = plan["files"]
    assert (plan["looked_up"], plan["synced"], plan["word_synced"], plan["plain"]) == (
        files,
        1,
        1,
        1,
    )
    assert plan["untagged"] == files - 2  # the demo has no artist; the broken file nothing
    assert plan["had_lyrics"] == 1
    # Next to the songs (the default): a .lrc for synced lyrics, a .txt for plain ones.
    assert (plan["lrc"], plan["txt"], plan["lyricsfile"], plan["lyrics"]) == (1, 1, 0, 0)
    assert plan["songs"] == 2
    assert [(s["path"], s["lrc"], s["txt"], s["word_synced"]) for s in plan["sample"]] == [
        ("300.flac", True, False, True),
        ("301.flac", False, True, False),
    ]
    # Everywhere: Karma Police keeps the lyrics in its tags unless they're to be replaced.
    everywhere = {"sidecar": True, "lyricsfile": True, "embedded": True}
    both = (await owner.get(f"{base}/lyrics/{job['id']}", params=everywhere)).json()
    assert (both["lrc"], both["txt"], both["lyricsfile"], both["lyrics"]) == (1, 1, 1, 1)
    replace = (
        await owner.get(f"{base}/lyrics/{job['id']}", params=everywhere | {"overwrite": True})
    ).json()
    assert replace["lyrics"] == 2

    apply = f"{base}/lyrics/{job['id']}/apply"
    body = everywhere
    assert (await owner.post(apply, json=body, headers=CSRF)).status_code == 403  # editing off
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    nowhere = {"sidecar": False, "lyricsfile": False, "embedded": False}
    assert (await owner.post(apply, json=nowhere, headers=CSRF)).status_code == 422
    resp = await owner.post(apply, json=body, headers=CSRF)
    assert resp.status_code == 201, resp.text
    edit = resp.json()
    assert (
        edit["description"] == "Lyrics from LRCLIB (.lrc / .txt, .lyricsfile.yaml, tags; library)"
    )
    assert edit["total"] == 4
    assert (await owner.post(apply, json=body, headers=CSRF)).status_code == 409  # not twice
    # Lyrics are shown in the history by their first line.
    shown = {(c["path"], c["field"]): c["new_value"] for c in edit["changes"]}
    assert shown[("300.lrc", "lrc")] == "3 lines: [ar:Radiohead]"
    assert shown[("300.flac", "lyrics")] == "2 lines: Rain down, rain down"

    # Someone made a .txt for Karma Police meanwhile: it's left alone.
    (music / "301.txt").write_text("Mine\n")
    await apply_tag_changes.func(set_id=edit["id"])
    statuses = {
        (c["path"], c["field"]): (c["status"], c["error"])
        for c in (await owner.get(f"{base}/changes", params={"everything": True})).json()[0][
            "changes"
        ]
    }
    assert statuses[("300.lrc", "lrc")] == ("applied", None)
    assert statuses[("300.flac", "lyrics")] == ("applied", None)
    assert statuses[("301.txt", "txt")][0] == "conflict"
    assert (music / "300.lrc").read_text() == SYNCED + "\n"
    assert (
        embedded_lyrics.read(music / "300.flac") == "Rain down, rain down\nCome on rain down on me"
    )
    assert embedded_lyrics.read(music / "301.flac") == "Old words"
    assert (music / "301.txt").read_text() == "Mine\n"
    assert (music / "300.lyricsfile.yaml").read_text() == WORDS
    # Paranoid Android now has word-synced lyrics; Karma Police still its own.
    after = (await owner.get(coverage)).json()
    assert (after["word"], after["plain"], after["none"]) == (1, 1, 1)

    # Undo takes them out again.
    undo = await owner.post(f"{base}/undo", json={"set_id": edit["id"]}, headers=CSRF)
    assert undo.status_code == 201, undo.text
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert not (music / "300.lrc").exists()
    assert not (music / "300.lyricsfile.yaml").exists()
    assert embedded_lyrics.read(music / "300.flac") is None
    assert (await owner.get(coverage)).json()["word"] == 0

    # A .lrc someone adds next to a song counts at the next scan (the song itself unchanged).
    (music / "extra" / "demo.lrc").write_text("[00:01.00] la la")
    rescan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=rescan["id"])
    assert (await owner.get(coverage)).json()["synced"] == 1

    # Some albums only, looked up again: nothing new to write (what's there is known).
    again = (
        await owner.post(f"{base}/lyrics", json={"albums": [200], "again": False}, headers=CSRF)
    ).json()
    await look_up_lyrics.func(job_id=again["id"])
    some: dict[str, Any] = (await owner.get(f"{base}/lyrics/{again['id']}")).json()
    assert (some["files"], some["lrc"]) == (2, 1)  # the .lrc is gone again after the undo


@pytest.mark.db
async def test_a_library_bigger_than_a_query_can_list(
    app: FastAPI, plex: FakePlex, tmp_path: Path
) -> None:
    """70,000 files: more than Postgres takes parameters in one query (65,535)."""
    from sqlalchemy import select, text, update

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import FileScan, PlexServer
    from plekstender.worker import look_up_lyrics

    await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        server = await db.scalar(select(PlexServer))
        assert server is not None
        await db.execute(
            update(PlexServer)
            .where(PlexServer.id == server.id)
            .values(local_path_prefix=str(tmp_path))
        )
        await db.execute(
            text(
                "INSERT INTO media_files (server_id, path, size, mtime, tags, has_cover, reader) "
                "SELECT :s, 'song' || n || '.flac', 1, 1, '{}', false, 6 "
                "FROM generate_series(1, 70000) n"
            ),
            {"s": server.id},
        )
        # Looked up already (not found): nothing to ask LRCLIB.
        await db.execute(
            text(
                "INSERT INTO track_lyrics"
                " (media_file_id, server_id, status, embeddable, looked_up_at)"
                " SELECT id, server_id, 'none', false, now() FROM media_files WHERE server_id = :s"
            ),
            {"s": server.id},
        )
        job = FileScan(server_id=server.id, kind="lyrics", params={}, status="queued")
        db.add(job)
        await db.commit()
        job_id = job.id
    await look_up_lyrics.func(job_id=job_id)
    async with get_sessionmaker()() as db:
        done = await db.get(FileScan, job_id)
        assert done is not None
        assert (done.status, done.error, done.progress) == (
            "succeeded",
            None,
            {"done": 0, "total": 0, "looked_up": 0, "written": 0},
        )


async def test_jobs_of_a_stopped_worker_are_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lookup cut off by a restart mustn't hold its lock (and block the next) for ever."""
    from types import SimpleNamespace

    from procrastinate.jobs import Status

    from plekstender import worker

    closed: list[tuple[int, Status]] = []
    stalled = [SimpleNamespace(id=7, task_name="plekstender.worker.look_up_lyrics")]

    async def get_stalled_jobs(seconds_since_heartbeat: float) -> list[Any]:
        assert seconds_since_heartbeat == worker.STALLED_WORKER_SECONDS
        return stalled

    async def finish_job(job: Any, status: Status, delete_job: bool) -> None:
        closed.append((job.id, status))

    monkeypatch.setattr(worker.app.job_manager, "get_stalled_jobs", get_stalled_jobs)
    monkeypatch.setattr(worker.app.job_manager, "finish_job", finish_job)
    await worker.close_stalled_jobs.func(timestamp=0)
    assert closed == [(7, Status.FAILED)]


@pytest.mark.db
async def test_writing_lyrics_as_they_are_found(
    app: FastAPI,
    plex: FakePlex,
    music: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long lookup writes as it goes (one edit), so stopping halfway loses nothing."""
    from plekstender.worker import look_up_lyrics, scan_music_files

    monkeypatch.setattr(lrclib, "INTERVAL", 0.0)
    monkeypatch.setattr(lyrics_mod, "WRITE_BATCH", 1)  # a batch per song
    asked: list[str] = []

    def get(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["track_name"])
        if request.url.params["track_name"] == "Paranoid Android":
            body = {"id": 1, "syncedLyrics": SYNCED, "lyricsfile": WORDS, "hasWordSync": True}
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    def search(request: httpx.Request) -> httpx.Response:
        if request.url.params["track_name"] == "Karma Police (Live)":
            return httpx.Response(200, json=[{"id": 2, "duration": 1, "plainLyrics": "Karma"}])
        return httpx.Response(200, json=[])

    plex.router.get(f"{LRCLIB}/api/get").mock(side_effect=get)
    plex.router.get(f"{LRCLIB}/api/search").mock(side_effect=search)
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    write = {"sidecar": True, "lyricsfile": True, "embedded": False, "overwrite": False}
    # Writing needs file editing on, and somewhere to write.
    off = await owner.post(f"{base}/lyrics", json={"write": write}, headers=CSRF)
    assert off.status_code == 403
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    nowhere = {"sidecar": False, "lyricsfile": False, "embedded": False}
    bad = await owner.post(f"{base}/lyrics", json={"write": nowhere}, headers=CSRF)
    assert bad.status_code == 422

    # Paranoid Android is looked up first, without writing...
    first = (await owner.post(f"{base}/lyrics", json={"albums": [200]}, headers=CSRF)).json()
    await look_up_lyrics.func(job_id=first["id"])
    assert not (music / "300.lrc").exists()
    assert asked == ["Paranoid Android", "Karma Police (Live)"]
    asked.clear()

    # ...then the whole library, writing as it goes: what was found before is written
    # without asking LRCLIB again.
    job = (await owner.post(f"{base}/lyrics", json={"write": write}, headers=CSRF)).json()
    await look_up_lyrics.func(job_id=job["id"])
    assert asked == []  # every song was looked up already (the demo has no artist)
    assert (music / "300.lrc").read_text() == SYNCED + "\n"
    assert (music / "300.lyricsfile.yaml").read_text() == WORDS
    assert (music / "301.txt").read_text() == "Karma\n"
    plan = (await owner.get(f"{base}/lyrics/{job['id']}")).json()
    assert plan["job"]["status"] == "succeeded"
    assert plan["job"]["progress"]["written"] == 3
    [edit] = (await owner.get(f"{base}/changes", params={"everything": True})).json()
    assert edit["id"] == plan["applied_set_id"]
    assert edit["description"] == (
        "Lyrics from LRCLIB (.lrc / .txt, .lyricsfile.yaml; library, as found)"
    )
    assert (edit["status"], edit["counts"]) == ("applied", {"applied": 3})
    apply = await owner.post(f"{base}/lyrics/{job['id']}/apply", json=write, headers=CSRF)
    assert apply.status_code == 409  # written already
    assert (await owner.get(f"{base}/lyrics/coverage")).json()["word"] == 1

    # Nothing new to write: no empty edit is left in the history.
    again = (await owner.post(f"{base}/lyrics", json={"write": write}, headers=CSRF)).json()
    await look_up_lyrics.func(job_id=again["id"])
    assert len((await owner.get(f"{base}/changes", params={"everything": True})).json()) == 1
    assert (await owner.get(f"{base}/lyrics/{again['id']}")).json()["applied_set_id"] is None


async def _library_with_lyrics(app: FastAPI, plex: FakePlex, folder: Path) -> tuple[Any, str]:
    """Owner client and files base URL: LRCLIB has Paranoid Android (synced) and Karma Police
    (plain); the music folder is scanned and may be written to."""
    from plekstender.worker import scan_music_files

    def get(request: httpx.Request) -> httpx.Response:
        if request.url.params["track_name"] == "Paranoid Android":
            return httpx.Response(200, json={"id": 1, "syncedLyrics": SYNCED})
        return httpx.Response(404)

    def search(request: httpx.Request) -> httpx.Response:
        if request.url.params["track_name"] == "Karma Police (Live)":
            return httpx.Response(200, json=[{"id": 2, "duration": 1, "plainLyrics": "Karma"}])
        return httpx.Response(200, json=[])

    plex.router.get(f"{LRCLIB}/api/get").mock(side_effect=get)
    plex.router.get(f"{LRCLIB}/api/search").mock(side_effect=search)
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(folder)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    return owner, base


@pytest.mark.db
async def test_stopping_and_cancelling_a_lookup(
    app: FastAPI,
    plex: FakePlex,
    music: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func, select, update

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import FileScan, TrackLyrics
    from plekstender.worker import apply_tag_changes, look_up_lyrics

    monkeypatch.setattr(lrclib, "INTERVAL", 0.0)
    monkeypatch.setattr(lyrics_mod, "WRITE_BATCH", 1)
    owner, base = await _library_with_lyrics(app, plex, music)
    write = {"sidecar": True, "lyricsfile": False, "embedded": False, "overwrite": False}

    async def found() -> int:
        async with get_sessionmaker()() as db:
            return int(await db.scalar(select(func.count()).select_from(TrackLyrics)) or 0)

    # Stopped before it started (still queued): done straight away, nothing found.
    queued = (await owner.post(f"{base}/lyrics", json={}, headers=CSRF)).json()
    stopped = await owner.post(f"{base}/lyrics/{queued['id']}/stop", json={}, headers=CSRF)
    assert (stopped.json()["status"], stopped.json()["error"]) == (
        "failed",
        lyrics_mod.STOP_MESSAGES["stop"],
    )
    await look_up_lyrics.func(job_id=queued["id"])  # the worker leaves it alone
    assert await found() == 0
    again = await owner.post(f"{base}/lyrics/{queued['id']}/stop", json={}, headers=CSRF)
    assert again.status_code == 409  # not running

    # Asked to stop while it runs: the worker stops before its next song, keeping what it found.
    job = (await owner.post(f"{base}/lyrics", json={}, headers=CSRF)).json()
    checks = 0
    real_check = lyrics_mod._check_stop

    async def stop_after_one(db: Any, job_id: int) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:  # the first song is looked up, then the page asks it to stop
            await db.execute(
                update(FileScan).where(FileScan.id == job_id).values(params={"stop": "stop"})
            )
            await db.commit()
        await real_check(db, job_id)

    monkeypatch.setattr(lyrics_mod, "_check_stop", stop_after_one)
    await look_up_lyrics.func(job_id=job["id"])
    plan = (await owner.get(f"{base}/lyrics/{job['id']}")).json()
    assert (plan["job"]["status"], plan["job"]["error"]) == (
        "failed",
        lyrics_mod.STOP_MESSAGES["stop"],
    )
    assert await found() == 1  # kept

    # Cancelled while writing as it goes, taking out what it wrote: what this run found is
    # thrown away, and an undo edit takes the written lyrics out again.
    monkeypatch.setattr(lyrics_mod, "_check_stop", real_check)
    checks = 0

    async def cancel_later(db: Any, job_id: int) -> None:
        nonlocal checks
        checks += 1
        if checks == 6:
            params = await db.scalar(select(FileScan.params).where(FileScan.id == job_id))
            await db.execute(
                update(FileScan)
                .where(FileScan.id == job_id)
                .values(params=params | {"stop": "undo"})
            )
            await db.commit()
        await real_check(db, job_id)

    monkeypatch.setattr(lyrics_mod, "_check_stop", cancel_later)
    writing = (
        await owner.post(f"{base}/lyrics", json={"write": write, "again": True}, headers=CSRF)
    ).json()
    await look_up_lyrics.func(job_id=writing["id"])
    monkeypatch.setattr(lyrics_mod, "_check_stop", real_check)
    plan = (await owner.get(f"{base}/lyrics/{writing['id']}")).json()
    assert plan["job"]["error"] == lyrics_mod.STOP_MESSAGES["undo"]
    assert (music / "300.lrc").exists()  # written before it was cancelled...
    assert await found() == 0  # ...but what it found is gone
    edits = (await owner.get(f"{base}/changes", params={"everything": True})).json()
    assert [(e["kind"], e["status"]) for e in edits[:2]] == [
        ("undo", "queued"),
        ("edit", "applied"),
    ]
    await apply_tag_changes.func(set_id=edits[0]["id"])
    assert not (music / "300.lrc").exists()  # ...and taken out again

    # A lookup whose worker went away (a restart): stopping it is done by the app itself.
    lost = (await owner.post(f"{base}/lyrics", json={"write": write}, headers=CSRF)).json()
    await look_up_lyrics.func(job_id=lost["id"])
    assert (music / "300.lrc").exists()
    async with get_sessionmaker()() as db:
        await db.execute(
            update(FileScan)
            .where(FileScan.id == lost["id"])
            .values(
                status="running",
                updated_at=func.now() - dt.timedelta(hours=1),
                started_at=func.now() - dt.timedelta(hours=2),
            )
        )
        await db.commit()
    cancel = await owner.post(
        f"{base}/lyrics/{lost['id']}/stop", json={"cancel": True, "undo": True}, headers=CSRF
    )
    assert (cancel.json()["status"], cancel.json()["error"]) == (
        "failed",
        lyrics_mod.STOP_MESSAGES["undo"],
    )
    assert await found() == 0
    [undo, *_] = (await owner.get(f"{base}/changes", params={"everything": True})).json()
    assert undo["kind"] == "undo"
    await apply_tag_changes.func(set_id=undo["id"])
    assert not (music / "300.lrc").exists()

    latest_any = (await owner.get(f"{base}/lyrics/latest", params={"scope": "any"})).json()
    assert latest_any["id"] == lost["id"]


@pytest.mark.db
async def test_an_interrupted_lookup_closes_its_edit(
    app: FastAPI,
    plex: FakePlex,
    music: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func, update

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import FileScan, TagChangeSet
    from plekstender.worker import cleanup_sessions, look_up_lyrics

    monkeypatch.setattr(lrclib, "INTERVAL", 0.0)
    owner, base = await _library_with_lyrics(app, plex, music)
    write = {"sidecar": True, "lyricsfile": False, "embedded": False, "overwrite": False}
    job = (await owner.post(f"{base}/lyrics", json={"write": write}, headers=CSRF)).json()
    await look_up_lyrics.func(job_id=job["id"])
    set_id = (await owner.get(f"{base}/lyrics/{job['id']}")).json()["applied_set_id"]
    # As if the worker had stopped halfway: the lookup and its edit left "running".
    async with get_sessionmaker()() as db:
        await db.execute(
            update(FileScan)
            .where(FileScan.id == job["id"])
            .values(status="running", updated_at=func.now() - dt.timedelta(hours=1))
        )
        await db.execute(
            update(TagChangeSet).where(TagChangeSet.id == set_id).values(status="running")
        )
        await db.commit()
    await cleanup_sessions.func(timestamp=0)
    plan = (await owner.get(f"{base}/lyrics/{job['id']}")).json()
    assert plan["job"]["status"] == "failed"
    [edit] = (await owner.get(f"{base}/changes", params={"everything": True})).json()
    assert (edit["id"], edit["status"]) == (set_id, "applied")


def test_which_songs_a_lookup_asks_about() -> None:
    import datetime as dt

    from plekstender.tags.lyrics import _due, lookup_mode

    now = dt.datetime(2026, 11, 27, tzinfo=dt.UTC)
    yesterday = now - dt.timedelta(days=1)
    never, not_found, found_plain = None, ("none", yesterday), ("plain", yesterday)
    # New (the default): never looked up, or errors; LRCLIB's misses wait a month.
    assert _due(never, "new", now) and not _due(not_found, "new", now)
    assert _due(("none", now - dt.timedelta(days=31)), "new", now)
    # Songs without lyrics: however recently LRCLIB missed them, but not songs that have some.
    assert _due(not_found, "missing", now, has=None)
    assert not _due(never, "missing", now, has="plain")
    assert not _due(found_plain, "missing", now, has=None)  # found, waiting to be written
    # Without synced lyrics: plain ones are asked about again.
    assert _due(found_plain, "unsynced", now, has="plain")
    assert not _due(never, "unsynced", now, has="word")
    assert not _due(("synced", yesterday), "unsynced", now, has=None)
    assert _due(("synced", yesterday), "all", now, has="synced")
    # Jobs from before modes.
    assert lookup_mode({"again": True}) == "all" and lookup_mode({}) == "new"
    assert lookup_mode({"mode": "missing"}) == "missing"
