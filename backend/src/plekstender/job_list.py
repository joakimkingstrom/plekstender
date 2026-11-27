"""Every kind of long job, in one list (Settings → Jobs): library syncs, artwork jobs, music
folder jobs (scans, identifying, renames, lyrics, duplicates), edits being written, and
history imports and sends. Each says what it is, how far it got, and, when it's waiting,
what for: the job queue (procrastinate) runs one job at a time per lock (one music folder
job per server, for instance), so a queued job can be behind another.

Jobs a worker left "running" when it stopped (a restart, a new image) are closed here too
(`close_abandoned`), so they don't show as running, or stopping, for ever.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import (
    ArtworkJob,
    FileScan,
    ListenImport,
    SyncRun,
    TagChange,
    TagChangeSet,
    UserServer,
)
from plekstender.jobs import NO_WORKER_AFTER
from plekstender.tags import apply
from plekstender.tags.lyrics import close_edit as close_lyrics_edit

FINISHED_LISTED = 50
ACTIVE = ("queued", "running")

# Worker tasks that stand for one of the app's jobs: (kind, the argument with its id).
TASKS: dict[str, tuple[str, str]] = {
    "plekstender.worker.sync_library": ("sync", "run_id"),
    "plekstender.worker.artwork_job": ("artwork", "job_id"),
    "plekstender.worker.scan_music_files": ("files", "scan_id"),
    "plekstender.worker.identify_music_files": ("files", "job_id"),
    "plekstender.worker.plan_renames": ("files", "job_id"),
    "plekstender.worker.find_duplicates": ("files", "job_id"),
    "plekstender.worker.look_up_lyrics": ("files", "job_id"),
    "plekstender.worker.apply_tag_changes": ("edits", "set_id"),
    "plekstender.worker.import_lastfm": ("imports", "import_id"),
    "plekstender.worker.import_listenbrainz": ("imports", "import_id"),
    "plekstender.worker.send_listenbrainz_history": ("imports", "import_id"),
}
# Other tasks that can hold a lock a job waits for.
TASK_NAMES = {
    "plekstender.worker.save_artist_folder": "Saving an artist image to its folder",
    "plekstender.worker.check_artist_folders": "Checking the artist folders' images",
    "plekstender.worker.capture_plex_plays": "Reading new plays from Plex",
    "plekstender.worker.build_playlist": "Building a playlist in Plex",
}

FILE_JOBS = {
    "scan": "Reading the music files' tags",
    "identify": "Identifying files by sound",
    "rename": "Working out new file names",
    "lyrics": "Looking up lyrics",
    "dupes": "Finding duplicate files",
}
ARTWORK_JOBS = {
    "fanart": "Artist logos from fanart.tv",
    "export": "Exporting a logo pack",
    "import": "Importing a logo pack",
    "covers": "Best matches for album covers",
}
IMPORTS = {
    "lastfm": "Importing plays from Last.fm",
    "listenbrainz": "Importing plays from ListenBrainz",
}


@dataclass
class Waiting:
    what: str
    since: dt.datetime | None


@dataclass
class Job:
    kind: str  # sync | artwork | files | edits | imports
    id: int
    what: str
    status: str  # the job's own: queued, running, succeeded, failed, applied, partial...
    server_id: int | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    progress: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    stop_requested: str | None = None
    subkind: str | None = None
    waiting_for: Waiting | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    @property
    def stoppable(self) -> bool:
        # Lyrics lookups stop from their own page (they also write as they go).
        return self.kind in ("artwork", "files", "edits", "imports") and self.subkind != "lyrics"


def _sync(run: SyncRun, server_id: int | None) -> Job:
    return Job(
        "sync", run.id, f"Library sync ({run.kind})", run.status, server_id, run.created_at,
        run.started_at, run.finished_at, dict(run.progress or {}), run.error,
    )  # fmt: skip


def _artwork(job: ArtworkJob) -> Job:
    return Job(
        "artwork", job.id, ARTWORK_JOBS.get(job.kind, job.kind), job.status, job.server_id,
        job.created_at, job.started_at, job.finished_at, dict(job.progress or {}), job.error,
        job.stop_requested, job.kind,
    )  # fmt: skip


def _files(job: FileScan) -> Job:
    return Job(
        "files", job.id, FILE_JOBS.get(job.kind, job.kind), job.status, job.server_id,
        job.created_at, job.started_at, job.finished_at, dict(job.progress or {}), job.error,
        job.stop_requested, job.kind,
    )  # fmt: skip


def _import(job: ListenImport) -> Job:
    what = IMPORTS.get(job.source, "Sending your history to ListenBrainz")
    return Job(
        "imports", job.id, what, job.status, None, job.created_at, job.started_at,
        job.finished_at, dict(job.progress or {}), job.error, job.stop_requested, job.source,
    )  # fmt: skip


async def _edits(db: AsyncSession, sets: list[TagChangeSet]) -> list[Job]:
    """Edits, with how many of their changes are done (applied, or found to conflict)."""
    counts: dict[int, dict[str, int]] = {}
    if sets:
        rows = await db.execute(
            select(TagChange.change_set_id, TagChange.status, func.count())
            .where(TagChange.change_set_id.in_([s.id for s in sets]))
            .group_by(TagChange.change_set_id, TagChange.status)
        )
        for set_id, status, n in rows.all():
            counts.setdefault(set_id, {})[status] = n
    out = []
    for s in sets:
        c = counts.get(s.id, {})
        total = sum(c.values())
        done = total - c.get("pending", 0)
        out.append(
            Job(
                kind="edits",
                id=s.id,
                what=s.description,
                status=s.status,
                server_id=s.server_id,
                created_at=s.created_at,
                started_at=s.created_at if s.status != "queued" else None,
                finished_at=s.applied_at if s.status not in ACTIVE else None,
                progress={"done": done, "total": total},
                error=s.error,
                stop_requested=s.stop_requested,
                subkind=s.kind,
            )
        )
    return out


async def jobs_for(db: AsyncSession, user_id: int) -> tuple[list[Job], list[Job]]:
    """The user's jobs (their own syncs and imports, and the jobs on servers they own):
    those running or waiting, and the latest finished."""
    connections = list(await db.scalars(select(UserServer).where(UserServer.user_id == user_id)))
    server_of = {us.id: us.server_id for us in connections}
    owned = [us.server_id for us in connections if us.is_owner]

    found: list[Job] = []

    async def latest(model: Any, *where: Any, order: Any) -> list[Any]:
        active = list(await db.scalars(select(model).where(*where, model.status.in_(ACTIVE))))
        done = list(
            await db.scalars(
                select(model)
                .where(*where, model.status.not_in(ACTIVE))
                .order_by(order.desc())
                .limit(FINISHED_LISTED)
            )
        )
        return active + done

    if server_of:
        for run in await latest(
            SyncRun, SyncRun.user_server_id.in_(list(server_of)), order=SyncRun.id
        ):
            found.append(_sync(run, server_of.get(run.user_server_id)))
    if owned:
        found += [
            _artwork(j)
            for j in await latest(ArtworkJob, ArtworkJob.server_id.in_(owned), order=ArtworkJob.id)
        ]
        found += [
            _files(j)
            for j in await latest(FileScan, FileScan.server_id.in_(owned), order=FileScan.id)
        ]
        found += await _edits(
            db,
            await latest(TagChangeSet, TagChangeSet.server_id.in_(owned), order=TagChangeSet.id),
        )
    found += [
        _import(j)
        for j in await latest(ListenImport, ListenImport.user_id == user_id, order=ListenImport.id)
    ]

    active = sorted(
        (j for j in found if j.active), key=lambda j: (j.status != "running", j.created_at)
    )
    finished = sorted(
        (j for j in found if not j.active),
        key=lambda j: j.finished_at or j.created_at,
        reverse=True,
    )[:FINISHED_LISTED]
    await add_waiting(db, active)
    return active, finished


@dataclass
class _Queued:
    id: int
    task: str
    args: dict[str, Any]
    status: str
    lock: str | None
    started_at: dt.datetime | None
    # Running on a worker that has stopped (its heartbeats did): the queue closes it soon.
    orphaned: bool = False


_QUEUE = text(
    """
    SELECT j.id, j.task_name, j.args, j.status::text, j.lock,
           (SELECT min(e.at) FROM procrastinate_events e
             WHERE e.job_id = j.id AND e.type = 'started') AS started_at,
           j.status = 'doing'
             AND (w.id IS NULL OR w.last_heartbeat < now() - interval '1 minute') AS orphaned
    FROM procrastinate_jobs j
    LEFT JOIN procrastinate_workers w ON w.id = j.worker_id
    WHERE j.status IN ('todo', 'doing')
    ORDER BY j.id
    """
)


def _describe(entry: _Queued, jobs: dict[tuple[str, int], Job]) -> str:
    kind, arg = TASKS.get(entry.task, ("", ""))
    if kind and isinstance(entry.args.get(arg), int) and (kind, entry.args[arg]) in jobs:
        return jobs[(kind, entry.args[arg])].what
    return TASK_NAMES.get(entry.task, entry.task.rsplit(".", 1)[-1].replace("_", " ").capitalize())


async def add_waiting(db: AsyncSession, active: list[Job]) -> None:
    """For each queued job, what it waits for: the job holding the same lock (running, or
    queued before it), or a free place in the worker."""
    queued = [j for j in active if j.status == "queued"]
    if not queued:
        return
    entries = [_Queued(*row) for row in (await db.execute(_QUEUE)).all()]
    jobs = {(j.kind, j.id): j for j in active}
    for job in queued:
        entry = next(
            (
                e
                for e in entries
                if TASKS.get(e.task, ("", ""))[0] == job.kind
                and e.args.get(TASKS[e.task][1]) == job.id
            ),
            None,
        )
        if entry is None or entry.status != "todo":
            continue
        ahead = [
            e
            for e in entries
            if e.lock and e.lock == entry.lock and e.id != entry.id
            and (e.status == "doing" or e.id < entry.id)
        ]  # fmt: skip
        first = next((e for e in ahead if e.status == "doing"), ahead[0] if ahead else None)
        if first is not None and first.orphaned:
            job.waiting_for = Waiting(
                f"{_describe(first, jobs)}, left unfinished by a worker that stopped"
                " (it's cleared within 5 minutes)",
                None,
            )
        elif first is not None:
            job.waiting_for = Waiting(_describe(first, jobs), first.started_at)
        elif any(e.status == "doing" for e in entries):
            job.waiting_for = Waiting(
                "A free place in the worker (it's busy with other jobs)", None
            )
        else:
            job.waiting_for = Waiting("The worker to pick it up (is it running?)", None)


# --- jobs whose worker stopped ----------------------------------------------------------------

INTERRUPTED = {
    "sync": "Interrupted (the worker stopped during the sync).",
    "imports": "Interrupted (the worker stopped). Resume to continue.",
    "files": "Interrupted (the worker stopped). Start it again.",
    "artwork": "Interrupted (the worker stopped). Start it again.",
}
MODELS: dict[str, Any] = {
    "sync": SyncRun,
    "imports": ListenImport,
    "files": FileScan,
    "artwork": ArtworkJob,
    "edits": TagChangeSet,
}


async def in_worker(db: AsyncSession) -> set[tuple[str, int]]:
    """The jobs a worker is running now, as (kind, id)."""
    rows = await db.execute(
        text("SELECT task_name, args FROM procrastinate_jobs WHERE status = 'doing'")
    )
    found = set()
    for task, args in rows.all():
        kind, arg = TASKS.get(task, ("", ""))
        if kind and isinstance((args or {}).get(arg), int):
            found.add((kind, args[arg]))
    return found


async def close_abandoned(db: AsyncSession, now: dt.datetime | None = None) -> list[str]:
    """Close the jobs left "running" that no worker runs any more (and that haven't moved on
    for a while): failed, saying they were interrupted. An edit's unwritten changes are marked
    as such (it's partial if some were written), and so is a lyrics lookup's edit. Returns
    the jobs closed, as "kind id"."""
    now = now or dt.datetime.now(dt.UTC)
    running = await in_worker(db)
    closed: list[str] = []
    for kind, model in MODELS.items():
        left = [
            job
            for job in await db.scalars(
                select(model).where(
                    model.status == "running", model.updated_at < now - NO_WORKER_AFTER
                )
            )
            if (kind, job.id) not in running
        ]
        if kind == "edits":
            # A lyrics lookup writes its own edit as it goes: that one ends with the lookup.
            lookups = await db.scalars(
                select(FileScan.params).where(
                    FileScan.kind == "lyrics", FileScan.status.in_(ACTIVE)
                )
            )
            owned = {p.get("applied_set_id") for p in lookups}
            left = [s for s in left if s.id not in owned]
        for job in left:
            await _close(db, kind, job, now)
            closed.append(f"{kind} {job.id}")
    return closed


async def _close(db: AsyncSession, kind: str, job: Any, now: dt.datetime) -> None:
    if kind == "edits":
        unwritten = await db.execute(
            update(TagChange)
            .where(TagChange.change_set_id == job.id, TagChange.status == "pending")
            .values(status="failed", error="Not written: the worker stopped first.")
        )
        n = int(getattr(unwritten, "rowcount", 0) or 0)
        job.error = (
            f"Interrupted (the worker stopped): {n} "
            f"{'change was' if n == 1 else 'changes were'} not written."
        )
        await apply.finish(db, job, job.id)  # failed, or partial if some were written
        return
    job.status = "failed"
    job.error = INTERRUPTED[kind]
    job.finished_at = job.updated_at = now
    await db.commit()
    if kind == "files" and job.kind == "lyrics" and (set_id := job.params.get("applied_set_id")):
        await close_lyrics_edit(db, job, set_id)
