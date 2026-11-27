"""Settings → New music: logos, covers and lyrics for artists and albums new in Plex, started
after a sync."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender import new_music
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.instance import set_fanart_api_key
from plekstender.db.models import Album, Artist, ArtworkJob, FileScan, Notification, PlexServer
from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_new_music_gets_logos_covers_and_lyrics(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    url = f"/api/servers/{sid}/new-music"
    off = (await owner.get(url)).json()
    assert (off["logos"], off["covers"], off["lyrics"], off["since"]) == (False, False, False, None)
    assert (off["fanart_ready"], off["folder_ready"], off["tag_editing"]) == (False, False, False)

    async with get_sessionmaker()() as db:
        await set_fanart_api_key(db, get_settings(), "f" * 32)
        await db.execute(update(PlexServer).values(local_path_prefix="/music", tag_editing=True))
        await db.commit()
    body = {
        "logos": True, "covers": True, "lyrics": True,
        "lyrics_write": {"sidecar": True, "embedded": False},
    }  # fmt: skip
    on = (await owner.put(url, json=body, headers=CSRF)).json()
    assert on["since"] is not None and on["fanart_ready"] and on["folder_ready"]
    since = dt.datetime.fromisoformat(on["since"])

    async with get_sessionmaker()() as db:
        server_id = await db.scalar(select(PlexServer.id))
        assert server_id is not None
        # What was in the library already isn't new.
        assert await new_music.act(db, get_settings(), server_id) == {}
        # Plex gains an album (and its artist).
        later = since + dt.timedelta(minutes=5)
        await db.execute(update(Album).where(Album.rating_key == 200).values(added_at=later))
        await db.execute(update(Artist).where(Artist.rating_key == 100).values(added_at=later))
        await db.commit()

        done = await new_music.act(db, get_settings(), server_id)
        assert done == {"artists": 1, "albums": 1, "fanart": 1, "covers": 1, "lyrics": 1}
        jobs = {j.kind: j.params for j in await db.scalars(select(ArtworkJob))}
        assert jobs["fanart"] == {"rating_keys": [100]}
        assert jobs["covers"]["rating_keys"] == [200]
        lookup = await db.scalar(select(FileScan).where(FileScan.kind == "lyrics"))
        assert lookup is not None
        assert (lookup.params["albums"], lookup.params["mode"]) == ([200], "new")
        assert lookup.params["write"]["sidecar"] is True  # tag editing is on
        note = await db.scalar(select(Notification).where(Notification.key.like("new-music:%")))
        assert note is not None
        assert note.title == "New in Plex: 1 album and 1 new artist"
        assert note.detail == "Radiohead · OK Computer. Getting logos, best covers and lyrics."
        assert note.link == "/settings/jobs"
        # Done once: the next sync finds nothing new.
        assert await new_music.act(db, get_settings(), server_id) == {}

    # Only the server's owner chooses.
    friend, _ = await sign_in(app, plex, "friend-token")
    from tests.test_sync import connect

    friend_sid = await connect(friend)
    assert (await friend.get(f"/api/servers/{friend_sid}/new-music")).status_code == 403


async def test_what_cant_be_done_is_said(app: FastAPI, plex: FakePlex) -> None:
    """No fanart.tv key: no logos; tag editing off: lyrics looked up, not written."""
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        await db.execute(update(PlexServer).values(local_path_prefix="/music"))
        await db.commit()
    body = {"logos": True, "covers": False, "lyrics": True, "lyrics_write": {"sidecar": True}}
    since = dt.datetime.fromisoformat(
        (await owner.put(f"/api/servers/{sid}/new-music", json=body, headers=CSRF)).json()["since"]
    )
    async with get_sessionmaker()() as db:
        server_id = await db.scalar(select(PlexServer.id))
        assert server_id is not None
        later = since + dt.timedelta(minutes=1)
        await db.execute(update(Album).where(Album.rating_key == 202).values(added_at=later))
        await db.execute(update(Artist).where(Artist.rating_key == 101).values(added_at=later))
        await db.commit()
        done = await new_music.act(db, get_settings(), server_id)
        assert done == {"artists": 1, "albums": 1, "lyrics": 1}
        lookup = await db.scalar(select(FileScan).where(FileScan.kind == "lyrics"))
        assert lookup is not None and "write" not in lookup.params
        note = await db.scalar(select(Notification).where(Notification.key.like("new-music:%")))
        assert note is not None and note.detail is not None
        assert "No logos: fanart.tv isn't set up" in note.detail
        assert "Lyrics only looked up, not written: tag editing is off." in note.detail
