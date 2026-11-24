"""Lyrics for music files: LRCLIB lookups, lyrics in each format's tags, and writing them as
an edit (next to the songs and in their tags) that undo takes out again."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from mutagen.flac import FLAC

from plekstender.tags import embedded_lyrics, lrclib
from plekstender.tags import lyrics as lyrics_mod
from tests.fakes import CSRF, FakePlex
from tests.test_files import make_audio, music  # noqa: F401 - the music folder fixture
from tests.test_sync import owner_with_library

LRCLIB = "https://lrclib.net"
SYNCED = "[ar:Radiohead]\n[00:12.30] Rain down, rain down\n[00:15.80] Come on rain down on me"


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
async def test_looking_up_and_writing_lyrics(app: FastAPI, plex: FakePlex, music: Path) -> None:  # noqa: F811
    from plekstender.worker import apply_tag_changes, look_up_lyrics, scan_music_files

    # Karma Police already has lyrics in its tags.
    audio = FLAC(music / "301.flac")
    audio["LYRICS"] = "Old words"
    audio.save()

    def get(request: httpx.Request) -> httpx.Response:
        if request.url.params["track_name"] == "Paranoid Android":
            return httpx.Response(200, json={"id": 1, "syncedLyrics": SYNCED, "plainLyrics": None})
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

    job = (await owner.post(f"{base}/lyrics", json={}, headers=CSRF)).json()
    plan = (await owner.get(f"{base}/lyrics/{job['id']}")).json()
    assert (plan["job"]["status"], plan["looked_up"]) == ("queued", 0)
    await look_up_lyrics.func(job_id=job["id"])
    plan = (await owner.get(f"{base}/lyrics/{job['id']}")).json()
    assert plan["job"]["status"] == "succeeded", plan
    files = plan["files"]
    assert (plan["looked_up"], plan["synced"], plan["plain"]) == (files, 1, 1)
    assert plan["untagged"] == files - 2  # the demo has no artist; the broken file nothing
    assert plan["had_lyrics"] == 1
    # Next to the songs (the default): a .lrc for synced lyrics, a .txt for plain ones.
    assert (plan["lrc"], plan["txt"], plan["lyrics"], plan["songs"]) == (1, 1, 0, 2)
    assert [(s["path"], s["lrc"], s["txt"]) for s in plan["sample"]] == [
        ("300.flac", True, False),
        ("301.flac", False, True),
    ]
    # In the tags too: Karma Police keeps its own unless they're to be replaced.
    both = (await owner.get(f"{base}/lyrics/{job['id']}", params={"placement": "both"})).json()
    assert (both["lrc"], both["txt"], both["lyrics"]) == (1, 1, 1)
    replace = (
        await owner.get(
            f"{base}/lyrics/{job['id']}", params={"placement": "both", "overwrite": True}
        )
    ).json()
    assert replace["lyrics"] == 2

    apply = f"{base}/lyrics/{job['id']}/apply"
    body = {"placement": "both"}
    assert (await owner.post(apply, json=body, headers=CSRF)).status_code == 403  # editing off
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    resp = await owner.post(apply, json=body, headers=CSRF)
    assert resp.status_code == 201, resp.text
    edit = resp.json()
    assert edit["description"] == "Lyrics from LRCLIB written next to and into the songs (library)"
    assert edit["total"] == 3
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
        for c in (await owner.get(f"{base}/changes")).json()[0]["changes"]
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

    # Undo takes them out again.
    undo = await owner.post(f"{base}/undo", json={"set_id": edit["id"]}, headers=CSRF)
    assert undo.status_code == 201, undo.text
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert not (music / "300.lrc").exists()
    assert embedded_lyrics.read(music / "300.flac") is None

    # Some albums only, looked up again: nothing new to write (what's there is known).
    again = (
        await owner.post(f"{base}/lyrics", json={"albums": [200], "again": False}, headers=CSRF)
    ).json()
    await look_up_lyrics.func(job_id=again["id"])
    some: dict[str, Any] = (await owner.get(f"{base}/lyrics/{again['id']}")).json()
    assert (some["files"], some["lrc"]) == (2, 1)  # the .lrc is gone again after the undo
