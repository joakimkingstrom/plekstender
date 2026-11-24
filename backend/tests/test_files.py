"""Scanning the music folder (worker) and comparing the files' tags with Plex."""

from __future__ import annotations

import os
import re
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
        "lyrics": 0,
        "nfo": 0,
        "images": 0,
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


def test_planning_plex_metadata_into_files() -> None:
    from plekstender.tags.plexsync import plan_file

    plex = {
        "title": "Karma Police",
        "artist": "Radiohead",
        "album": "OK Computer",
        "albumartist": "Radiohead",
        "track": 6,
        "disc": 1,
        "year": 1997,
        "genre": ["Alternative", "Rock"],
    }
    every = {
        "title",
        "artist",
        "album",
        "albumartist",
        "tracknumber",
        "discnumber",
        "date",
        "genre",
    }
    same = {
        "title": "Karma Police",
        "artist": "Radiohead",
        "album": "OK Computer",
        "albumartist": "Radiohead",
        "tracknumber": "6/12",
        "date": "1997-05-21",
        "genre": ["Rock", "Alternative"],
    }
    assert plan_file(same, plex, every) == {}  # "6/12" is 6; 1997-05-21 is 1997; any order
    differs = {
        **same,
        "title": "Karma Police (Live)",
        "tracknumber": "5/12",
        "date": "2009",
        "discnumber": "2",
        "genre": ["Rock"],
    }
    assert plan_file(differs, plex, every) == {
        "title": "Karma Police",
        "tracknumber": "6/12",  # keeps the file's total
        "date": "1997",
        "discnumber": "1",
        "genre": ["Alternative", "Rock"],
    }
    assert plan_file(differs, plex, {"title"}) == {"title": "Karma Police"}  # only chosen fields
    assert (
        plan_file({}, {**plex, "genre": [], "year": None}, {"genre", "date"}) == {}
    )  # nothing in Plex


@pytest.mark.db
async def test_writing_plex_metadata_in_bulk(app: FastAPI, plex: FakePlex, music: Path) -> None:
    from plekstender.worker import apply_tag_changes, scan_music_files

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    # Paranoid Android already matches Plex; Karma Police's file says "(Live)".
    preview = (
        await owner.post(f"{base}/plex-sync/preview", json={"scope": "library"}, headers=CSRF)
    ).json()
    assert (preview["files"], preview["changes"], preview["by_field"]) == (1, 1, {"title": 1})
    assert preview["samples"][0] == {
        "path": "301.flac",
        "field": "title",
        "old_unknown": False,
        "old_value": "Karma Police (Live)",
        "new_value": "Karma Police",
    }
    nothing = await owner.post(
        f"{base}/plex-sync", json={"scope": "artist", "key": 101}, headers=CSRF
    )
    assert nothing.status_code == 400  # Portishead's files: none in the folder

    resp = await owner.post(f"{base}/plex-sync", json={"scope": "library"}, headers=CSRF)
    assert resp.status_code == 201, resp.text
    change_set = resp.json()
    assert change_set["description"] == "Plex's metadata to 1 file (the library)"
    assert change_set["counts"] == {"pending": 1} and change_set["total"] == 1
    await apply_tag_changes.func(set_id=change_set["id"])
    assert FLAC(music / "301.flac")["title"] == ["Karma Police"]
    [done] = (await owner.get(f"{base}/changes")).json()
    assert (done["status"], done["counts"], done["undoable"]) == ("applied", {"applied": 1}, 1)

    # Undo the whole edit, by its id (big edits have too many changes to list).
    resp = await owner.post(f"{base}/undo", json={"set_id": change_set["id"]}, headers=CSRF)
    assert resp.status_code == 201, resp.text
    await apply_tag_changes.func(set_id=resp.json()["id"])
    assert FLAC(music / "301.flac")["title"] == ["Karma Police (Live)"]
    history = (await owner.get(f"{base}/changes")).json()
    assert history[1]["undoable"] == 0
    assert (await owner.post(f"{base}/undo", json={}, headers=CSRF)).status_code == 422


# --- MusicBrainz releases --------------------------------------------------------------------

RELEASE_ID = "0b6b4ba0-d36f-47bd-b4ea-6a5b91842d29"
GROUP_ID = "b1392450-e666-3926-a536-22c65f834433"
RADIOHEAD_ID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
OK_TITLES = [
    "Airbag", "Paranoid Android", "Subterranean Homesick Alien", "Exit Music (For a Film)",
    "Let Down", "Karma Police", "Fitter Happier", "Electioneering", "Climbing Up the Walls",
    "No Surprises", "Lucky", "The Tourist",
]  # fmt: skip


def mb_release() -> dict[str, Any]:
    credit = [{"name": "Radiohead", "joinphrase": "", "artist": {"id": RADIOHEAD_ID}}]
    return {
        "id": RELEASE_ID,
        "title": "OK Computer",
        "date": "1997-05-21",
        "country": "GB",
        "status": "Official",
        "barcode": "724385522925",
        "artist-credit": credit,
        "label-info": [{"catalog-number": "NODATA 02", "label": {"name": "Parlophone"}}],
        "release-group": {
            "id": GROUP_ID,
            "primary-type": "Album",
            "first-release-date": "1997-05-21",
        },
        "media": [
            {
                "position": 1,
                "format": "CD",
                "tracks": [
                    {
                        "id": f"00000000-0000-4000-8000-0000000000{n:02d}",
                        "position": n,
                        "title": title,
                        "length": 1000,
                        "recording": {"id": f"10000000-0000-4000-8000-0000000000{n:02d}"},
                        "artist-credit": credit,
                    }
                    for n, title in enumerate(OK_TITLES, start=1)
                ],
            }
        ],
    }


class FakeMusicBrainz:
    def __init__(self, router: Any) -> None:
        import httpx

        self.searches: list[str] = []
        self.cover_requests = 0

        def search(request: httpx.Request) -> httpx.Response:
            self.searches.append(request.url.params["query"])
            assert request.headers["User-Agent"].startswith("Plekstender/")
            summary = {**mb_release(), "score": 100, "track-count": 12}
            summary["media"] = [{"format": "CD", "track-count": 12}]
            return httpx.Response(200, json={"releases": [summary]})

        def lookup(request: httpx.Request) -> httpx.Response:
            if not request.url.path.endswith(RELEASE_ID):
                return httpx.Response(404, json={"error": "Not Found"})
            return httpx.Response(200, json=mb_release())

        def cover(request: httpx.Request) -> httpx.Response:
            self.cover_requests += 1
            if RELEASE_ID not in request.url.path:
                return httpx.Response(404)
            return httpx.Response(
                200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}
            )

        router.get("https://musicbrainz.org/ws/2/release").mock(side_effect=search)
        router.get(url__regex=r"https://musicbrainz\.org/ws/2/release/.+").mock(side_effect=lookup)
        router.get(url__regex=r"https://coverartarchive\.org/release/.+").mock(side_effect=cover)


@pytest.fixture
def mb(plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> FakeMusicBrainz:
    from plekstender import musicbrainz

    monkeypatch.setattr(musicbrainz, "INTERVAL", 0.0)
    musicbrainz.clear_cache()
    return FakeMusicBrainz(plex.router)


def test_matching_files_to_release_tracks() -> None:
    from plekstender.musicbrainz import Release, ReleaseTrack
    from plekstender.tags.mbapply import AlbumFile, match, plan_file, wanted

    tracks = [
        ReleaseTrack(f"t{n}", f"r{n}", 1, n, title, "Radiohead", "a", 200_000)
        for n, title in enumerate(["Airbag", "Paranoid Android", "Let Down"], start=1)
    ]
    release = Release(
        "rel", "OK Computer", "Radiohead", "a", "1997", None, "GB", "g", 1, None, tracks
    )  # type: ignore[arg-type]
    files = [
        # Numbered wrongly, but the title and length say it's Let Down.
        AlbumFile(1, "a.flac", 1, {"title": "Let Down (Remastered)", "track": 2}, 199_000),
        AlbumFile(2, "b.flac", 2, {"title": "Paranoid Android", "track": 2}, 383_000),
        AlbumFile(3, "c.flac", 3, {"title": "Hidden track"}, 30_000),  # no good match
    ]
    assert match(files, release) == {1: "t3", 2: "t2", 3: None}

    values = wanted(release, tracks[1])
    fields = {"title", "tracknumber", "discnumber", "date", "musicbrainz_albumid"}
    # "2" already is track 2; a full date isn't cut to the year; one disc: no disc number.
    assert plan_file(
        {"title": "Paranoid Android", "tracknumber": "2", "date": "1997-05-21"}, values, fields
    ) == {"musicbrainz_albumid": "rel"}
    assert plan_file({"tracknumber": "3/3", "musicbrainz_albumid": "REL"}, values, fields) == {
        "title": "Paranoid Android", "tracknumber": "2/3", "date": "1997",
    }  # fmt: skip


@pytest.mark.db
async def test_applying_a_musicbrainz_release(
    app: FastAPI, plex: FakePlex, music: Path, mb: FakeMusicBrainz
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

    # Search: by title and artist; a release link finds that release.
    found = (
        await owner.get(
            f"{base}/musicbrainz/search", params={"album": "OK Computer", "artist": "Radiohead"}
        )
    ).json()
    assert mb.searches == ['release:"OK Computer" AND artist:"Radiohead"']
    assert found[0] | {"id": RELEASE_ID} == found[0]
    assert (found[0]["formats"], found[0]["label"], found[0]["track_count"]) == (
        "CD",
        "Parlophone",
        12,
    )
    linked = (
        await owner.get(
            f"{base}/musicbrainz/search",
            params={"album": f"https://musicbrainz.org/release/{RELEASE_ID}"},
        )
    ).json()
    assert [r["id"] for r in linked] == [RELEASE_ID] and len(mb.searches) == 1

    # The cover, kept after the first request; none for unknown releases.
    for _ in range(2):
        resp = await owner.get(f"{base}/musicbrainz/cover/{RELEASE_ID}")
        assert resp.status_code == 200 and resp.content == b"\xff\xd8jpeg"
    assert mb.cover_requests == 1
    assert (await owner.get(f"{base}/musicbrainz/cover/{GROUP_ID}")).status_code == 404

    # Preview: matched by number and title; only what differs.
    body = {"release_id": RELEASE_ID}
    preview = (
        await owner.post(f"{base}/albums/200/musicbrainz/preview", json=body, headers=CSRF)
    ).json()
    assert len(preview["tracks"]) == 12
    by_path = {f["path"]: f for f in preview["files"]}
    assert by_path["300.flac"]["track_id"].endswith("02")
    assert by_path["301.flac"]["track_id"].endswith("06")
    karma = {c["field"]: c["new_value"] for c in by_path["301.flac"]["changes"]}
    assert karma["title"] == "Karma Police"
    assert karma["musicbrainz_albumid"] == RELEASE_ID
    assert karma["releasecountry"] == "GB" and karma["originaldate"] == "1997-05-21"
    assert "tracknumber" not in karma and "date" not in karma and "discnumber" not in karma
    assert preview["changes"] == 17

    # Pick and choose: leave Karma Police alone; a track can't be used twice.
    t2 = by_path["300.flac"]["track_id"]
    ids = {f["path"]: f["file_id"] for f in preview["files"]}
    only = {**body, "mapping": {ids["300.flac"]: t2, ids["301.flac"]: None}}
    picked = (
        await owner.post(f"{base}/albums/200/musicbrainz/preview", json=only, headers=CSRF)
    ).json()
    assert picked["changes"] == 8
    twice = {**body, "mapping": {ids["300.flac"]: t2, ids["301.flac"]: t2}}
    resp = await owner.post(f"{base}/albums/200/musicbrainz/preview", json=twice, headers=CSRF)
    assert resp.status_code == 422

    # Writing needs tag editing on; then the release's tags are in the files.
    assert (
        await owner.post(f"{base}/albums/200/musicbrainz", json=body, headers=CSRF)
    ).status_code == 403
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    resp = await owner.post(f"{base}/albums/200/musicbrainz", json=body, headers=CSRF)
    assert resp.status_code == 201, resp.text
    change_set = resp.json()
    assert change_set["description"] == "MusicBrainz release OK Computer (1997, GB, CD) to 2 files"
    await apply_tag_changes.func(set_id=change_set["id"])
    karma_file = FLAC(music / "301.flac")
    assert karma_file["title"] == ["Karma Police"]
    assert karma_file["musicbrainz_albumid"] == [RELEASE_ID]
    assert karma_file["musicbrainz_releasetrackid"] == [t2[:-2] + "06"]
    again = (
        await owner.post(f"{base}/albums/200/musicbrainz/preview", json=body, headers=CSRF)
    ).json()
    assert again["changes"] == 0  # the re-read files now hold every MusicBrainz tag


@pytest.mark.db
async def test_tag_editing_tab(
    app: FastAPI, plex: FakePlex, music: Path, mb: FakeMusicBrainz
) -> None:
    """Albums whose files need a look, and MusicBrainz's best match for each."""
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

    # OK Computer: Karma Police's title differs from Plex; no file has a release ID.
    page = (await owner.get(f"{base}/albums")).json()
    assert [(a["title"], a["artist_title"], a["files"]) for a in page["items"]] == [
        ("OK Computer", "Radiohead", 2)
    ]
    [ok] = page["items"]
    assert (ok["differs"], ok["differing_fields"], ok["no_mbid"], ok["missing"]) == (
        1,
        ["title"],
        2,
        0,
    )
    assert page["counts"] == {"any": 1, "differs": 1, "no_mbid": 1, "missing": 0, "errors": 0}
    assert page["not_in_plex"] == 1  # extra/demo.mp3
    missing = (await owner.get(f"{base}/albums", params={"issue": "missing"})).json()
    assert missing["items"] == []
    assert (await owner.get(f"{base}/albums", params={"q": "radio"})).json()["total"] == 1
    assert (await owner.get(f"{base}/albums", params={"q": "nothing"})).json()["total"] == 0

    # Found by searching: both files match, but the release has 12 tracks, so it's
    # proposed, not a clear match.
    best = (await owner.post(f"{base}/albums/200/musicbrainz/best", headers=CSRF)).json()
    assert best["release"]["id"] == RELEASE_ID
    assert (best["source"], best["files"], best["matched"], best["changes"]) == (
        "search",
        2,
        2,
        17,
    )
    assert best["clear"] is False
    assert best["note"] == "The release has 12 tracks; the album has 2 files."
    assert mb.searches == ['release:"OK Computer" AND artist:"Radiohead"']

    # Written: the release ID in the files is used next time (no search).
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    written = (
        await owner.post(
            f"{base}/albums/200/musicbrainz", json={"release_id": RELEASE_ID}, headers=CSRF
        )
    ).json()
    await apply_tag_changes.func(set_id=written["id"])
    best = (await owner.post(f"{base}/albums/200/musicbrainz/best", headers=CSRF)).json()
    assert (best["source"], best["changes"]) == ("tags", 0)
    assert len(mb.searches) == 1
    page = (await owner.get(f"{base}/albums")).json()
    assert page["counts"]["no_mbid"] == 0

    friend, _ = await sign_in(app, plex, "friend-token")
    assert (await friend.get(f"{base}/albums")).status_code in (403, 404)


# --- Ratings in the files --------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["mp3", "flac", "m4a"])
def test_ratings_in_each_format(tmp_path: Path, fmt: str) -> None:
    from mutagen.id3 import ID3, POPM

    from plekstender.tags.write import Planned as Change
    from plekstender.tags.write import apply

    path = tmp_path / f"song.{fmt}"
    make_audio(path, fmt)
    if fmt == "mp3":  # another player's rating and play count are kept (and updated)
        id3 = ID3()
        id3.add(POPM(email="foo@example.com", rating=196, count=7))
        id3.save(path)
        assert read_file(path)["tags"]["rating"] == "80"
    assert [
        o.status
        for o in apply(path, [Change(1, "rating", read_file(path)["tags"].get("rating"), "90")])
    ] == ["applied"]
    assert read_file(path)["tags"]["rating"] == "90"
    if fmt == "mp3":
        [frame] = ID3(path).getall("POPM")
        assert (frame.rating, frame.count) == (242, 7)
    elif fmt == "flac":
        assert FLAC(path)["rating"] == ["90"]
    assert [o.status for o in apply(path, [Change(2, "rating", "90", None)])] == ["applied"]
    assert "rating" not in read_file(path)["tags"]


def test_popm_bytes_from_other_players() -> None:
    from plekstender.tags.keys import popm_to_rating

    assert [popm_to_rating(b) for b in (0, 1, 13, 64, 100, 128, 196, 255)] == [
        None,
        2,
        1,
        4,
        6,
        6,
        8,
        10,
    ]


RELEASE_FIELDS = {
    "composer": "Thom Yorke",
    "originaldate": "1997-05-21",
    "releasecountry": "GB",
    "label": "Parlophone",
    "catalognumber": "NODATA 02",
    "barcode": "724385522925",
    "musicbrainz_albumid": "b1392450-e666-3926-a536-22c65f834433",
    "musicbrainz_releasegroupid": "b1392450-e666-3926-a536-22c65f834433",
    "musicbrainz_albumartistid": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
}


@pytest.mark.parametrize("fmt", ["mp3", "flac", "m4a"])
def test_release_fields_in_each_format(tmp_path: Path, fmt: str) -> None:
    """The tag editor's extra fields are written and read back the same way in every format
    (the tags Picard uses)."""
    from mutagen.id3 import ID3

    from plekstender.tags.write import Planned as Change
    from plekstender.tags.write import apply

    path = tmp_path / f"song.{fmt}"
    make_audio(path, fmt)
    changes = [Change(i, f, None, v) for i, (f, v) in enumerate(RELEASE_FIELDS.items())]
    assert {o.status for o in apply(path, changes)} == {"applied"}
    tags = read_file(path)["tags"]
    assert {f: tags.get(f) for f in RELEASE_FIELDS} == RELEASE_FIELDS
    if fmt == "mp3":
        id3 = ID3(path)
        assert str(id3["TPUB"]) == "Parlophone"
        assert str(id3["TXXX:CATALOGNUMBER"]) == "NODATA 02"
    elif fmt == "flac":
        assert FLAC(path)["label"] == ["Parlophone"]


@pytest.mark.db
async def test_writing_your_ratings_to_files(app: FastAPI, plex: FakePlex, music: Path) -> None:
    from plekstender.worker import apply_tag_changes, scan_music_files

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    # Not unless asked for; then only the songs you've rated (Paranoid Android, 5 stars).
    default = (
        await owner.post(f"{base}/plex-sync/preview", json={"scope": "library"}, headers=CSRF)
    ).json()
    assert "rating" not in default["by_field"]
    body = {"scope": "library", "fields": ["rating"]}
    preview = (await owner.post(f"{base}/plex-sync/preview", json=body, headers=CSRF)).json()
    assert preview["samples"] == [
        {
            "path": "300.flac",
            "field": "rating",
            "old_unknown": False,
            "old_value": None,
            "new_value": "100",
        }
    ]
    resp = await owner.post(f"{base}/plex-sync", json=body, headers=CSRF)
    await apply_tag_changes.func(set_id=resp.json()["id"])
    assert FLAC(music / "300.flac")["rating"] == ["100"]
    again = (await owner.post(f"{base}/plex-sync/preview", json=body, headers=CSRF)).json()
    assert again["changes"] == 0


# --- Identifying files by sound --------------------------------------------------------------

ACOUSTID_KEY = "goodkey123"


def acoustid_recording(n: int, title: str) -> dict[str, Any]:
    artists = [{"id": RADIOHEAD_ID, "name": "Radiohead"}]
    return {
        "id": f"10000000-0000-4000-8000-0000000000{n:02d}",
        "title": title,
        "duration": 280,
        "artists": artists,
        "releases": [
            {
                "id": RELEASE_ID,
                "title": "OK Computer",
                "artists": artists,
                "country": "GB",
                "date": {"year": 1997, "month": 5, "day": 21},
                "medium_count": 1,
                "mediums": [
                    {
                        "position": 1,
                        "format": "CD",
                        "track_count": 12,
                        "tracks": [
                            {"id": f"00000000-0000-4000-8000-0000000000{n:02d}", "position": n}
                        ],
                    }
                ],
            }
        ],
    }


class FakeAcoustId:
    """AcoustID: knows demo.mp3 (it's Airbag) and 300.flac (Paranoid Android)."""

    def __init__(self, router: Any) -> None:
        import httpx

        known = {
            "FP-demo.mp3": acoustid_recording(1, "Airbag"),
            "FP-300.flac": acoustid_recording(2, "Paranoid Android"),
        }
        self.lookups: list[dict[str, str]] = []

        def lookup(request: httpx.Request) -> httpx.Response:
            from urllib.parse import parse_qsl

            form = dict(parse_qsl(request.content.decode()))
            if form["client"] != ACOUSTID_KEY:
                return httpx.Response(
                    400,
                    json={"status": "error", "error": {"code": 4, "message": "invalid API key"}},
                )
            if "trackid" in form:
                return httpx.Response(200, json={"status": "ok", "results": []})
            self.lookups.append(form)
            recording = known.get(form["fingerprint"])
            results = (
                [{"id": "acoustid-1", "score": 0.93, "recordings": [recording]}]
                if recording
                else []
            )
            return httpx.Response(200, json={"status": "ok", "results": results})

        router.post("https://api.acoustid.org/v2/lookup").mock(side_effect=lookup)


@pytest.fixture
def fpcalc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for fpcalc: the fingerprint is "FP-" and the file's name."""
    from plekstender import acoustid

    stub = tmp_path / "fpcalc"
    stub.write_text(
        '#!/bin/sh\necho "{\\"duration\\": 61.4, \\"fingerprint\\": \\"FP-$(basename "$4")\\"}"\n'
    )
    stub.chmod(0o755)
    monkeypatch.setattr(get_settings(), "fpcalc_path", str(stub))
    monkeypatch.setattr(acoustid, "INTERVAL", 0.0)
    return stub


@pytest.mark.db
async def test_identifying_files_by_sound(
    app: FastAPI, plex: FakePlex, music: Path, fpcalc: Path, mb: FakeMusicBrainz
) -> None:
    from mutagen.easyid3 import EasyID3

    from plekstender.worker import apply_tag_changes, identify_music_files, scan_music_files

    acoustid = FakeAcoustId(plex.router)
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    # The key first: checked with AcoustID.
    assert (await owner.get(f"{base}/identify")).json()["configured"] is False
    assert (
        await owner.post(f"{base}/identify", json={"kind": "missing"}, headers=CSRF)
    ).status_code == 400
    bad = await owner.put("/api/admin/acoustid", json={"api_key": "wrongkey1"}, headers=CSRF)
    assert bad.status_code == 400 and "application key" in bad.json()["detail"]
    good = await owner.put("/api/admin/acoustid", json={"api_key": ACOUSTID_KEY}, headers=CSRF)
    assert good.json() == {"configured": True, "source": "settings"}

    # The demo has no artist or album; nothing's known about it yet.
    unknown = (await owner.get(f"{base}/unknown", params={"kind": "missing"})).json()
    assert unknown["total"] == 1
    [demo] = unknown["files"]
    assert (demo["path"], demo["status"], demo["in_plex"]) == ("extra/demo.mp3", "new", False)
    assert (await owner.get(f"{base}/unknown", params={"kind": "no_mbid"})).json()["total"] == 3

    job = (await owner.post(f"{base}/identify", json={"kind": "missing"}, headers=CSRF)).json()
    assert job["status"] == "queued"
    await identify_music_files.func(job_id=job["id"])
    status = (await owner.get(f"{base}/identify")).json()
    assert status["last"]["status"] == "succeeded", status
    assert status["last"]["stats"] == {
        "files": 1,
        "identified": 1,
        "no_match": 0,
        "errors": 0,
        "known": 0,
    }
    assert acoustid.lookups[0]["duration"] == "61"  # length and fingerprint only

    [demo] = (await owner.get(f"{base}/unknown", params={"kind": "missing"})).json()["files"]
    assert demo["status"] == "identified"
    assert demo["results"][0]["recordings"][0]["releases"][0]["date"] == "1997-05-21"
    suggestion = demo["suggestion"]
    assert suggestion["track_id"].endswith("01")
    again = await owner.post(f"{base}/identify", json={"kind": "missing"}, headers=CSRF)
    assert again.status_code == 400  # known already (unless asked to look again)

    # Preview, then write (with tag editing on).
    body = {"choices": [suggestion]}
    preview = (await owner.post(f"{base}/identify/preview", json=body, headers=CSRF)).json()
    changes = {c["field"]: c["new_value"] for c in preview["files"][0]["changes"]}
    assert changes["title"] == "Airbag" and changes["album"] == "OK Computer"
    assert changes["tracknumber"] == "1/12" and changes["musicbrainz_albumid"] == RELEASE_ID
    assert "discnumber" not in changes
    wrong = {"choices": [{**suggestion, "track_id": "nope"}]}
    assert (
        await owner.post(f"{base}/identify/preview", json=wrong, headers=CSRF)
    ).status_code == 422
    assert (await owner.post(f"{base}/identify/apply", json=body, headers=CSRF)).status_code == 403
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    resp = await owner.post(f"{base}/identify/apply", json=body, headers=CSRF)
    assert resp.status_code == 201, resp.text
    assert resp.json()["description"] == "Identified by sound: 1 file"
    await apply_tag_changes.func(set_id=resp.json()["id"])
    tags = EasyID3(music / "extra" / "demo.mp3")
    assert tags["title"] == ["Airbag"] and tags["artist"] == ["Radiohead"]
    assert tags["musicbrainz_albumid"] == [RELEASE_ID]
    assert (await owner.get(f"{base}/unknown", params={"kind": "missing"})).json()["total"] == 0

    # An album's files: the releases they sound like are suggested for it.
    job = (await owner.post(f"{base}/identify", json={"album_key": 200}, headers=CSRF)).json()
    await identify_music_files.func(job_id=job["id"])
    suggested = (await owner.get(f"{base}/albums/200/musicbrainz/suggested")).json()
    assert [(r["id"], r["score"], r["formats"]) for r in suggested] == [(RELEASE_ID, 50, "CD")]
    assert (await owner.get(f"{base}/summary")).json()["last"]["id"] == scan["id"]  # scans only


@pytest.mark.db
async def test_tags_an_older_scan_couldnt_read(
    app: FastAPI, plex: FakePlex, music: Path, mb: FakeMusicBrainz
) -> None:
    """Files scanned before Plekstender read release country and release track IDs: the
    preview says so, writing replaces whatever they hold, and undo puts it back."""
    from sqlalchemy import update

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import MediaFile
    from plekstender.worker import apply_tag_changes, scan_music_files

    for name in ("300.flac", "301.flac"):  # tagged from another release before
        tag_flac(music / name, releasecountry="XW", musicbrainz_releasetrackid="old-track")
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])
    async with get_sessionmaker()() as db:  # as an older reader saw them
        for media in (await db.scalars(select_files())).all():
            tags = {
                k: v
                for k, v in media.tags.items()
                if k not in ("releasecountry", "musicbrainz_releasetrackid")
            }
            await db.execute(
                update(MediaFile).where(MediaFile.id == media.id).values(tags=tags, reader=1)
            )
        await db.commit()

    body = {"release_id": RELEASE_ID, "fields": ["releasecountry", "musicbrainz_releasetrackid"]}
    preview = (
        await owner.post(f"{base}/albums/200/musicbrainz/preview", json=body, headers=CSRF)
    ).json()
    changes = [c for f in preview["files"] for c in f["changes"]]
    assert len(changes) == 4 and all(c["old_unknown"] and c["old_value"] is None for c in changes)

    resp = await owner.post(f"{base}/albums/200/musicbrainz", json=body, headers=CSRF)
    await apply_tag_changes.func(set_id=resp.json()["id"])
    [done] = (await owner.get(f"{base}/changes")).json()
    assert (done["status"], done["counts"]) == ("applied", {"applied": 4})
    assert {c["old_value"] for c in done["changes"]} == {"XW", "old-track"}  # what was there
    assert FLAC(music / "301.flac")["releasecountry"] == ["GB"]

    undo = await owner.post(f"{base}/undo", json={"set_id": done["id"]}, headers=CSRF)
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert FLAC(music / "301.flac")["releasecountry"] == ["XW"]
    assert FLAC(music / "301.flac")["musicbrainz_releasetrackid"] == ["old-track"]


def select_files() -> Any:
    from sqlalchemy import select

    from plekstender.db.models import MediaFile

    return select(MediaFile)


def test_a_changed_file_is_left_alone_with_a_clear_reason(tmp_path: Path) -> None:
    from plekstender.tags.write import Planned as Change
    from plekstender.tags.write import apply

    path = tmp_path / "a.flac"
    make_audio(path)
    tag_flac(path, releasecountry="XW")
    [outcome] = apply(path, [Change(1, "releasecountry", None, "GB")])
    assert outcome.status == "conflict"
    assert (
        outcome.error
        == "Left as it was: the file had 'XW', not no value (changed since it was scanned)."
    )


# --- Renaming and moving files ---------------------------------------------------------------


# Plex's own layout: the rename template the app suggests (frontend/src/api/files.ts).
DEFAULT_TEMPLATE = "{albumartist}/{album}/[{disc}-][{track:02} - ]{title}"


def test_rename_templates() -> None:
    from plekstender.tags.rename import (
        TemplateError,
        check_template,
        render,
        values_for,
    )

    tags = {
        "title": "Paranoid Android",
        "artist": "Radiohead",
        "album": "OK Computer",
        "track": 2,
        "disc": 1,
        "date": "1997-05-21",
    }
    assert (
        render(DEFAULT_TEMPLATE, values_for(tags, "x.flac", discs=1), ".FLAC")
        == "Radiohead/OK Computer/02 - Paranoid Android.flac"
    )
    assert (
        render(DEFAULT_TEMPLATE, values_for({**tags, "disc": 2}, "x.flac", discs=2), ".flac")
        == "Radiohead/OK Computer/2-02 - Paranoid Android.flac"
    )
    # Optional parts drop out; unsafe characters and trailing dots don't make it into names.
    odd = {"title": "What?", "albumartist": "AC/DC", "album": "Vol. 1...", "date": "1980"}
    assert (
        render(
            "{albumartist}/[{year} - ]{album}/[{track:02} ]{title}",
            values_for(odd, "x.mp3", 1),
            ".mp3",
        )
        == "AC_DC/1980 - Vol. 1/What_.mp3"
    )
    assert (
        render(DEFAULT_TEMPLATE, values_for({}, "Some file.mp3", 1), ".mp3")
        == "Unknown artist/Unknown album/Some file.mp3"
    )
    long = render("{title}", {"title": "x" * 400}, ".flac")
    assert len(long.encode()) <= 180 and long.endswith(".flac")

    for bad, message in [
        ("{albumartist}/{nope}/{title}", "isn't a field"),
        ("{albumartist}/[{year} - {album}/{title}", "Brackets"),
        ("{title}/{album}", "file name"),
        ("../{title}", ".. folders"),
        ("{album}/{track:2} {title}", "{track:02}"),
    ]:
        with pytest.raises(TemplateError, match=re.escape(message)):
            check_template(bad)


def test_planning_moves_with_their_files(tmp_path: Path) -> None:
    from plekstender.tags.rename import Move, plan_moves

    root = tmp_path
    album = root / "incoming" / "okc"
    album.mkdir(parents=True)
    for name in ("01.flac", "02.flac", "01.lrc", "01.en.lrc", "cover.jpg", "album.cue"):
        (album / name).write_bytes(b"x")
    (album / "Scans").mkdir()
    (album / "Scans" / "front.png").write_bytes(b"x")
    mixed = root / "mixed"
    mixed.mkdir()
    for name in ("a.flac", "b.flac", "folder.jpg"):
        (mixed / name).write_bytes(b"x")
    (root / "Taken").mkdir()
    (root / "Taken" / "a.flac").write_bytes(b"x")

    songs = [
        Move("song", "incoming/okc/01.flac", "Radiohead/OK Computer/01 - Airbag.flac", 1),
        Move("song", "incoming/okc/02.flac", "Radiohead/OK Computer/02 - Paranoid Android.flac", 2),
        Move("song", "mixed/a.flac", "Taken/a.flac", 3),  # something's there
        Move("song", "mixed/b.flac", "Elsewhere/b.flac", 4),
    ]
    moves, skipped, unchanged = plan_moves(root, songs)
    targets = {m.source: (m.kind, m.target) for m in moves}
    assert targets["incoming/okc/01.lrc"] == ("sidecar", "Radiohead/OK Computer/01 - Airbag.lrc")
    assert targets["incoming/okc/01.en.lrc"] == (
        "sidecar",
        "Radiohead/OK Computer/01 - Airbag.en.lrc",
    )
    assert targets["incoming/okc/cover.jpg"] == ("folder", "Radiohead/OK Computer/cover.jpg")
    assert targets["incoming/okc/Scans"] == ("folder", "Radiohead/OK Computer/Scans")
    assert targets["mixed/b.flac"] == ("song", "Elsewhere/b.flac")
    reasons = {s.path: s.reason for s in skipped}
    assert reasons["mixed/a.flac"] == "Another file is already there."
    assert reasons["mixed/folder.jpg"] == "Its folder's songs go to different places."
    assert unchanged == 0


@pytest.mark.db
async def test_renaming_files_and_moving_them_back(
    app: FastAPI, plex: FakePlex, music: Path
) -> None:
    from plekstender.worker import apply_tag_changes, plan_renames, scan_music_files

    (music / "300.lrc").write_text("[00:01] lyrics")
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    bad = await owner.post(
        f"{base}/renames", json={"scope": "album", "key": 200, "template": "{nope}"}, headers=CSRF
    )
    assert bad.status_code == 422 and "isn't a field" in bad.json()["detail"]
    template = "{albumartist}/{album}/[{track:02} - ]{title}"
    job = (
        await owner.post(
            f"{base}/renames",
            json={"scope": "album", "key": 200, "template": template},
            headers=CSRF,
        )
    ).json()
    await plan_renames.func(job_id=job["id"])
    plan = (await owner.get(f"{base}/renames/{job['id']}")).json()
    assert plan["job"]["status"] == "succeeded", plan
    assert {(m["kind"], m["source"], m["target"]) for m in plan["moves"]} == {
        ("song", "300.flac", "Radiohead/OK Computer/02 - Paranoid Android.flac"),
        ("sidecar", "300.lrc", "Radiohead/OK Computer/02 - Paranoid Android.lrc"),
        ("song", "301.flac", "Radiohead/OK Computer/06 - Karma Police (Live).flac"),
    }
    assert plan["skipped"] == [] and plan["unchanged"] == 0
    assert (music / "300.flac").exists()  # a dry run moves nothing

    apply = f"{base}/renames/{job['id']}/apply"
    assert (await owner.post(apply, headers=CSRF)).status_code == 403  # editing is off
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    resp = await owner.post(apply, headers=CSRF)
    assert resp.status_code == 201, resp.text
    change_set = resp.json()
    assert change_set["description"] == f"Moved 2 songs and 1 other file to {template}"
    assert (await owner.post(apply, headers=CSRF)).status_code == 409  # not twice
    plex.library.refreshed.clear()
    await apply_tag_changes.func(set_id=change_set["id"])
    album = music / "Radiohead" / "OK Computer"
    assert sorted(p.name for p in album.iterdir()) == [
        "02 - Paranoid Android.flac", "02 - Paranoid Android.lrc", "06 - Karma Police (Live).flac",
    ]  # fmt: skip
    assert not (music / "300.flac").exists()
    assert plex.library.refreshed and all(
        path == "" for _, path in plex.library.refreshed
    )  # whole sections
    files = (await owner.get(f"{base}/albums/200")).json()["tracks"]
    assert {t["file"] for t in files if t["file"]} == {
        "Radiohead/OK Computer/02 - Paranoid Android.flac",
        "Radiohead/OK Computer/06 - Karma Police (Live).flac",
    }
    [done] = (await owner.get(f"{base}/changes")).json()
    assert (done["status"], done["counts"]) == ("applied", {"applied": 3})

    # Undo moves them back, and the emptied folders go.
    undo = await owner.post(f"{base}/undo", json={"set_id": done["id"]}, headers=CSRF)
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert (music / "300.flac").exists() and (music / "300.lrc").exists()
    assert not (music / "Radiohead").exists()

    # A file in the way is never overwritten: that move is left out.
    job = (
        await owner.post(
            f"{base}/renames",
            json={"scope": "album", "key": 200, "template": template},
            headers=CSRF,
        )
    ).json()
    await plan_renames.func(job_id=job["id"])
    album.mkdir(parents=True)
    (album / "06 - Karma Police (Live).flac").write_bytes(b"someone else's")
    resp = await owner.post(f"{base}/renames/{job['id']}/apply", headers=CSRF)
    await apply_tag_changes.func(set_id=resp.json()["id"])
    latest = (await owner.get(f"{base}/changes")).json()[0]
    assert latest["counts"] == {"applied": 2, "conflict": 1}
    assert (album / "06 - Karma Police (Live).flac").read_bytes() == b"someone else's"
    assert (music / "301.flac").exists()


# --- .nfo files ----------------------------------------------------------------------------------

ALBUM_NFO = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<album>
    <title>OK Computer</title>
    <musicbrainzalbumid>{RELEASE_ID}</musicbrainzalbumid>
    <musicbrainzreleasegroupid>https://musicbrainz.org/release-group/{GROUP_ID}</musicbrainzreleasegroupid>
    <artistdesc>Radiohead</artistdesc>
    <genre>Rock / Alternative</genre>
    <style>Art Rock</style>
    <mood>Paranoid</mood>
    <mood>Bleak</mood>
    <review>A landmark.</review>
    <label>Parlophone</label>
    <releasedate>1997-05-21</releasedate>
    <albumArtistCredits><artist>Radiohead</artist><musicBrainzArtistID>{RADIOHEAD_ID}</musicBrainzArtistID></albumArtistCredits>
</album>
"""
ARTIST_NFO = f"""<?xml version="1.0" encoding="UTF-8"?>
<artist>
    <name>Radiohead</name>
    <musicBrainzArtistID>{RADIOHEAD_ID}</musicBrainzArtistID>
    <genre>Alternative Rock</genre>
    <formed>1985</formed>
    <biography>Radiohead are an English rock band formed in Abingdon.</biography>
</artist>
"""


def test_reading_nfo_files(tmp_path: Path) -> None:
    from plekstender.tags.nfo import NfoError, parse

    (tmp_path / "album.nfo").write_text(ALBUM_NFO)
    kind, album = parse(tmp_path / "album.nfo")
    assert kind == "album"
    assert album["musicbrainz_albumid"] == RELEASE_ID
    assert album["musicbrainz_releasegroupid"] == GROUP_ID  # from a link
    assert album["musicbrainz_albumartistids"] == [RADIOHEAD_ID]
    assert album["genres"] == ["Rock", "Alternative"] and album["moods"] == ["Paranoid", "Bleak"]
    assert (album["label"], album["release_date"], album["review"]) == (
        "Parlophone",
        "1997-05-21",
        "A landmark.",
    )

    (tmp_path / "artist.nfo").write_text(ARTIST_NFO)
    kind, artist = parse(tmp_path / "artist.nfo")
    assert (kind, artist["name"], artist["born"]) == ("artist", "Radiohead", "1985")
    assert artist["musicbrainz_artistid"] == RADIOHEAD_ID

    (tmp_path / "scene.nfo").write_text("Ripped by ███ in 1999\n  greetz")
    with pytest.raises(NfoError, match="not XML"):
        parse(tmp_path / "scene.nfo")
    (tmp_path / "bomb.nfo").write_text(
        '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY a "aaaa">]><album>&a;</album>'
    )
    with pytest.raises(NfoError, match="entities"):
        parse(tmp_path / "bomb.nfo")
    # The same in UTF-16, which a search of the raw bytes for "<!ENTITY" wouldn't find.
    wide = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE a [<!ENTITY a "a">]><album>&a;</album>'
    (tmp_path / "wide.nfo").write_bytes(wide.encode("utf-16"))
    with pytest.raises(NfoError, match="entities"):
        parse(tmp_path / "wide.nfo")


@pytest.mark.db
async def test_nfo_files_on_album_and_artist_pages(
    app: FastAPI, plex: FakePlex, music: Path
) -> None:
    from sqlalchemy import update

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import Track
    from plekstender.worker import scan_music_files

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    # The usual layout: Artist/Album/files, artist.nfo and album.nfo in their folders.
    album = music / "Radiohead" / "OK Computer"
    album.mkdir(parents=True)
    for key in (300, 301):
        (music / f"{key}.flac").rename(album / f"{key}.flac")
    async with get_sessionmaker()() as db:
        for key in (300, 301):
            await db.execute(
                update(Track)
                .where(Track.rating_key == key)
                .values(file_path=f"/m/Radiohead/OK Computer/{key}.flac")
            )
        await db.commit()
    (album / "album.nfo").write_text(ALBUM_NFO)
    (music / "Radiohead" / "artist.nfo").write_text(ARTIST_NFO)
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])
    assert (await owner.get(f"{base}/summary")).json()["last"]["stats"]["nfo"] == 2

    nfo = (await owner.get(f"{base}/albums/200/nfo")).json()
    assert (nfo["path"], nfo["kind"], nfo["error"]) == (
        "Radiohead/OK Computer/album.nfo",
        "album",
        None,
    )
    assert nfo["data"]["musicbrainz_albumid"] == RELEASE_ID
    assert nfo["file_release_ids"] == []  # the files don't say which release yet
    artist = (await owner.get(f"{base}/artists/100/nfo")).json()
    assert (artist["path"], artist["data"]["born"]) == ("Radiohead/artist.nfo", "1985")
    assert artist["plex_mbid"]  # Plex's, to compare with the .nfo's
    assert (await owner.get(f"{base}/albums/201/nfo")).json() is None  # no files, no .nfo

    # Gone from the folder: forgotten at the next scan.
    (album / "album.nfo").unlink()
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])
    assert (await owner.get(f"{base}/albums/200/nfo")).json() is None


# --- Artwork in the files --------------------------------------------------------------------


def picture_bytes(width: int, height: int, fmt: str = "PNG", colour: str = "red") -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["flac", "mp3", "m4a"])
def test_embedded_covers_in_each_format(tmp_path: Path, fmt: str) -> None:
    from mutagen.flac import Picture

    from plekstender.tags.pictures import embed, embedded, info, prepare

    path = tmp_path / f"song.{fmt}"
    make_audio(path, fmt)
    assert embedded(path) is None
    if fmt == "flac":  # a back cover stays when the front one changes
        audio = FLAC(path)
        back = Picture()
        back.type, back.mime, back.data = 4, "image/png", picture_bytes(10, 10)
        audio.add_picture(back)
        audio.save()
    big = picture_bytes(2400, 1800)
    small = prepare(big)
    described = info(small)
    assert (described.mime, described.width, described.height) == ("image/jpeg", 1000, 750)
    import io

    from PIL import Image

    assert not Image.open(io.BytesIO(small)).info.get("progressive")  # players need baseline
    embed(path, small)
    assert embedded(path) == (small, "image/jpeg")
    if fmt == "flac":
        assert [p.type for p in FLAC(path).pictures] == [4, 3]
    embed(path, None)
    # No front cover left: a FLAC's back cover is what players show now.
    assert embedded(path) == ((back.data, "image/png") if fmt == "flac" else None)


@pytest.mark.db
async def test_album_artwork_in_files_and_folders(
    app: FastAPI, plex: FakePlex, music: Path
) -> None:
    from plekstender.tags.pictures import embed, embedded, sha1
    from plekstender.worker import apply_tag_changes, scan_music_files

    huge = picture_bytes(3000, 3000, "JPEG", "blue")
    embed(music / "300.flac", huge, "image/jpeg")  # 301 has none
    folder_cover = picture_bytes(1200, 1200, "JPEG", "green")
    (music / "cover.jpg").write_bytes(folder_cover)
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    art = (await owner.get(f"{base}/albums/200/artwork")).json()
    assert (art["songs"], art["without_cover"], art["folder"], art["plex_poster"]) == (
        2,
        1,
        ".",
        True,
    )
    [cover] = art["covers"]
    assert (cover["sha1"], cover["width"], cover["files"], cover["oversized"]) == (
        sha1(huge),
        3000,
        1,
        True,
    )
    [image] = art["folder_images"]
    assert (image["path"], image["kind"], image["width"]) == ("cover.jpg", "cover", 1200)
    thumb = await owner.get(f"{base}/pictures/{cover['sha1']}.jpg")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    summary = (await owner.get(f"{base}/artwork/summary")).json()
    assert summary["oversized"] == 1 and summary["without_cover"] >= 1

    # The folder's cover into every song, player-sized; undo puts back what was there.
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    body = {"source": "folder", "path": "cover.jpg", "max_px": 1000}
    resp = await owner.post(f"{base}/albums/200/artwork/embed", json=body, headers=CSRF)
    assert resp.status_code == 201, resp.text
    assert resp.json()["description"] == "Embedded cover.jpg in 2 songs (1000 px)"
    await apply_tag_changes.func(set_id=resp.json()["id"])
    for name in ("300.flac", "301.flac"):
        data, mime = embedded(music / name) or (b"", "")
        assert mime == "image/jpeg" and data != huge
    [done] = (await owner.get(f"{base}/changes")).json()
    assert done["counts"] == {"applied": 2}
    undo = await owner.post(f"{base}/undo", json={"set_id": done["id"]}, headers=CSRF)
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert embedded(music / "300.flac") == (huge, "image/jpeg")
    assert embedded(music / "301.flac") is None

    # Plex's poster as folder.jpg; undo removes the file it made.
    resp = await owner.post(
        f"{base}/albums/200/artwork/folder",
        json={"source": "plex", "name": "folder.jpg"},
        headers=CSRF,
    )
    assert resp.json()["description"] == "Saved Plex's poster as folder.jpg"
    await apply_tag_changes.func(set_id=resp.json()["id"])
    assert (music / "folder.jpg").read_bytes()[:2] == b"\xff\xd8"
    names = [
        i["path"] for i in (await owner.get(f"{base}/albums/200/artwork")).json()["folder_images"]
    ]
    assert names == ["cover.jpg", "folder.jpg"]
    latest = (await owner.get(f"{base}/changes")).json()[0]
    undo = await owner.post(f"{base}/undo", json={"set_id": latest["id"]}, headers=CSRF)
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert not (music / "folder.jpg").exists()

    # Oversized covers, library-wide.
    resp = await owner.post(f"{base}/artwork/shrink", json={"max_px": 800}, headers=CSRF)
    assert resp.json()["description"] == "Shrank 1 embedded cover to 800 px"
    await apply_tag_changes.func(set_id=resp.json()["id"])
    from plekstender.tags.pictures import info

    assert info(embedded(music / "300.flac")[0]).width == 800  # type: ignore[index]
    assert (await owner.post(f"{base}/artwork/shrink", json={}, headers=CSRF)).status_code == 400
