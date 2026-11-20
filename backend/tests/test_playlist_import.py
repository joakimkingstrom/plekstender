# ruff: noqa: E501 (hand-aligned test data)
"""Importing playlists from files (Exportify / TuneMyMusic CSV, M3U, JSPF)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI

from plekstender.playlists.imports import PlaylistFileError, parse, split_artists
from tests.fakes import CSRF, FakePlex
from tests.test_sync import owner_with_library

EXPORTIFY = (
    '"Track URI","Track Name","Artist Name(s)","Album Name","Disc Number","Track Number","Track Duration (ms)","ISRC"\n'
    '"spotify:track:1","Karma Police","Radiohead","OK Computer","1","6","264066","GBAYE9700003"\n'
    '"spotify:track:2","Glory Box","Portishead","Dummy","1","11","301000",""\n'
    '"spotify:track:3","Not In The Library","Somebody, Someone Else","X","1","1","200000",""\n'
)
TUNEMYMUSIC = "Track name,Artist name,Album,Playlist name,Type,ISRC\nLucky,Radiohead,OK Computer OKNOTOK,Road trip,Playlist,\n"
M3U = "#EXTM3U\n#PLAYLIST:Evening\n#EXTINF:240,Radiohead - Paranoid Android\n/music/whatever.flac\n/music/Portishead/Dummy/11 - Glory Box.flac\nC:\\Music\\Radiohead - Lucky.mp3\n"
JSPF = json.dumps({"playlist": {"title": "From ListenBrainz", "track": [
    {"title": "Karma Police", "creator": "Radiohead", "album": "OK Computer OKNOTOK", "duration": 240000},
]}})  # fmt: skip


def test_reading_playlist_files() -> None:
    name, songs = parse("My Mix.csv", EXPORTIFY)
    assert name == "My Mix"  # no name in the file: the file's
    assert [(s.title, s.artists[0], s.album, s.duration_ms) for s in songs] == [
        ("Karma Police", "Radiohead", "OK Computer", 264066),
        ("Glory Box", "Portishead", "Dummy", 301000),
        ("Not In The Library", "Somebody, Someone Else", "X", 200000),
    ]
    assert songs[2].artists == ["Somebody, Someone Else", "Somebody", "Someone Else"]
    assert parse("export.csv", TUNEMYMUSIC)[0] == "Road trip"
    name, songs = parse("evening.m3u8", M3U)
    assert name == "Evening"
    assert [(s.title, s.artists[0], s.album) for s in songs] == [
        ("Paranoid Android", "Radiohead", None), ("Glory Box", "Portishead", "Dummy"), ("Lucky", "Radiohead", None),
    ]  # fmt: skip
    name, songs = parse("x.jspf", JSPF)
    assert (name, songs[0].title, songs[0].duration_ms) == (
        "From ListenBrainz",
        "Karma Police",
        240000,
    )
    assert split_artists("Simon & Garfunkel") == ["Simon & Garfunkel", "Simon", "Garfunkel"]
    with pytest.raises(PlaylistFileError):
        parse("x.csv", "just,some\n1,2\n")


@pytest.mark.db
async def test_import_a_playlist(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    preview = await owner.post(
        f"/api/servers/{sid}/playlists/import/preview",
        params={"filename": "My Mix.csv"},
        content=EXPORTIFY.encode(),
        headers={**CSRF, "content-type": "text/csv"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["name"], body["total"], body["matched"]) == ("My Mix", 3, 2)
    karma, glory, missing = body["songs"]
    # Two Karma Polices in the library: the one on the same album.
    assert (karma["match"]["rating_key"], karma["match"]["album"], karma["match"]["how"]) == (
        301,
        "OK Computer",
        "exact",
    )
    assert glory["match"]["rating_key"] == 304
    assert missing["match"] is None

    # The other Karma Police (on OKNOTOK) for the JSPF that names that album.
    jspf = await owner.post(
        f"/api/servers/{sid}/playlists/import/preview",
        params={"filename": "x.jspf"},
        content=JSPF.encode(),
        headers={**CSRF, "content-type": "application/json"},
    )
    assert jspf.json()["songs"][0]["match"]["rating_key"] == 302

    made = await owner.post(
        f"/api/servers/{sid}/playlists/import",
        json={"name": "My Mix", "rating_keys": [301, 304, 999999]},
        headers=CSRF,
    )
    assert made.status_code == 201, made.text
    assert made.json()["songs"] == 2  # not in the library: left out
    created = plex.library.playlists[made.json()["plex_playlist_key"]]
    assert (created["title"], created["items"]) == ("My Mix", [301, 304])

    bad = await owner.post(
        f"/api/servers/{sid}/playlists/import/preview",
        params={"filename": "x.csv"},
        content=b"nothing here",
        headers={**CSRF, "content-type": "text/csv"},
    )
    assert bad.status_code == 422
