# ruff: noqa: S311, E501 - seeded test data; expected moves one per line
"""Duplicate files: candidates by name and length, confirmed by comparing fingerprints, and
the extra copies moved out of the library (and back by undo)."""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from mutagen.easyid3 import EasyID3
from sqlalchemy import delete

from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import FileFingerprint, MediaFile
from plekstender.tags import duplicates as dupes
from tests.fakes import CSRF, FakePlex
from tests.test_files import OK_COMPUTER, make_audio, music, tag_flac  # noqa: F401
from tests.test_sync import owner_with_library

rng = random.Random(7)
SONG = [rng.getrandbits(32) for _ in range(300)]
OTHER = [rng.getrandbits(32) for _ in range(300)]


def noisy(values: list[int], flips: int) -> list[int]:
    """A re-encode: a few bits differ here and there."""
    r = random.Random(flips)
    return [v ^ (1 << r.randrange(32)) if r.random() < flips / 100 else v for v in values]


def test_similarity() -> None:
    assert dupes.similarity(SONG, SONG) == 1.0
    assert dupes.similarity(SONG, SONG[5:]) == 1.0  # starts a little later
    assert dupes.similarity(SONG, noisy(SONG, 30)) > 0.98
    assert dupes.similarity(SONG, OTHER) < 0.6
    assert dupes.similarity(SONG[:10], SONG[:10]) == 0.0  # too short to tell


def media(n: int, path: str, fmt: str, size: int, **tags: Any) -> MediaFile:
    return MediaFile(
        id=n, server_id=1, path=path, size=size, mtime=1.0, format=fmt, duration_ms=200_000,
        tags=tags, rating_key=None,
    )  # fmt: skip


def test_candidates_and_the_copy_to_keep() -> None:
    a = media(1, "a/Song.mp3", "mp3", 4_000_000, artist="The Band", title="Song")
    b = media(
        2, "b/Song (Remastered).flac", "flac", 30_000_000, artist="Band", title="Song (Remastered)"
    )
    c = media(3, "c/x.m4a", "m4a", 6_000_000, title="Whatever", musicbrainz_trackid="rec-1")
    d = media(
        4, "d/y.mp3", "mp3", 5_000_000, artist="Other", title="Else", musicbrainz_trackid="rec-1"
    )
    e = media(5, "e/Song.mp3", "mp3", 4_000_000, artist="The Band", title="Song")
    e.duration_ms = 400_000  # a long live version: not the same
    lone = media(6, "f/Alone.mp3", "mp3", 1, artist="X", title="Alone")
    groups = dupes.candidates([a, b, c, d, e, lone])
    assert sorted(sorted(f.id for f in g) for g in groups) == [[1, 2], [3, 4]]
    assert dupes.best([a, b]).id == 2  # lossless
    assert dupes.best([c, d]).id == 3  # higher bitrate
    assert dupes.kbps(a) == 160


def test_moves_keep_a_copy() -> None:
    groups = [
        {"files": [
            {"file_id": 1, "path": "a.flac", "rating_key": 300, "sidecars": []},
            {"file_id": 2, "path": "b/a.mp3", "rating_key": None, "sidecars": ["b/a.lrc"]},
        ]}
    ]  # fmt: skip
    assert dupes.moves_for(groups, {2}) == [
        {"file_id": 2, "rating_key": None, "source": "b/a.mp3", "target": ".plekstender-duplicates/b/a.mp3"},
        {"file_id": None, "rating_key": None, "source": "b/a.lrc", "target": ".plekstender-duplicates/b/a.lrc"},
    ]  # fmt: skip
    with pytest.raises(dupes.DuplicatesError, match="Keep at least one"):
        dupes.moves_for(groups, {1, 2})
    with pytest.raises(dupes.DuplicatesError):
        dupes.moves_for(groups, {9})


@pytest.fixture
def raw_fpcalc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for fpcalc -raw: Paranoid Android sounds the same in every copy except the
    one in other/, which is something else."""
    prints = tmp_path / "prints"
    prints.mkdir()
    (prints / "song.json").write_text(json.dumps({"duration": 1, "fingerprint": SONG}))
    (prints / "copy.json").write_text(json.dumps({"duration": 1, "fingerprint": noisy(SONG, 20)}))
    (prints / "other.json").write_text(json.dumps({"duration": 1, "fingerprint": OTHER}))
    stub = tmp_path / "fpcalc"
    stub.write_text(
        "#!/bin/sh\n"
        "for last; do :; done\n"
        'case "$last" in\n'
        f'  */other/*) cat "{prints}/other.json" ;;\n'
        f'  */copy/*) cat "{prints}/copy.json" ;;\n'
        f'  *) cat "{prints}/song.json" ;;\n'
        "esac\n"
    )
    stub.chmod(0o755)
    monkeypatch.setattr(get_settings(), "fpcalc_path", str(stub))
    return stub


@pytest.mark.db
async def test_finding_and_removing_duplicates(
    app: FastAPI,
    plex: FakePlex,
    music: Path,  # noqa: F811
    raw_fpcalc: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plekstender.worker import apply_tag_changes, find_duplicates, scan_music_files

    # A second copy of Paranoid Android (an MP3, with lyrics), and a different song that
    # happens to share its name.
    make_audio(music / "copy" / "Paranoid Android.mp3", "mp3")
    tags = EasyID3()
    tags.update({"title": "Paranoid Android", "artist": "Radiohead"})
    tags.save(music / "copy" / "Paranoid Android.mp3")
    (music / "copy" / "Paranoid Android.lrc").write_text("[00:01] lyrics\n")
    shutil.copy(music / "300.flac", music / "other.flac")
    (music / "other").mkdir()
    shutil.move(music / "other.flac", music / "other" / "Paranoid Android.flac")

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/files"
    assert (await owner.post(f"{base}/duplicates", headers=CSRF)).status_code == 409
    assert (await owner.get(f"{base}/duplicates/latest")).json() is None
    await owner.put(
        f"/api/servers/{sid}/artwork/folder",
        json={"enabled": False, "plex_prefix": "/m", "local_prefix": str(music)},
        headers=CSRF,
    )
    scan = (await owner.post(f"{base}/scans", headers=CSRF)).json()
    await scan_music_files.func(scan_id=scan["id"])

    job = (await owner.post(f"{base}/duplicates", headers=CSRF)).json()
    await find_duplicates.func(job_id=job["id"])
    found = (await owner.get(f"{base}/duplicates/latest")).json()
    assert found["job"]["status"] == "succeeded", found
    assert (found["total_groups"], found["fingerprint_error"]) == (1, None)
    [group] = found["groups"]
    assert group["confirmed"] and group["similarity"] > 0.9
    assert [(f["path"], f["keep"], f["sidecars"]) for f in group["files"]] == [
        ("300.flac", True, []),  # lossless, and Plex's
        ("copy/Paranoid Android.mp3", False, ["copy/Paranoid Android.lrc"]),
    ]
    mp3 = group["files"][1]["file_id"]

    apply = f"{base}/duplicates/{job['id']}/apply"
    assert (await owner.post(apply, json={"remove": [mp3]}, headers=CSRF)).status_code == 403
    await owner.put(f"{base}/settings", json={"tag_editing": True}, headers=CSRF)
    every = [f["file_id"] for f in group["files"]]
    keep_none = await owner.post(apply, json={"remove": every}, headers=CSRF)
    assert keep_none.status_code == 422 and "Keep at least one" in keep_none.json()["detail"]
    resp = await owner.post(apply, json={"remove": [mp3]}, headers=CSRF)
    assert resp.status_code == 201, resp.text
    edit = resp.json()
    assert edit["description"] == (
        "Moved 1 duplicate file out of the library (into .plekstender-duplicates)"
    )
    assert (await owner.post(apply, json={"remove": [mp3]}, headers=CSRF)).status_code == 409
    await apply_tag_changes.func(set_id=edit["id"])
    trash = music / ".plekstender-duplicates" / "copy"
    assert (trash / "Paranoid Android.mp3").exists() and (trash / "Paranoid Android.lrc").exists()
    assert not (music / "copy").exists()  # left empty, so removed

    undo = await owner.post(f"{base}/undo", json={"set_id": edit["id"]}, headers=CSRF)
    await apply_tag_changes.func(set_id=undo.json()["id"])
    assert (music / "copy" / "Paranoid Android.mp3").exists()
    assert (music / "copy" / "Paranoid Android.lrc").exists()

    # Without fpcalc: the candidates, marked as not checked by sound.
    monkeypatch.setattr(get_settings(), "fpcalc_path", str(raw_fpcalc.parent / "missing"))
    async with get_sessionmaker()() as db:
        await db.execute(delete(FileFingerprint))
        await db.commit()
    again = (await owner.post(f"{base}/duplicates", headers=CSRF)).json()
    await find_duplicates.func(job_id=again["id"])
    unchecked = (await owner.get(f"{base}/duplicates/{again['id']}")).json()
    assert "fpcalc can't be run" in unchecked["fingerprint_error"]
    [group] = unchecked["groups"]
    assert (group["confirmed"], len(group["files"])) == (False, 3)
