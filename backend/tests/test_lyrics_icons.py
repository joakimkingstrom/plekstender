"""Lyrics icons (a preference): what lyrics each song's files have, and how many of an
album's songs have some, on the artist and album pages."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender.db import get_sessionmaker
from plekstender.db.models import MediaFile, User, UserServer
from plekstender.tags.read import LYRICS_READER
from tests.fakes import CSRF, FakePlex
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_lyrics_icons(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        us = await db.scalar(select(UserServer).where(UserServer.user_id == user_id))
        assert us is not None
        files = [
            # OK Computer: one song synced, one read and without lyrics.
            (300, "synced", LYRICS_READER, None),
            (301, None, LYRICS_READER, None),
            # OKNOTOK: word-synced, and one song in two files (the better one counts).
            (302, "word", LYRICS_READER, None),
            (303, "plain", LYRICS_READER, None),
            (303, "word", LYRICS_READER, None),
            # Dummy: read before lyrics were looked for, and one that couldn't be read.
            (304, "plain", LYRICS_READER - 1, None),
            (304, "plain", LYRICS_READER, "unreadable"),
        ]
        for n, (key, kind, reader, error) in enumerate(files):
            db.add(
                MediaFile(
                    server_id=us.server_id, path=f"m/{n}.flac", size=1, mtime=1.0, tags={},
                    rating_key=key, lyrics=kind, reader=reader, error=error,
                )
            )  # fmt: skip
        await db.commit()

    def lyrics_of(body: dict[str, object], field: str) -> dict[int, object]:
        rows = body[field]
        assert isinstance(rows, list)
        return {row["rating_key"]: row["lyrics"] for row in rows}

    # Off (the default): not marked.
    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert album["lyrics"] is None
    assert set(lyrics_of(album, "tracks").values()) == {None}

    prefs = (await owner.get("/api/auth/me")).json()["preferences"]
    assert prefs["lyrics_icons"] is False
    resp = await owner.put(
        "/api/auth/me/preferences", json={**prefs, "lyrics_icons": True}, headers=CSRF
    )
    assert resp.status_code == 200, resp.text

    album = (await owner.get(f"/api/servers/{sid}/library/albums/200")).json()
    assert album["lyrics"] == {"songs": 2, "with_lyrics": 1}
    assert lyrics_of(album, "tracks") == {300: "synced", 301: "none"}

    artist = (await owner.get(f"/api/servers/{sid}/library/artists/100")).json()
    assert lyrics_of(artist, "albums") == {
        200: {"songs": 2, "with_lyrics": 1},
        201: {"songs": 2, "with_lyrics": 2},
    }
    assert lyrics_of(artist, "top_tracks") == {301: "none", 300: "synced", 303: "word"}

    # None of Dummy's files have been read for lyrics: not marked.
    dummy = (await owner.get(f"/api/servers/{sid}/library/albums/202")).json()
    assert dummy["lyrics"] is None
    assert lyrics_of(dummy, "tracks") == {304: None}
