"""Release covers kept on disk: saved for recent and upcoming releases, served from there,
and deleted once a release is over 90 days old."""

from __future__ import annotations

import datetime as dt
import io

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from plekstender import release_covers
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import ReleaseGroup
from tests.fakes import FakePlex
from tests.test_sync import owner_with_library

RECENT = "00000011-0000-4000-8000-000000000000"  # out 10 days ago
COMING = "00000012-0000-4000-8000-000000000000"  # out next month
OLD = "00000013-0000-4000-8000-000000000000"  # out 200 days ago
BARE = "00000014-0000-4000-8000-000000000000"  # out last week, no cover anywhere
CAA = "https://coverartarchive.org/release-group"


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (600, 600), "navy").save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def archive(plex: FakePlex) -> list[str]:
    """The Cover Art Archive: covers for every release group but BARE; what was asked."""
    asked: list[str] = []

    def front(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if BARE in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=jpeg())

    plex.router.get(url__startswith=CAA).mock(side_effect=front)
    for path in release_covers.folder(get_settings()).glob("*"):
        path.unlink()
    return asked


@pytest.mark.db
async def test_release_covers_kept_on_disk(
    app: FastAPI, plex: FakePlex, archive: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_covers, "PAUSE", 0)
    owner, _ = await owner_with_library(app, plex)
    today = dt.date.today()
    async with get_sessionmaker()() as db:
        for mbid, days in ((RECENT, -10), (COMING, 30), (OLD, -200), (BARE, -7)):
            db.add(
                ReleaseGroup(
                    artist_mbid="a74b1b7f-71a5-4011-9441-d0b5e4122711", mbid=mbid, title=mbid,
                    title_norm=mbid, kind="album", released=today + dt.timedelta(days=days),
                )
            )  # fmt: skip
        await db.commit()

        # After a releases refresh: the covers of the recent and upcoming ones, both sizes.
        async with httpx.AsyncClient() as http:
            assert await release_covers.save_recent(db, http, get_settings()) == 3
        saved = {p.name for p in release_covers.folder(get_settings()).glob("*")}
        assert saved == {
            f"{RECENT}-250.img", f"{RECENT}-500.img", f"{COMING}-250.img", f"{COMING}-500.img",
            f"{BARE}.none",
        }  # fmt: skip
        assert not any(OLD in path for path in archive)
        # Nothing left to fetch (BARE is tried again tomorrow).
        async with httpx.AsyncClient() as http:
            assert await release_covers.save_recent(db, http, get_settings()) == 0

    # The page gets them from disk: no more asking the archive.
    asked = len(archive)
    resp = await owner.get(f"/api/releases/{RECENT}/cover", params={"size": 500})
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    assert (await owner.get(f"/api/releases/{BARE}/cover")).status_code == 404
    assert len(archive) == asked
    # One it doesn't keep is fetched (and kept until the next clean-up).
    assert (await owner.get(f"/api/releases/{OLD}/cover")).status_code == 200
    assert len(archive) == asked + 1

    # The daily clean-up: covers of releases out over 90 days ago go (100 days on, RECENT too).
    async with get_sessionmaker()() as db:
        assert await release_covers.prune(db, get_settings()) == 1  # OLD
        later = today + dt.timedelta(days=100)
        assert await release_covers.prune(db, get_settings(), today=later) == 3  # RECENT, BARE
    left = {p.name for p in release_covers.folder(get_settings()).glob("*")}
    assert left == {f"{COMING}-250.img", f"{COMING}-500.img"}
