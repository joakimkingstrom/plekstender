"""Genres, styles and moods: counts, filters, their pages and as a playlist rule."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from tests.fakes import CSRF, FakePlex, sign_in
from tests.test_playlists import keys, preview
from tests.test_sync import owner_with_library, run_queued_syncs

pytestmark = pytest.mark.db


async def test_tags_count_artists_through_their_albums(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/library"

    # Melancholy is on Radiohead; Paranoid only on their album OK Computer.
    moods = (await owner.get(f"{base}/tags", params={"kind": "mood"})).json()
    assert [(t["name"], t["count"]) for t in moods] == [("Melancholy", 1), ("Paranoid", 1)]
    # Rock is on Radiohead, Secret Band and OK Computer: still two artists.
    genres = (await owner.get(f"{base}/tags", params={"kind": "genre"})).json()
    assert ("Rock", 2) in [(t["name"], t["count"]) for t in genres]

    paranoid = moods[1]["id"]
    found = (await owner.get(f"{base}/artists", params={"tag": paranoid})).json()
    assert [a["title"] for a in found["items"]] == ["Radiohead"]


async def test_tag_page(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/library"
    genres = {t["name"]: t["id"] for t in (await owner.get(f"{base}/tags")).json()}

    rock = (await owner.get(f"{base}/tags/{genres['Rock']}")).json()
    assert (rock["kind"], rock["name"]) == ("genre", "Rock")
    assert (rock["artist_count"], rock["album_count"]) == (2, 1)
    # Most played first: Radiohead has plays, Secret Band none.
    assert [a["title"] for a in rock["artists"]] == ["Radiohead", "Secret Band"]
    assert [(a["title"], a["artist_title"]) for a in rock["albums"]] == [
        ("OK Computer", "Radiohead")
    ]
    # Found with Rock on Radiohead or OK Computer; countries and formats left out.
    related = {(t["kind"], t["name"]): t["count"] for t in rock["related"]}
    assert related == {
        ("genre", "Alternative"): 1,
        ("mood", "Melancholy"): 1,
        ("mood", "Paranoid"): 1,
        ("style", "Art Rock"): 1,
    }

    by_name = (await owner.get(f"{base}/tags/{genres['Rock']}", params={"sort": "name"})).json()
    assert [a["title"] for a in by_name["artists"]] == ["Radiohead", "Secret Band"]

    assert (await owner.get(f"{base}/tags/999999")).status_code == 404

    # A shared user only counts what they can see (Secret Band is hidden from them).
    friend, _ = await sign_in(app, plex, "friend-token")
    friend_sid = (
        await friend.post("/api/servers", json={"machine_identifier": "home-machine"}, headers=CSRF)
    ).json()["server"]["id"]
    await run_queued_syncs()
    seen = (await friend.get(f"/api/servers/{friend_sid}/library/tags/{genres['Rock']}")).json()
    assert [a["title"] for a in seen["artists"]] == ["Radiohead"]


async def test_playlist_tag_rule(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    moods = {
        t["name"]: t["id"]
        for t in (
            await owner.get(f"/api/servers/{sid}/library/tags", params={"kind": "mood"})
        ).json()
    }
    # Only Radiohead's songs on an album (or by an artist) with the Paranoid mood: the
    # songs on OK Computer, not the compilation's Lucky.
    body = await preview(
        owner, sid, artists=[100], order="popular", rules={"tags": [moods["Paranoid"]]}
    )
    assert sorted(keys(body)) == [300, 301]
    # Melancholy is on Radiohead themselves, so every song of theirs passes.
    body = await preview(
        owner, sid, artists=[100], order="popular", rules={"tags": [moods["Melancholy"]]}
    )
    assert len(keys(body)) == 3
    # A song chosen by hand is always in.
    body = await preview(
        owner, sid, artists=[101], tracks=[303], rules={"tags": [moods["Paranoid"]]}
    )
    assert keys(body) == [303]
