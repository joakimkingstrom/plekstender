"""Scanning the music folder (worker) and comparing the files' tags with Plex."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC

from plekstender.config import get_settings
from plekstender.previews import ffmpeg_exe
from plekstender.tags.read import read_file
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs


def make_audio(path: Path, fmt: str = "flac") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 - test fixture, fixed arguments
        [
            ffmpeg_exe(get_settings()),
            "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:a", "flac" if fmt == "flac" else "libmp3lame" if fmt == "mp3" else "aac",
            "-y", str(path),
        ],
        check=True,
    )  # fmt: skip


def tag_flac(path: Path, **tags: str) -> None:
    audio = FLAC(path)
    for key, value in tags.items():
        audio[key] = value
    audio.save()


OK_COMPUTER = {
    "artist": "Radiohead",
    "album": "OK Computer",
    "albumartist": "Radiohead",
    "date": "1997-05-21",
    "genre": "Rock",
    "discnumber": "1",
}


@pytest.fixture
def music(tmp_path: Path) -> Path:
    """Files where the fake Plex says its tracks are (/m/{key}.flac), plus extras."""
    root = tmp_path / "music"
    make_audio(root / "300.flac")
    tag_flac(root / "300.flac", title="Paranoid Android", tracknumber="2/12", **OK_COMPUTER)
    make_audio(root / "301.flac")
    tag_flac(root / "301.flac", title="Karma Police (Live)", tracknumber="6", **OK_COMPUTER)
    make_audio(root / "extra" / "demo.mp3", "mp3")
    demo = EasyID3()
    demo["title"] = "A demo"
    demo.save(root / "extra" / "demo.mp3")
    (root / "broken.flac").write_bytes(b"not audio at all")
    make_audio(root / ".hidden.flac")
    outside = tmp_path / "outside"
    make_audio(outside / "secret.flac")
    os.symlink(outside / "secret.flac", root / "link.flac")
    return root


def test_reading_tags(music: Path) -> None:
    info = read_file(music / "300.flac")
    assert info["format"] == "flac"
    assert 900 <= info["duration_ms"] <= 1100
    tags = info["tags"]
    assert (tags["title"], tags["artist"], tags["track"], tags["track_total"]) == (
        "Paranoid Android",
        "Radiohead",
        2,
        12,
    )
    assert tags["genre"] == ["Rock"]
    assert info["has_cover"] is False


@pytest.mark.db
async def test_scan_and_compare(app: FastAPI, plex: FakePlex, music: Path) -> None:
    from plekstender.worker import scan_music_files

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"

    summary = (await owner.get(f"{base}/summary")).json()
    assert (summary["mapped"], summary["files"]) == (False, 0)
    assert (await owner.post(f"{base}/scans", headers=CSRF)).status_code == 400

    # The mapping alone (without saving artist.jpg).
    resp = await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    assert resp.status_code == 200, resp.text

    async def scan() -> dict[str, Any]:
        resp = await owner.post(f"{base}/scans", headers=CSRF)
        assert resp.status_code == 201, resp.text
        await scan_music_files.func(scan_id=resp.json()["id"])
        result: dict[str, Any] = (await owner.get(f"{base}/summary")).json()
        assert result["last"]["status"] == "succeeded", result["last"]["error"]
        return result

    first = await scan()
    # 300, 301, the demo and the broken file; not the hidden file or the symlink.
    assert (first["files"], first["matched"], first["errors"]) == (4, 2, 1)
    assert first["last"]["stats"] == {
        "files": 4,
        "read": 4,
        "unchanged": 0,
        "removed": 0,
        "errors": 1,
        "matched": 2,
    }

    album = (await owner.get(f"{base}/albums/200")).json()
    assert album["scanned"] is True
    rows = {t["rating_key"]: t for t in album["tracks"]}
    android = rows[300]
    assert android["file"] == "300.flac" and android["format"] == "flac"
    assert android["differences"] == []  # the file agrees with Plex
    assert rows[301]["differences"] == ["title"]
    assert (rows[301]["plex"]["title"], rows[301]["tags"]["title"]) == (
        "Karma Police",
        "Karma Police (Live)",
    )

    # Rescans only read what changed, and forget files that are gone.
    (music / "extra" / "demo.mp3").unlink()
    tag_flac(music / "301.flac", title="Karma Police")
    os.utime(music / "301.flac", (1, 1_800_000_000))
    second = await scan()
    assert second["last"]["stats"]["read"] == 1
    assert second["last"]["stats"]["removed"] == 1
    assert second["files"] == 3
    album = (await owner.get(f"{base}/albums/200")).json()
    assert {t["rating_key"]: t["differences"] for t in album["tracks"]}[301] == []
    # Case counts: "radiohead" isn't "Radiohead".
    tag_flac(music / "300.flac", artist="radiohead")
    os.utime(music / "300.flac", (1, 1_800_000_100))
    await scan()
    album = (await owner.get(f"{base}/albums/200")).json()
    assert {t["rating_key"]: t["differences"] for t in album["tracks"]}[300] == ["artist"]

    # Only the server's owner sees files.
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = await connect(friend)
    await run_queued_syncs()
    assert (await friend.get(f"/api/servers/{friend_sid}/files/summary")).status_code == 403


@pytest.mark.db
async def test_editing_tags_with_history_and_undo(
    app: FastAPI, plex: FakePlex, music: Path
) -> None:
    from plekstender.worker import apply_tag_changes, scan_music_files

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])
    album = (await owner.get(f"{base}/albums/200")).json()
    files = {t["rating_key"]: t["file_id"] for t in album["tracks"]}

    async def edit(changes: list[dict[str, Any]], description: str | None = None) -> dict[str, Any]:
        resp = await owner.post(
            f"{base}/edits", json={"changes": changes, "description": description}, headers=CSRF
        )
        assert resp.status_code == 201, resp.text
        body: dict[str, Any] = resp.json()
        return body

    async def run(change_set: dict[str, Any]) -> dict[str, Any]:
        await apply_tag_changes.func(set_id=change_set["id"])
        [done] = [
            s for s in (await owner.get(f"{base}/changes")).json() if s["id"] == change_set["id"]
        ]
        return done

    # Off until the owner turns it on: it writes to the music files.
    off = await owner.post(
        f"{base}/edits",
        json={"changes": [{"file_id": files[301], "field": "title", "value": "Karma Police"}]},
        headers=CSRF,
    )
    assert off.status_code == 403
    on = await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    assert on.json()["tag_editing"] is True

    # Fix a title and set the genres of two files, in one edit.
    fix = await edit(
        [
            {"file_id": files[301], "field": "title", "value": "Karma Police"},
            {"file_id": files[301], "field": "genre", "value": ["Alternative", "Rock"]},
            {"file_id": files[300], "field": "genre", "value": ["Alternative", "Rock"]},
            {"file_id": files[300], "field": "title", "value": "Paranoid Android"},  # no change
        ],
        "Fix OK Computer",
    )
    assert (fix["status"], fix["description"], len(fix["changes"])) == (
        "queued",
        "Fix OK Computer",
        3,
    )
    assert FLAC(music / "301.flac")["title"] == [
        "Karma Police (Live)"
    ]  # not yet: the worker writes
    done = await run(fix)
    assert done["status"] == "applied", done
    assert FLAC(music / "301.flac")["title"] == ["Karma Police"]
    assert FLAC(music / "300.flac")["genre"] == ["Alternative", "Rock"]
    album = (await owner.get(f"{base}/albums/200")).json()
    assert {t["rating_key"]: t["tags"].get("title") for t in album["tracks"]}[301] == "Karma Police"
    assert ("3", "/m") in plex.library.refreshed  # Plex was asked to rescan the folder

    # Edited elsewhere since the scan: that change isn't written (a conflict).
    tag_flac(music / "300.flac", composer="Thom Yorke")
    stale = await edit([{"file_id": files[300], "field": "composer", "value": "Radiohead"}])
    clash = await run(stale)
    assert clash["status"] == "failed"
    assert clash["changes"][0]["status"] == "conflict"
    assert FLAC(music / "300.flac")["composer"] == ["Thom Yorke"]

    # Undo picked changes: Karma Police's title only (its genre stays).
    title_change = next(c for c in done["changes"] if c["field"] == "title")
    resp = await owner.post(f"{base}/undo", json={"change_ids": [title_change["id"]]}, headers=CSRF)
    assert resp.status_code == 201, resp.text
    undo = await run(resp.json())
    assert undo["status"] == "applied" and undo["kind"] == "undo"
    assert FLAC(music / "301.flac")["title"] == ["Karma Police (Live)"]
    assert FLAC(music / "301.flac")["genre"] == ["Alternative", "Rock"]
    again = await owner.post(
        f"{base}/undo", json={"change_ids": [title_change["id"]]}, headers=CSRF
    )
    assert again.status_code == 400  # already undone

    history = (await owner.get(f"{base}/changes", params={"album": 200})).json()
    assert [s["description"] for s in history] == [
        "Undid 1 change",
        "Edited 1 tag in 1 file",
        "Fix OK Computer",
    ]
    original = {c["id"]: c for c in history[2]["changes"]}
    assert original[title_change["id"]]["undone"] is True
    assert history[2]["user"] == "owner"
    nothing = await owner.post(
        f"{base}/edits",
        json={
            "changes": [{"file_id": files[300], "field": "title", "value": " Paranoid  Android "}]
        },
        headers=CSRF,
    )
    assert nothing.status_code == 400  # the same value (spaces don't count)
