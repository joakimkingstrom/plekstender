"""Video covers in album folders (cover.mp4...): found by the scan (not as songs), copied
where the web app can stream them, and offered on album pages and cards."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender.db import get_sessionmaker
from plekstender.db.models import FolderVideo, MediaFile, PlexServer, User, UserServer
from plekstender.tags.read import is_video_cover
from plekstender.tags.scan import scan_videos, video_folder, walk_all
from tests.fakes import FakePlex
from tests.test_sync import owner_with_library


def test_video_covers_by_name(tmp_path: Path) -> None:
    assert is_video_cover("cover.mp4") and is_video_cover("Folder.WEBM")
    assert is_video_cover("animated.mov") and not is_video_cover("cover.jpg")
    assert not is_video_cover("01 Song.mp4")  # a song in an MP4
    album = tmp_path / "Radiohead" / "OK Computer"
    album.mkdir(parents=True)
    for name in ("01 Airbag.flac", "02 Paranoid Android.mp4", "cover.mp4", "cover.jpg"):
        (album / name).write_bytes(b"x")
    walked = walk_all(tmp_path)
    assert [f.path for f in walked.videos] == ["Radiohead/OK Computer/cover.mp4"]
    assert [f.path for f in walked.audio] == [
        "Radiohead/OK Computer/01 Airbag.flac",
        "Radiohead/OK Computer/02 Paranoid Android.mp4",
    ]


@pytest.mark.db
async def test_video_covers(
    app: FastAPI, plex: FakePlex, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plekstender.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "data")
    owner, sid = await owner_with_library(app, plex)
    root = tmp_path / "music"
    ok = root / "Radiohead" / "OK Computer"
    (ok / "CD1").mkdir(parents=True)
    (ok / "cover.mp4").write_bytes(b"0123456789" * 10)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        us = await db.scalar(select(UserServer).where(UserServer.user_id == user_id))
        assert us is not None
        server = await db.get(PlexServer, us.server_id)
        assert server is not None
        # OK Computer's songs in a disc folder under the video; OKNOTOK's elsewhere.
        for key, path in (
            (300, "Radiohead/OK Computer/CD1/300.flac"),
            (302, "Radiohead/OKNOTOK/302.flac"),
        ):
            db.add(
                MediaFile(
                    server_id=server.id, path=path, size=1, mtime=1.0, tags={}, rating_key=key
                )
            )
        await db.commit()
        assert await scan_videos(db, server, root, walk_all(root).videos) == 1
        [video] = await db.scalars(select(FolderVideo))
        copy = video_folder(tmp_path / "data", server.id) / (video.stored or "")
        assert copy.read_bytes() == b"0123456789" * 10

    artist = (await owner.get(f"/api/servers/{sid}/library/artists/100")).json()
    videos = {a["rating_key"]: a["video"] for a in artist["albums"]}
    assert videos == {200: f"/api/servers/{sid}/library/albums/200/video", 201: None}
    whole = await owner.get(f"/api/servers/{sid}/library/albums/200/video")
    assert (whole.status_code, whole.headers["content-type"]) == (200, "video/mp4")
    part = await owner.get(
        f"/api/servers/{sid}/library/albums/200/video", headers={"Range": "bytes=0-3"}
    )
    assert (part.status_code, part.content) == (206, b"0123")
    assert (await owner.get(f"/api/servers/{sid}/library/albums/201/video")).status_code == 404

    # Gone from the folder: forgotten, and its copy deleted.
    (ok / "cover.mp4").unlink()
    async with get_sessionmaker()() as db:
        server = await db.get(PlexServer, server.id)
        assert server is not None
        await scan_videos(db, server, root, walk_all(root).videos)
        assert list(await db.scalars(select(FolderVideo))) == []
    assert not copy.exists()
    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert album["video"] is None
