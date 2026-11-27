"""Background job runner (procrastinate, backed by Postgres) and job scheduling helpers."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx
from procrastinate import App, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import __version__, release_covers
from plekstender.artist_countries import look_up as look_up_artist_countries
from plekstender.artwork.folders import check_server as check_folder_mapping
from plekstender.artwork.folders import write_queued as write_queued_folder
from plekstender.artwork.jobs import run_job as run_artwork_job
from plekstender.artwork.packs import remove_old_files
from plekstender.artwork.service import remove_old_uploads
from plekstender.auth.sessions import delete_expired_sessions
from plekstender.config import get_settings
from plekstender.cover_colours import colour_covers
from plekstender.db import get_sessionmaker
from plekstender.db.instance import get_library_settings, record_worker_heartbeat
from plekstender.db.models import (
    ArtworkJob,
    FileScan,
    ListenBrainzLink,
    ListenImport,
    LiveSettings,
    Playlist,
    PlexServer,
    ScrobbleTarget,
    SyncRun,
    TagChangeSet,
    UserSectionSync,
    UserServer,
)
from plekstender.history.geo import update_database as update_geoip_database
from plekstender.history.lastfm_import import run_lastfm_import
from plekstender.history.listen_counts import refresh_listen_counts
from plekstender.history.listenbrainz import submit_pending
from plekstender.history.listenbrainz_import import run_listenbrainz_import
from plekstender.history.listenbrainz_send import run_listenbrainz_send
from plekstender.history.places import fill_places, refresh_public_addresses
from plekstender.history.plex_capture import capture_plays
from plekstender.history.scrobblers import submit_targets
from plekstender.history.sessions import POLLS_PER_RUN, attach_sessions, watch
from plekstender.job_list import close_abandoned
from plekstender.playlists.service import build as build_playlist_in_plex
from plekstender.previews import trim_cache as trim_preview_cache
from plekstender.releases import look_up as look_up_releases
from plekstender.sync.popularity import recompute_popularity
from plekstender.sync.runner import run_sync
from plekstender.sync.schedule import due_sync
from plekstender.tags.apply import run_change_set as run_tag_change_set
from plekstender.tags.scan import run_scan as run_file_scan

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
# A running sync updates its progress at least every page; silence this long means the
# worker died mid-run.
STALE_AFTER = dt.timedelta(minutes=30)


def _connector() -> PsycopgConnector:
    return PsycopgConnector(conninfo=get_settings().libpq_url)


app = App(connector=_connector(), import_paths=["plekstender.worker"])


# --- sync -------------------------------------------------------------------------------


@app.task(queue="sync")
async def sync_library(run_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_sync(db, http, get_settings(), run_id)
        run = await db.get(SyncRun, run_id, populate_existing=True)
        us = await db.get(UserServer, run.user_server_id) if run else None
    if run is not None and run.status == "succeeded" and us is not None:
        await enqueue_listen_counts([us.user_id])  # new or changed items to match
        await enqueue_new_music(us.server_id)  # logos, covers and lyrics for what's new


@app.task(queue="maintenance", lock="new-music")
async def act_on_new_music(server_id: int) -> None:
    """After a sync: the owner's automatic actions for artists and albums new in Plex (see
    plekstender.new_music)."""
    from plekstender import new_music

    async with get_sessionmaker()() as db:
        done = await new_music.act(db, get_settings(), server_id)
    if done:
        log.info("new music on server %s: %s", server_id, done)


async def enqueue_new_music(server_id: int) -> None:
    try:
        await act_on_new_music.configure(queueing_lock=f"new-music-{server_id}").defer_async(
            server_id=server_id
        )
    except AlreadyEnqueued:
        pass


async def enqueue_sync(db: AsyncSession, us: UserServer, kind: str, trigger: str) -> SyncRun:
    """Queue a sync for one user's server connection, unless one is already pending.

    A pending incremental sync is upgraded when a full one is asked for. Syncs for the
    same server never run at the same time (procrastinate lock), whoever started them.
    """
    existing = await db.scalar(
        select(SyncRun)
        .where(SyncRun.user_server_id == us.id, SyncRun.status.in_(ACTIVE))
        .order_by(SyncRun.id.desc())
        .limit(1)
    )
    if existing is not None:
        if kind == "full" and existing.kind != "full" and existing.status == "queued":
            existing.kind = "full"
            await db.commit()
        return existing

    run = SyncRun(user_server_id=us.id, kind=kind, trigger=trigger, status="queued")
    db.add(run)
    await db.commit()
    try:
        await sync_library.configure(
            lock=f"sync-server-{us.server_id}", queueing_lock=f"sync-us-{us.id}"
        ).defer_async(run_id=run.id)
    except AlreadyEnqueued:
        run.status = "failed"
        run.error = "Another sync was already queued."
        await db.commit()
    return run


async def schedule_due_syncs(now: dt.datetime | None = None) -> dict[str, int]:
    """Queue a sync for every connection that's due one. Returns counts per kind."""
    now = now or dt.datetime.now(dt.UTC)
    queued: dict[str, int] = {"full": 0, "incremental": 0}
    async with get_sessionmaker()() as db:
        settings = await get_library_settings(db)
        connections = [us for us in await db.scalars(select(UserServer)) if us.music_section_keys]
        if not connections:
            return queued
        ids = [us.id for us in connections]
        last_attempts = dict(
            (
                await db.execute(
                    select(SyncRun.user_server_id, func.max(SyncRun.created_at))
                    .where(SyncRun.user_server_id.in_(ids))
                    .group_by(SyncRun.user_server_id)
                )
            ).all()
        )
        full_syncs: dict[tuple[int, str], dt.datetime | None] = {
            (row.user_server_id, row.section_key): row.last_full_sync_at
            for row in await db.scalars(
                select(UserSectionSync).where(UserSectionSync.user_server_id.in_(ids))
            )
        }
        for us in connections:
            fulls = [full_syncs.get((us.id, key)) for key in us.music_section_keys]
            last_full = None if any(f is None for f in fulls) else min(f for f in fulls if f)
            kind = due_sync(
                now,
                last_attempt=last_attempts.get(us.id),
                last_full=last_full,
                settings=settings,
            )
            if kind is not None:
                await enqueue_sync(db, us, kind, "schedule")
                queued[kind] += 1
    return queued


@app.periodic(cron="*/5 * * * *", periodic_id="schedule-syncs")
@app.task(queue="maintenance", queueing_lock="schedule-syncs")
async def schedule_syncs(timestamp: int) -> None:
    """Every 5 minutes: queue the syncs that are due (intervals are set on Settings)."""
    queued = await schedule_due_syncs()
    if any(queued.values()):
        log.info("queued syncs: %s", queued)


@app.task(queue="maintenance", queueing_lock="recompute-popularity")
async def recompute_all_popularity() -> None:
    """Re-score albums and artists on every server after the popularity settings change."""
    async with get_sessionmaker()() as db:
        settings = await get_library_settings(db)
        for server_id in list(await db.scalars(select(PlexServer.id))):
            await recompute_popularity(db, server_id, settings)
            await db.commit()


async def enqueue_popularity_recompute() -> None:
    try:
        await recompute_all_popularity.defer_async()
    except AlreadyEnqueued:
        pass  # one is already waiting and will use the latest settings


# --- listening history ------------------------------------------------------------------


@app.task(queue="imports")
async def import_lastfm(import_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_lastfm_import(db, http, get_settings(), import_id)
    await _after_import(import_id)


@app.task(queue="imports")
async def import_listenbrainz(import_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_listenbrainz_import(db, http, get_settings(), import_id)
    await _after_import(import_id)


@app.task(queue="imports")
async def send_listenbrainz_history(import_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_listenbrainz_send(db, http, get_settings(), import_id)


async def _after_import(import_id: int) -> None:
    async with get_sessionmaker()() as db:
        job = await db.get(ListenImport, import_id)
    if job is not None and job.status in ("succeeded", "failed"):  # stopped ones too
        await enqueue_listen_counts([job.user_id])


async def enqueue_listen_import(db: AsyncSession, job: ListenImport) -> None:
    """Queue a (new or resumed) import. One import per user runs at a time."""
    task = {
        "listenbrainz": import_listenbrainz,
        "lb_send": send_listenbrainz_history,
    }.get(job.source, import_lastfm)
    try:
        await task.configure(
            lock=f"import-user-{job.user_id}", queueing_lock=f"import-{job.id}"
        ).defer_async(import_id=job.id)
    except AlreadyEnqueued:
        pass  # already waiting to run


async def capture_connections(db: AsyncSession) -> list[UserServer]:
    """Connections to read Plex play history through.

    Per server, the owner's connection sees every account's plays, so it's the only one
    used when the owner has connected. Otherwise each user's own connection is read. A
    connection waits until its libraries have had a full sync (names come from there).
    """
    connections = [us for us in await db.scalars(select(UserServer)) if us.music_section_keys]
    synced: dict[int, set[str]] = {}
    for row in await db.scalars(
        select(UserSectionSync).where(UserSectionSync.last_full_sync_at.is_not(None))
    ):
        synced.setdefault(row.user_server_id, set()).add(row.section_key)
    by_server: dict[int, list[UserServer]] = {}
    for us in connections:
        by_server.setdefault(us.server_id, []).append(us)
    chosen = []
    for group in by_server.values():
        owners = [us for us in group if us.is_owner]
        for us in owners or group:
            if set(us.music_section_keys) <= synced.get(us.id, set()):
                chosen.append(us)
    return chosen


@app.task(queue="history")
async def capture_plex_plays(user_server_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        us = await db.get(UserServer, user_server_id)
        if us is None or not us.music_section_keys:
            return
        result = await capture_plays(db, http, get_settings(), us)
        # Plays seen live get their player and place.
        for user_id, since in result.earliest.items():
            await attach_sessions(db, user_id, since)
        await db.commit()
    if result.added:
        log.info("captured %d Plex plays via connection %s", result.added, user_server_id)
        await enqueue_listenbrainz(list(result.earliest))
        await enqueue_listen_counts(list(result.earliest))
    for section, error in result.errors.items():
        log.warning("play history of section %s via %s: %s", section, user_server_id, error)


@app.task(queue="history")
async def update_listen_counts(user_id: int) -> None:
    """Re-match a user's listens to the library (see history.listen_counts)."""
    async with get_sessionmaker()() as db:
        await refresh_listen_counts(db, user_id)
        await db.commit()


async def enqueue_listen_counts(user_ids: list[int]) -> None:
    for user_id in set(user_ids):
        try:
            await update_listen_counts.configure(
                lock=f"listen-counts-{user_id}", queueing_lock=f"listen-counts-{user_id}"
            ).defer_async(user_id=user_id)
        except AlreadyEnqueued:
            pass  # one is already waiting and will see the latest listens


async def schedule_play_capture() -> int:
    """Queue a play-history read for every connection that should be read. Returns count."""
    async with get_sessionmaker()() as db:
        connections = await capture_connections(db)
    queued = 0
    for us in connections:
        try:
            await capture_plex_plays.configure(
                lock=f"capture-server-{us.server_id}", queueing_lock=f"capture-us-{us.id}"
            ).defer_async(user_server_id=us.id)
            queued += 1
        except AlreadyEnqueued:
            pass
    return queued


@app.task(queue="history")
async def submit_listenbrainz(user_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        result = await submit_pending(db, http, get_settings(), user_id)
        # The other scrobble services (Maloja, Koito, Libre.fm…), each on its own.
        others = await submit_targets(db, http, get_settings(), user_id)
    if result.sent or result.skipped:
        log.info(
            "ListenBrainz: sent %d, skipped %d for user %s", result.sent, result.skipped, user_id
        )
    if result.error:
        log.warning("ListenBrainz submission for user %s: %s", user_id, result.error)
    for target_id, other in others.items():
        if other.error:
            log.warning("Scrobble target %s for user %s: %s", target_id, user_id, other.error)


async def enqueue_listenbrainz(user_ids: list[int] | None = None) -> int:
    """Queue scrobbling (ListenBrainz and the other services) for these users (default:
    everyone with somewhere to send plays)."""
    async with get_sessionmaker()() as db:
        query = select(ListenBrainzLink.user_id).where(ListenBrainzLink.paused.is_(False))
        others = select(ScrobbleTarget.user_id).where(ScrobbleTarget.paused.is_(False))
        if user_ids is not None:
            query = query.where(ListenBrainzLink.user_id.in_(user_ids))
            others = others.where(ScrobbleTarget.user_id.in_(user_ids))
        linked = sorted(set(await db.scalars(query)) | set(await db.scalars(others)))
    for user_id in linked:
        try:
            await submit_listenbrainz.configure(
                lock=f"listenbrainz-user-{user_id}", queueing_lock=f"listenbrainz-{user_id}"
            ).defer_async(user_id=user_id)
        except AlreadyEnqueued:
            pass
    return len(linked)


@app.periodic(cron="*/15 * * * *", periodic_id="retry-listenbrainz")
@app.task(queue="maintenance", queueing_lock="retry-listenbrainz")
async def retry_listenbrainz(timestamp: int) -> None:
    """Every 15 minutes: send anything a failed submission left behind."""
    await enqueue_listenbrainz()


@app.periodic(cron="* * * * *", periodic_id="watch-sessions")
@app.task(queue="sessions", queueing_lock="watch-sessions")
async def watch_sessions(timestamp: int) -> None:
    """Every minute: poll what's playing every 10 s (skips, players, locations)."""
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        connections = await capture_connections(db)
        await watch(db, http, get_settings(), connections, POLLS_PER_RUN, asyncio.sleep)


@app.periodic(cron="40 * * * *", periodic_id="update-geoip")
@app.task(queue="maintenance", queueing_lock="update-geoip")
async def update_geoip(timestamp: int) -> None:
    """Hourly check (a no-op while the copy is fresh): fetch the IP location database when
    it's missing or a month old, so a new install has locations within the hour."""
    settings = get_settings()
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await update_geoip_database(http, settings)
        # Places for the listens map: the servers' own locations (for home-network plays),
        # then any plays not placed yet.
        await refresh_public_addresses(db, http, settings)
        await fill_places(db, settings)


@app.periodic(cron="*/5 * * * *", periodic_id="capture-plays")
@app.task(queue="maintenance", queueing_lock="capture-plays")
async def capture_plays_periodically(timestamp: int) -> None:
    """Every 5 minutes: read new plays from Plex's play history."""
    await schedule_play_capture()


# --- artwork ------------------------------------------------------------------------------


@app.task(queue="artwork")
async def artwork_job(job_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_artwork_job(db, http, get_settings(), job_id)


async def enqueue_artwork_job(job: ArtworkJob) -> None:
    """Queue an artwork job. One runs per server at a time."""
    try:
        await artwork_job.configure(
            lock=f"artwork-server-{job.server_id}", queueing_lock=f"artwork-job-{job.id}"
        ).defer_async(job_id=job.id)
    except AlreadyEnqueued:
        pass


# --- playlists ----------------------------------------------------------------------------


@app.task(queue="playlists")
async def build_playlist(playlist_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await build_playlist_in_plex(db, http, get_settings(), playlist_id)


async def enqueue_playlist_build(playlist: Playlist) -> None:
    """Queue a rebuild. One build per user's server at a time, so Plex isn't flooded."""
    try:
        await build_playlist.configure(
            lock=f"playlists-us-{playlist.user_server_id}",
            queueing_lock=f"playlist-{playlist.id}",
        ).defer_async(playlist_id=playlist.id)
    except AlreadyEnqueued:
        pass


async def schedule_due_playlists(now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as db:
        due = list(
            await db.scalars(
                select(Playlist).where(
                    Playlist.schedule != "manual",
                    Playlist.next_run_at <= now,
                    Playlist.status.not_in(ACTIVE),
                )
            )
        )
        for playlist in due:
            playlist.status = "queued"
        await db.commit()
    for playlist in due:
        await enqueue_playlist_build(playlist)
    return len(due)


@app.periodic(cron="*/10 * * * *", periodic_id="refresh-playlists")
@app.task(queue="maintenance", queueing_lock="refresh-playlists")
async def refresh_playlists(timestamp: int) -> None:
    await schedule_due_playlists()


# --- music folders (only the worker mounts them) -------------------------------------------


@app.task(queue="artwork")
async def save_artist_folder(server_id: int, rating_key: int) -> None:
    async with get_sessionmaker()() as db:
        await write_queued_folder(db, get_settings().data_dir, server_id, rating_key)


async def enqueue_folder_save(server_id: int, rating_key: int) -> None:
    """Write a queued image as artist.jpg (a newer image for the same artist replaces it)."""
    try:
        await save_artist_folder.configure(
            lock=f"folders-{server_id}", queueing_lock=f"folder-{server_id}-{rating_key}"
        ).defer_async(server_id=server_id, rating_key=rating_key)
    except AlreadyEnqueued:
        pass


@app.task(queue="artwork")
async def check_artist_folders(server_id: int) -> None:
    async with get_sessionmaker()() as db:
        server = await db.get(PlexServer, server_id)
        if server is not None:
            await check_folder_mapping(db, server)


async def enqueue_folder_check(server_id: int) -> None:
    try:
        await check_artist_folders.configure(
            lock=f"folders-{server_id}", queueing_lock=f"folder-check-{server_id}"
        ).defer_async(server_id=server_id)
    except AlreadyEnqueued:
        pass


@app.task(queue="files")
async def scan_music_files(scan_id: int) -> None:
    async with get_sessionmaker()() as db:
        await run_file_scan(db, scan_id)


async def enqueue_file_scan(scan: FileScan) -> None:
    try:
        await scan_music_files.configure(
            lock=f"folders-{scan.server_id}", queueing_lock=f"file-scan-{scan.id}"
        ).defer_async(scan_id=scan.id)
    except AlreadyEnqueued:
        pass


@app.task(queue="files")
async def identify_music_files(job_id: int) -> None:
    from plekstender.tags.identify import run_identify

    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_identify(db, http, get_settings(), job_id)


async def enqueue_identify(job: FileScan) -> None:
    """Identify files by sound. Reads files only, so it may run beside scans and edits."""
    try:
        await identify_music_files.configure(
            lock=f"identify-{job.server_id}", queueing_lock=f"identify-{job.id}"
        ).defer_async(job_id=job.id)
    except AlreadyEnqueued:
        pass


@app.task(queue="files")
async def plan_renames(job_id: int) -> None:
    from plekstender.tags.rename import run_rename_plan

    async with get_sessionmaker()() as db:
        await run_rename_plan(db, job_id)


async def enqueue_rename_plan(job: FileScan) -> None:
    """A dry run of renaming files (reads folders; waits for scans and edits)."""
    try:
        await plan_renames.configure(
            lock=f"folders-{job.server_id}", queueing_lock=f"rename-{job.id}"
        ).defer_async(job_id=job.id)
    except AlreadyEnqueued:
        pass


@app.task(queue="files")
async def find_duplicates(job_id: int) -> None:
    from plekstender.tags.duplicates import run_duplicates

    async with get_sessionmaker()() as db:
        await run_duplicates(db, get_settings(), job_id)


async def enqueue_duplicates(job: FileScan) -> None:
    """Finding duplicate files (fingerprints the candidates; waits for scans and edits)."""
    try:
        await find_duplicates.configure(
            lock=f"folders-{job.server_id}", queueing_lock=f"dupes-{job.id}"
        ).defer_async(job_id=job.id)
    except AlreadyEnqueued:
        pass


@app.task(queue="files")
async def look_up_lyrics(job_id: int) -> None:
    from plekstender.tags.lyrics import run_lookup

    async with (
        httpx.AsyncClient() as http,
        get_sessionmaker()() as db,
    ):
        await run_lookup(db, http, get_settings(), job_id)


async def enqueue_lyrics_lookup(job: FileScan) -> None:
    """Looking up lyrics (mostly waiting on LRCLIB: its own lock, so scans aren't held up)."""
    try:
        await look_up_lyrics.configure(
            lock=f"lyrics-{job.server_id}", queueing_lock=f"lyrics-{job.id}"
        ).defer_async(job_id=job.id)
    except AlreadyEnqueued:
        pass


@app.task(queue="files")
async def apply_tag_changes(set_id: int) -> None:
    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await run_tag_change_set(db, http, get_settings(), set_id)


async def enqueue_tag_changes(change_set: TagChangeSet) -> None:
    """Apply an edit. One file job per server at a time (scans and edits don't overlap)."""
    try:
        await apply_tag_changes.configure(
            lock=f"folders-{change_set.server_id}", queueing_lock=f"tag-set-{change_set.id}"
        ).defer_async(set_id=change_set.id)
    except AlreadyEnqueued:
        pass


# --- housekeeping --------------------------------------------------------------------


# --- live shows -------------------------------------------------------------------------

# Shows are looked up about once a day; a refresh asked for by hand waits this long.
LIVE_MIN_AGE = dt.timedelta(hours=20)
LIVE_MANUAL_GAP = dt.timedelta(hours=1)


@app.task(queue="live", lock="jambase")
async def refresh_live(user_id: int, datathistle: str = "yes") -> None:
    """Look up one user's upcoming shows (one refresh at a time: they share the budgets).
    `datathistle`: "yes", "no", "only" or "starred" (see refresh_user)."""
    from plekstender.live.refresh import refresh_user as refresh_live_user  # (import cycle)

    mode = datathistle if datathistle in ("yes", "no", "only", "starred") else "yes"
    async with httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http:
        await refresh_live_user(
            get_sessionmaker(),
            http,
            get_settings(),
            user_id,
            datathistle=mode,  # type: ignore[arg-type]
        )


async def enqueue_live_refresh(
    db: AsyncSession, user_id: int, delay_seconds: int = 0, datathistle: str = "yes"
) -> bool:
    """Queue a refresh (once: a queued one already reads the latest settings). Commits.
    A Data Thistle-only lookup ("only") is queued separately and leaves the status alone."""
    live = await db.get(LiveSettings, user_id)
    if live is None:
        return False
    only = datathistle == "only"
    try:
        await refresh_live.configure(
            queueing_lock=f"live-{'dt-' if only else ''}{user_id}",
            schedule_in={"seconds": delay_seconds},
        ).defer_async(user_id=user_id, datathistle=datathistle)
    except AlreadyEnqueued:
        return True
    if only:
        live.datathistle_at = dt.datetime.now(dt.UTC)  # asked for: don't ask again at once
    else:
        live.refresh_status, live.refresh_error = "queued", None
        live.refresh_requested_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return True


async def schedule_live_refreshes(now: dt.datetime | None = None) -> list[int]:
    from plekstender.live.refresh import due_users as due_live_users  # (import cycle)
    from plekstender.live.refresh import expire_datathistle, forget_old_sightings

    now = now or dt.datetime.now(dt.UTC)
    settings = get_settings()
    async with get_sessionmaker()() as db:
        await expire_datathistle(db, now)
        await forget_old_sightings(db, now)
        await db.commit()
        users = await due_live_users(db, settings, now, LIVE_MIN_AGE)
        for user_id in users:
            # Data Thistle only for those who starred its shows (kept a day at most).
            await enqueue_live_refresh(db, user_id, datathistle="starred")
    return users


@app.periodic(cron="20 5 * * *", periodic_id="schedule-live")
@app.task(queue="maintenance", queueing_lock="schedule-live")
async def schedule_live(timestamp: int) -> None:
    """Daily (05:20 UTC): refresh users' shows, as many as today's share of the budget allows."""
    await schedule_live_refreshes()


async def renew_datathistle_tokens() -> bool:
    """Renew Data Thistle's tokens when the access token has a few days left. True if renewed."""
    from plekstender.db.instance import get_datathistle_tokens
    from plekstender.live.refresh import Stop, usable_datathistle_token

    settings = get_settings()
    async with get_sessionmaker()() as db:
        tokens = await get_datathistle_tokens(db, settings)
    if tokens is None:
        return False
    before = tokens.access
    async with httpx.AsyncClient() as http:
        try:
            await usable_datathistle_token(get_sessionmaker(), http, settings, tokens)
        except Stop as exc:
            log.warning("Data Thistle: %s", exc)
            return False
    return tokens.access != before


@app.periodic(cron="50 4 * * *", periodic_id="renew-datathistle")
@app.task(queue="maintenance", queueing_lock="renew-datathistle")
async def renew_datathistle(timestamp: int) -> None:
    """Daily: keep the Data Thistle tokens alive (each renewal gives a new pair)."""
    await renew_datathistle_tokens()


# --- gigs ---------------------------------------------------------------------------------


@app.task(queue="gigs", lock="nominatim")
async def place_gigs(user_id: int | None = None) -> None:
    """Look up where gigs were (one at a time: Nominatim allows a request a second)."""
    from plekstender.gigs.places import place_gigs as place  # (import cycle)

    async with (
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        get_sessionmaker()() as db,
    ):
        await place(db, http, get_settings().nominatim_url, user_id)


async def enqueue_gig_places(user_id: int) -> None:
    try:
        await place_gigs.configure(
            queueing_lock=f"gig-places-{user_id}", schedule_in={"seconds": 5}
        ).defer_async(user_id=user_id)
    except AlreadyEnqueued:
        pass


@app.periodic(cron="35 * * * *", periodic_id="place-gigs")
@app.task(queue="maintenance", queueing_lock="place-gigs-sweep")
async def place_gigs_sweep(timestamp: int) -> None:
    """Hourly: places for gigs a run didn't get to (or a busy lookup left behind)."""
    try:
        await place_gigs.configure(queueing_lock="gig-places-all").defer_async()
    except AlreadyEnqueued:
        pass


@app.periodic(cron="25 * * * *", periodic_id="artist-countries")
@app.task(queue="maintenance", queueing_lock="artist-countries")
async def find_artist_countries(timestamp: int) -> None:
    """Hourly: where artists without a country in Plex are from, on MusicBrainz (Library →
    Stats map). A few hundred an hour; a big library is done over a day or two."""
    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        done = await look_up_artist_countries(db, http)
    if done:
        log.info("Looked up %d artists' countries on MusicBrainz", done)


@app.periodic(cron="55 * * * *", periodic_id="artist-releases")
@app.task(queue="maintenance", queueing_lock="artist-releases")
async def find_artist_releases(timestamp: int) -> None:
    """Hourly: library artists' releases on MusicBrainz (Library -> Releases), a few hundred
    artists an hour; followed artists daily, the rest every two weeks."""
    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        done = await look_up_releases(db, http)
        # Their covers, kept on disk while they're recent or coming up.
        saved = await release_covers.save_recent(db, http, get_settings())
    if done:
        log.info("Read %d artists' releases from MusicBrainz", done)
    if saved:
        log.info("Saved the covers of %d releases", saved)


@app.periodic(cron="5 * * * *", periodic_id="discover-art")
@app.task(queue="maintenance", queueing_lock="discover-art", lock="discover-art")
async def find_discover_art(timestamp: int | None = None) -> None:
    """Hourly (and when Discover shows artists not looked up yet): fanart.tv artwork for the
    top suggested artists (see plekstender.discover_art)."""
    from plekstender import discover_art

    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        done = await discover_art.fill(db, http, get_settings())
    if done:
        log.info("Looked up artwork for %d suggested artists", done)


async def enqueue_discover_art() -> None:
    try:
        await find_discover_art.configure(queueing_lock="discover-art").defer_async()
    except AlreadyEnqueued:
        pass


@app.periodic(cron="*/10 * * * *", periodic_id="cover-songs")
@app.task(queue="maintenance", queueing_lock="cover-songs", lock="cover-songs")
async def find_cover_songs(timestamp: int | None = None) -> None:
    """Every ten minutes, when an admin turned it on: whether library songs are covers, from
    SecondHandSongs, a few at a time within its rate limits (see plekstender.cover_songs)."""
    from plekstender import cover_songs

    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        done = await cover_songs.run(db, http, get_settings())
    if done:
        log.info("Looked up %d songs on SecondHandSongs", done)


@app.periodic(cron="*/15 * * * *", periodic_id="cover-colours")
@app.task(queue="maintenance", queueing_lock="cover-colours")
async def find_cover_colours(timestamp: int) -> None:
    """Every 15 minutes: the main colour of covers not looked at yet, or changed (Library →
    Stats). The first runs go through a big library; then only new covers."""
    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        done = await colour_covers(db, http, get_settings())
    if done:
        log.info("Worked out %d album covers' colours", done)


@app.task(queue="maintenance", queueing_lock="cover-sizes")
async def measure_cover_sizes() -> None:
    """Measure album covers not measured yet, or changed (Library → Artwork → Album covers).
    Only the start of each image is read."""
    from plekstender.artwork.covers import measure_covers  # (import cycle)

    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        done = await measure_covers(db, http, get_settings())
    if done:
        log.info("Measured %d album covers", done)


async def enqueue_cover_sizes() -> None:
    try:
        await measure_cover_sizes.configure(queueing_lock="cover-sizes").defer_async()
    except AlreadyEnqueued:
        pass


@app.periodic(cron="*/10 * * * *", periodic_id="cover-sizes")
@app.task(queue="maintenance", queueing_lock="cover-sizes-sweep")
async def measure_cover_sizes_sweep(timestamp: int) -> None:
    """Every 10 minutes: measure new or changed album covers."""
    await enqueue_cover_sizes()


@app.periodic(cron="*/5 * * * *", periodic_id="check-notifications")
@app.task(queue="maintenance", queueing_lock="check-notifications")
async def check_notifications(timestamp: int) -> None:
    """Every 5 minutes: raise, update or resolve notifications from every connection's
    status (Phase 17)."""
    from plekstender.notifications import check

    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        await check(db, http, get_settings())


@app.periodic(cron="15 6 * * *", periodic_id="top-10-news")
@app.task(queue="maintenance", queueing_lock="top-10-news")
async def top_10_news(timestamp: int) -> None:
    """Daily: tell people when an artist, album or song entered their all-time top 10s,
    or is the new #1 (top 10s don't change often)."""
    from plekstender.history import top_watch

    async with get_sessionmaker()() as db:
        await top_watch.check(db, dt.datetime.now(dt.UTC))


@app.periodic(cron="* * * * *", periodic_id="heartbeat")
@app.task(queue="maintenance", queueing_lock="heartbeat")
async def heartbeat(timestamp: int) -> None:
    """Lets the web app (and Docker healthcheck) know a worker is alive."""
    async with get_sessionmaker()() as db:
        await record_worker_heartbeat(db)
        await db.commit()


# A worker that stopped (a restart, a new image) leaves its running jobs "doing", holding
# their locks: no other job with the same lock (a lyrics lookup, a scan) could ever start.
# Its heartbeats stop, so after this long its jobs are closed as failed, and so are the app's
# own records of them (scans, edits, syncs...), which would otherwise stay "running".
STALLED_WORKER_SECONDS = 300


@app.periodic(cron="*/5 * * * *", periodic_id="close-stalled-jobs")
@app.task(queue="maintenance", queueing_lock="close-stalled-jobs")
async def close_stalled_jobs(timestamp: int) -> None:
    from procrastinate.jobs import Status

    stalled = list(
        await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=STALLED_WORKER_SECONDS)
    )
    for job in stalled:
        await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
    if stalled:
        log.warning(
            "closed %d jobs left running by a stopped worker: %s",
            len(stalled),
            ", ".join(f"{j.task_name}[{j.id}]" for j in stalled),
        )
    async with get_sessionmaker()() as db:
        closed = await close_abandoned(db)
    if closed:
        log.warning("closed %d jobs a stopped worker left running: %s", len(closed), closed)


@app.periodic(cron="45 * * * *", periodic_id="gig-playlists")
@app.task(queue="maintenance", queueing_lock="gig-playlists", lock="gig-playlists")
async def make_gig_playlists_periodically(timestamp: int) -> None:
    """Hourly: playlists for gigs, for people who turned them on (before a starred gig, and
    after one they went to)."""
    from sqlalchemy import text as sql

    async with get_sessionmaker()() as db:
        user_ids = list(
            await db.scalars(
                sql(
                    "SELECT id FROM users"
                    " WHERE preferences #>> '{gig_playlists,before}' = 'true'"
                    " OR preferences #>> '{gig_playlists,after}' = 'true'"
                    " ORDER BY id"
                )
            )
        )
    for user_id in user_ids:
        await _gig_playlists(user_id)


@app.task(queue="maintenance", lock="gig-playlists")
async def make_gig_playlists_now(user_id: int) -> None:
    """One person's gig playlists, straight away (they've just turned them on)."""
    await _gig_playlists(user_id)


async def enqueue_gig_playlists(user_id: int) -> None:
    try:
        await make_gig_playlists_now.configure(
            queueing_lock=f"gig-playlists-{user_id}"
        ).defer_async(user_id=user_id)
    except AlreadyEnqueued:
        pass


async def _gig_playlists(user_id: int) -> None:
    from plekstender.db.models import User
    from plekstender.gigs.playlists import make_gig_playlists

    async with (
        get_sessionmaker()() as db,
        httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
    ):
        user = await db.get(User, user_id)
        if user is None:
            return
        made = await make_gig_playlists(db, http, get_settings(), user)
        for playlist_id in made.to_build:
            playlist = await db.get(Playlist, playlist_id)
            if playlist is not None:
                await enqueue_playlist_build(playlist)
    if made.made or made.refreshed:
        log.info(
            "gig playlists for user %s: %d made, %d refreshed", user_id, made.made, made.refreshed
        )


@app.periodic(cron="30 4 * * *", periodic_id="delete-old-gig-playlists")
@app.task(queue="maintenance", queueing_lock="old-gig-playlists", lock="gig-playlists")
async def delete_old_gig_playlists_periodically(timestamp: int) -> None:
    """Daily: gig playlists deleted (in Plex and here) for people who want them gone some
    time after the gig."""
    from sqlalchemy import text as sql

    from plekstender.db.models import User
    from plekstender.gigs.playlists import delete_old_gig_playlists

    async with get_sessionmaker()() as db:
        user_ids = list(
            await db.scalars(
                sql(
                    "SELECT id FROM users"
                    " WHERE preferences #>> '{gig_playlists,delete_after}' NOT IN ('0')"
                    " ORDER BY id"
                )
            )
        )
    for user_id in user_ids:
        async with (
            get_sessionmaker()() as db,
            httpx.AsyncClient(headers={"User-Agent": f"Plekstender/{__version__}"}) as http,
        ):
            user = await db.get(User, user_id)
            if user is None:
                continue
            deleted = await delete_old_gig_playlists(db, http, get_settings(), user)
        if deleted:
            log.info("old gig playlists for user %s: %d deleted", user_id, deleted)


@app.periodic(cron="40 3 * * *", periodic_id="prune-history")
@app.task(queue="maintenance", queueing_lock="prune-history")
async def prune_history(timestamp: int) -> None:
    """Daily: finished jobs out of the queue (a week; failed ones a month), and finished
    task records older than 90 days (keeping the latest few of each kind)."""
    from plekstender.maintenance import prune_jobs, prune_records

    await prune_jobs(app)
    async with get_sessionmaker()() as db:
        covers_gone = await release_covers.prune(db, get_settings())
        if covers_gone:
            log.info("deleted %d covers of releases out over 90 days ago", covers_gone)
        removed = await prune_records(db)
        await db.commit()
    if any(removed.values()):
        log.info("pruned old task records: %s", removed)


@app.periodic(cron="17 * * * *", periodic_id="cleanup-sessions")
@app.task(queue="maintenance", queueing_lock="cleanup-sessions")
async def cleanup_sessions(timestamp: int) -> None:
    async with get_sessionmaker()() as db:
        removed = await delete_expired_sessions(db)
        await db.execute(
            update(Playlist)
            .where(
                Playlist.status == "running",
                Playlist.updated_at < dt.datetime.now(dt.UTC) - STALE_AFTER,
            )
            .values(status="error", error="Interrupted (the worker stopped). Build it again.")
        )
        await db.commit()
    remove_old_uploads(get_settings(), dt.timedelta(days=1))
    trim_preview_cache(get_settings())
    remove_old_files(get_settings(), dt.timedelta(days=7))  # artwork exports and imports
    if removed:
        log.info("removed %d expired sessions", removed)
