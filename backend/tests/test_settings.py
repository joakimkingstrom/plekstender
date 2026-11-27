"""Library settings: popularity tuning and the sync schedule."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from plekstender.db import get_sessionmaker
from plekstender.db.instance import LibrarySettings, get_library_settings
from plekstender.db.models import ArtistArea, SyncRun
from plekstender.sync.schedule import due_sync
from plekstender.worker import recompute_all_popularity, schedule_due_syncs
from tests.fakes import CSRF, FakePlex, _album, _artist, _track, sign_in
from tests.test_sync import connect, owner_with_library

NOW = dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.UTC)
SETTINGS = LibrarySettings(incremental_minutes=30, full_days=7)


@pytest.mark.parametrize(
    ("last_attempt", "last_full", "expected"),
    [
        (None, None, "full"),  # never synced
        (NOW - dt.timedelta(minutes=10), None, None),  # tried recently (even if it failed)
        (NOW - dt.timedelta(minutes=31), NOW - dt.timedelta(days=1), "incremental"),
        (NOW - dt.timedelta(minutes=31), NOW - dt.timedelta(days=7), "full"),
        (NOW - dt.timedelta(minutes=5), NOW - dt.timedelta(days=30), None),
    ],
)
def test_due_sync(
    last_attempt: dt.datetime | None, last_full: dt.datetime | None, expected: str | None
) -> None:
    assert due_sync(NOW, last_attempt=last_attempt, last_full=last_full, settings=SETTINGS) == (
        expected
    )


@pytest.mark.db
async def test_admin_edits_library_settings(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)

    defaults = (await owner.get("/api/admin/library")).json()
    assert defaults == {
        "album_top_n": 3,
        "artist_top_n": 10,
        "incremental_minutes": 30,
        "full_days": 7,
    }
    status = (await owner.get(f"/api/servers/{sid}/sync")).json()
    assert (status["incremental_minutes"], status["full_days"]) == (30, 7)

    # Preview: with only each artist's top song, Radiohead scores 2000 (Karma Police).
    preview = (
        await owner.get("/api/admin/library/preview", params={"server_id": sid, "artist_top_n": 1})
    ).json()
    assert [(p["title"], p["popularity"], p["current_rank"]) for p in preview] == [
        ("Radiohead", 2000, 1),
        ("Portishead", 800, 2),
        ("Secret Band", 10, 3),
    ]

    bad = await owner.put("/api/admin/library", json=defaults | {"artist_top_n": 0}, headers=CSRF)
    assert bad.status_code == 422

    saved = await owner.put(
        "/api/admin/library",
        json={"album_top_n": 1, "artist_top_n": 1, "incremental_minutes": 60, "full_days": 14},
        headers=CSRF,
    )
    assert saved.status_code == 200
    async with get_sessionmaker()() as db:
        queued = await db.scalar(
            text(
                "SELECT count(*) FROM procrastinate_jobs"
                " WHERE task_name = 'plekstender.worker.recompute_all_popularity'"
            )
        )
    assert queued == 1

    await recompute_all_popularity.func()  # what the worker runs
    radiohead = (await owner.get(f"/api/servers/{sid}/library/artists/100")).json()
    assert radiohead["popularity"] == 2000
    assert radiohead["country"] == "GB"  # Plex's "United Kingdom"
    async with get_sessionmaker()() as db:  # areas are kept across servers: start clean
        name_key = await db.scalar(text("SELECT name_key FROM artists WHERE rating_key = 101"))
        await db.execute(text("DELETE FROM artist_areas WHERE key = :k"), {"k": f"name:{name_key}"})
        await db.commit()
    portishead = (await owner.get(f"/api/servers/{sid}/library/artists/101")).json()
    assert portishead["country"] is None  # not looked up on MusicBrainz yet
    async with get_sessionmaker()() as db:  # MusicBrainz says (by name: no MBID in Plex)
        db.add(
            ArtistArea(
                key=f"name:{name_key}", country_code="GB", checked_at=dt.datetime.now(dt.UTC)
            )
        )
        await db.commit()
    portishead = (await owner.get(f"/api/servers/{sid}/library/artists/101")).json()
    assert portishead["country"] == "GB"
    assert [a["popularity"] for a in radiohead["albums"]] == [2000, 2000]

    status = (await owner.get(f"/api/servers/{sid}/sync")).json()
    assert (status["incremental_minutes"], status["full_days"]) == (60, 14)


@pytest.mark.db
async def test_library_settings_are_admin_only(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await sign_in(app, plex, "owner-token")
    await connect(owner)  # approves the server so the friend may sign in
    friend, _ = await sign_in(app, plex, "friend-token")
    assert (await friend.get("/api/admin/library")).status_code == 403
    put = await friend.put("/api/admin/library", json=LibrarySettings().model_dump(), headers=CSRF)
    assert put.status_code == 403
    preview = await friend.get(
        "/api/admin/library/preview", params={"server_id": 1, "artist_top_n": 3}
    )
    assert preview.status_code == 403


@pytest.mark.db
async def test_invalid_stored_values_fall_back_per_setting(clean_db: None) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "INSERT INTO instance_settings (key, value) VALUES"
                """ ('library', '{"album_top_n": 99, "artist_top_n": 5}')"""
            )
        )
        await db.commit()
        settings = await get_library_settings(db)
    assert settings.album_top_n == 3  # 99 is out of range: default
    assert settings.artist_top_n == 5  # valid: kept


@pytest.mark.db
async def test_scheduler_queues_due_syncs(app: FastAPI, plex: FakePlex) -> None:
    await owner_with_library(app, plex)  # a full sync just ran

    assert await schedule_due_syncs() == {"full": 0, "incremental": 0}
    later = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=31)
    assert await schedule_due_syncs(later) == {"full": 0, "incremental": 1}

    async with get_sessionmaker()() as db:
        await db.execute(text("UPDATE sync_runs SET status = 'succeeded'"))
        await db.execute(text("DELETE FROM procrastinate_jobs"))
        await db.commit()
    much_later = dt.datetime.now(dt.UTC) + dt.timedelta(days=8)
    assert await schedule_due_syncs(much_later) == {"full": 1, "incremental": 0}
    async with get_sessionmaker()() as db:
        kinds = list(await db.scalars(select(SyncRun.kind).order_by(SyncRun.id)))
    assert kinds == ["full", "incremental", "full"]


@pytest.mark.db
async def test_preview_leaves_out_various_artists(app: FastAPI, plex: FakePlex) -> None:
    # A compilation by "Various Artists" with a huge hit: not an artist to rank.
    plex.library.items[110] = _artist(110, "Various Artists")
    plex.library.items[210] = _album(210, "Now That's What I Call Music", 110, 2001)
    plex.library.items[310] = _track(310, "Big Hit", 210, 110, 1, 9_000_000)
    owner, sid = await owner_with_library(app, plex)

    preview = (
        await owner.get("/api/admin/library/preview", params={"server_id": sid, "artist_top_n": 10})
    ).json()
    assert [(p["title"], p["current_rank"]) for p in preview] == [
        ("Radiohead", 1),
        ("Portishead", 2),
        ("Secret Band", 3),
    ]
