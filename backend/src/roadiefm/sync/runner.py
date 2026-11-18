"""Library sync: mirrors a user's music libraries from Plex into Postgres.

A sync always runs with one user's own server token, so it records both the shared
metadata and that user's personal state (ratings, plays, and which items they can see).

- Full sync: lists every artist, album and track, fetches details (genres, moods,
  styles, similar artists) for all artists and albums, then removes what's gone. Only a
  server owner's full sync deletes metadata; anyone else's only drops their own
  visibility rows, because a shared user may simply not have access to everything.
- Incremental sync: items whose metadata changed (`updatedAt`) plus items the user
  played or rated (`lastViewedAt` / `lastRatedAt`) since the last sync. If the server
  doesn't support those filters, it falls back to re-reading all personal state.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from roadiefm.config import Settings
from roadiefm.db.models import (
    Album,
    Artist,
    ItemTag,
    LibrarySection,
    SimilarArtist,
    SyncRun,
    Tag,
    Track,
    UserItemState,
    UserSectionSync,
    UserServer,
)
from roadiefm.plex.server import PlexServerClient
from roadiefm.plex.tv import PlexAuthError, PlexError
from roadiefm.security.crypto import DecryptionError, TokenCipher
from roadiefm.sync.parse import (
    PLEX_TYPES,
    ROW_BUILDERS,
    Item,
    similar_of,
    state_row,
    tags_of,
    to_epoch,
)
from roadiefm.sync.popularity import recompute_popularity

log = logging.getLogger(__name__)

PAGE_SIZE = 500
DETAIL_BATCH = 50
DETAIL_CONCURRENCY = 4
# Re-read a little before the last sync started, in case of clock skew or slow writes.
INCREMENTAL_OVERLAP = dt.timedelta(minutes=5)
ITEM_TYPES = ("artist", "album", "track")
MODELS: dict[str, type[Artist] | type[Album] | type[Track]] = {
    "artist": Artist,
    "album": Album,
    "track": Track,
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def upsert_items(
    db: AsyncSession, model: type[Artist] | type[Album] | type[Track], rows: list[dict[str, Any]]
) -> None:
    if not rows:
        return
    stmt = insert(model).values(rows)
    columns = rows[0].keys() - {"server_id", "rating_key"}
    stmt = stmt.on_conflict_do_update(
        index_elements=["server_id", "rating_key"],
        set_={c: stmt.excluded[c] for c in columns},
    )
    await db.execute(stmt)


async def upsert_states(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = insert(UserItemState).values(rows)
    columns = rows[0].keys() - {"user_id", "server_id", "rating_key"}
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "server_id", "rating_key"],
        set_={c: stmt.excluded[c] for c in columns},
    )
    await db.execute(stmt)


@dataclass
class Sync:
    db: AsyncSession
    client: PlexServerClient
    run: SyncRun
    us: UserServer
    started: dt.datetime
    full_requested: bool
    stats: Counter[str] = field(default_factory=Counter)
    modes: dict[str, str] = field(default_factory=dict)
    _tag_ids: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def server_id(self) -> int:
        return self.us.server_id

    # --- bookkeeping ----------------------------------------------------------------

    async def _progress(self, section: str, phase: str, done: int, total: int) -> None:
        self.run.progress = {"section": section, "phase": phase, "done": done, "total": total}
        self.run.updated_at = utcnow()
        await self.db.commit()

    async def _user_section(self, section: str) -> UserSectionSync:
        row = await self.db.get(UserSectionSync, (self.us.id, section))
        if row is None:
            row = UserSectionSync(user_server_id=self.us.id, section_key=section)
            self.db.add(row)
        return row

    async def _library_section(self, section: str) -> LibrarySection:
        row = await self.db.get(LibrarySection, (self.server_id, section))
        if row is None:
            row = LibrarySection(server_id=self.server_id, section_key=section)
            self.db.add(row)
        return row

    # --- listing ----------------------------------------------------------------------

    async def _pages(
        self, section: str, item_type: str, filters: dict[str, Any] | None
    ) -> AsyncIterator[tuple[list[Item], int, int]]:
        start = 0
        while True:
            items, total = await self.client.list_items(
                section, PLEX_TYPES[item_type], start=start, size=PAGE_SIZE, filters=filters
            )
            if not items:
                return
            start += len(items)
            yield items, start, total
            if start >= total:
                return

    async def _store(self, section: str, item_type: str, items: list[Item]) -> list[int]:
        build = ROW_BUILDERS[item_type]
        rows: dict[int, dict[str, Any]] = {}
        states: dict[int, dict[str, Any]] = {}
        for item in items:
            if "ratingKey" not in item:
                continue
            row = build(item, self.server_id, section, self.started)
            rows[row["rating_key"]] = row
            states[row["rating_key"]] = state_row(
                item,
                item_type=item_type,
                user_id=self.us.user_id,
                server_id=self.server_id,
                section_key=section,
                seen=self.started,
            )
        await upsert_items(self.db, MODELS[item_type], list(rows.values()))
        await upsert_states(self.db, list(states.values()))
        self.stats[f"{item_type}s"] += len(rows)
        return list(rows)

    async def _list_and_store(
        self, section: str, item_type: str, filters: dict[str, Any] | None, phase: str
    ) -> set[int]:
        keys: set[int] = set()
        async for items, done, total in self._pages(section, item_type, filters):
            keys.update(await self._store(section, item_type, items))
            await self._progress(section, phase, done, total)
        return keys

    # --- details: tags and similar artists ---------------------------------------------

    async def _tag_ids_for(self, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], int]:
        missing = sorted({p for p in pairs if p not in self._tag_ids})
        if missing:
            values = insert(Tag).values(
                [{"server_id": self.server_id, "kind": k, "name": n} for k, n in missing]
            )
            # DO UPDATE (not DO NOTHING) so RETURNING also yields already-existing tags.
            upsert = values.on_conflict_do_update(
                index_elements=["server_id", "kind", "name"], set_={"name": values.excluded.name}
            ).returning(Tag.id, Tag.kind, Tag.name)
            for tag_id, kind, name in (await self.db.execute(upsert)).all():
                self._tag_ids[(kind, name)] = tag_id
        return self._tag_ids

    async def _store_details(self, item_type: str, items: dict[int, Item]) -> None:
        if not items:
            return
        keys = list(items)
        await self.db.execute(
            delete(ItemTag).where(ItemTag.server_id == self.server_id, ItemTag.rating_key.in_(keys))
        )
        tagged = {key: tags_of(item) for key, item in items.items()}
        ids = await self._tag_ids_for(p for pairs in tagged.values() for p in pairs)
        tag_rows = [
            {"server_id": self.server_id, "rating_key": key, "tag_id": ids[pair]}
            for key, pairs in tagged.items()
            for pair in pairs
        ]
        if tag_rows:
            await self.db.execute(insert(ItemTag).values(tag_rows).on_conflict_do_nothing())

        if item_type == "artist":
            await self.db.execute(
                delete(SimilarArtist).where(
                    SimilarArtist.server_id == self.server_id,
                    SimilarArtist.artist_rating_key.in_(keys),
                )
            )
            similar_rows = [
                {
                    "server_id": self.server_id,
                    "artist_rating_key": key,
                    "position": pos,
                    "name": name,
                }
                for key, item in items.items()
                for pos, name in enumerate(similar_of(item))
            ]
            if similar_rows:
                await self.db.execute(insert(SimilarArtist).values(similar_rows))

    async def _fetch_details(self, section: str, item_type: str, keys: list[int]) -> None:
        if not keys:
            return
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        batches = [keys[i : i + DETAIL_BATCH] for i in range(0, len(keys), DETAIL_BATCH)]

        async def fetch(batch: list[int]) -> dict[int, Item]:
            wanted = set(batch)
            async with sem:
                found = {int(i["ratingKey"]): i for i in await self.client.metadata(batch)}
                # Items deleted meanwhile, or a server that only answers single keys.
                for key in batch:
                    if key not in found:
                        try:
                            for item in await self.client.metadata([key]):
                                found[int(item["ratingKey"])] = item
                        except PlexError as exc:
                            if exc.status != 404:
                                raise
                return {k: v for k, v in found.items() if k in wanted}

        done = 0
        step = DETAIL_CONCURRENCY * 4
        for i in range(0, len(batches), step):
            chunk = batches[i : i + step]
            for items in await asyncio.gather(*(fetch(b) for b in chunk)):
                await self._store_details(item_type, items)
            done += sum(len(b) for b in chunk)
            await self._progress(section, f"{item_type}_details", done, len(keys))

    # --- incremental: personal state that doesn't touch updatedAt ----------------------

    async def _state_changes(self, section: str, since: dt.datetime) -> None:
        stamp = to_epoch(since)
        try:
            for item_type in ITEM_TYPES:
                for field_name in ("lastViewedAt>>", "lastRatedAt>>"):
                    await self._list_and_store(
                        section, item_type, {field_name: stamp}, f"{item_type}_activity"
                    )
        except PlexError as exc:
            if exc.status != 400:
                raise
            log.info("server doesn't support activity filters; re-reading all state")
            for item_type in ITEM_TYPES:
                await self._list_and_store(section, item_type, None, f"{item_type}s")

    # --- removal of deleted items -------------------------------------------------------

    async def _sweep(self, section: str, library_section: LibrarySection) -> None:
        sid = self.server_id
        await self.db.execute(
            delete(UserItemState).where(
                UserItemState.user_id == self.us.user_id,
                UserItemState.server_id == sid,
                UserItemState.section_key == section,
                UserItemState.seen_at < self.started,
            )
        )
        if not self.us.is_owner:
            return
        for model in (Track, Album, Artist):
            stale = select(model.rating_key).where(
                model.server_id == sid,
                model.section_key == section,
                model.last_seen_at < self.started,
            )
            await self.db.execute(
                delete(ItemTag).where(ItemTag.server_id == sid, ItemTag.rating_key.in_(stale))
            )
            await self.db.execute(
                delete(UserItemState).where(
                    UserItemState.server_id == sid, UserItemState.rating_key.in_(stale)
                )
            )
            if model is Artist:
                await self.db.execute(
                    delete(SimilarArtist).where(
                        SimilarArtist.server_id == sid,
                        SimilarArtist.artist_rating_key.in_(stale),
                    )
                )
            result = await self.db.execute(
                delete(model).where(
                    model.server_id == sid,
                    model.section_key == section,
                    model.last_seen_at < self.started,
                )
            )
            self.stats[f"{model.__tablename__}_removed"] += int(getattr(result, "rowcount", 0) or 0)
        await self.db.execute(
            delete(Tag).where(
                Tag.server_id == sid,
                ~exists().where(ItemTag.tag_id == Tag.id),
            )
        )
        library_section.last_owner_full_sync_at = self.started

    # --- one library section -------------------------------------------------------------

    async def sync_section(self, section: str) -> None:
        user_section = await self._user_section(section)
        library_section = await self._library_section(section)
        since = user_section.last_sync_started_at
        full = self.full_requested or since is None
        self.modes[section] = "full" if full else "incremental"
        filters = (
            None
            if full or since is None
            else {"updatedAt>>": to_epoch(since - INCREMENTAL_OVERLAP)}
        )

        detail_keys: dict[str, set[int]] = {"artist": set(), "album": set()}
        for item_type in ITEM_TYPES:
            keys = await self._list_and_store(section, item_type, filters, f"{item_type}s")
            if item_type in detail_keys:
                detail_keys[item_type] |= keys

        if not full and since is not None:
            await self._state_changes(section, since - INCREMENTAL_OVERLAP)

        for item_type in ("artist", "album"):
            await self._fetch_details(section, item_type, sorted(detail_keys[item_type]))

        if full:
            await self._sweep(section, library_section)
            user_section.last_full_sync_at = self.started
        user_section.last_sync_started_at = self.started
        await self.db.commit()


def describe_error(exc: BaseException) -> str:
    """A message for the UI. Never includes tokens or URLs."""
    if isinstance(exc, (PlexAuthError, DecryptionError)):
        return "Plex rejected the access token. Reconnect this server in Settings."
    if isinstance(exc, httpx.TimeoutException):
        return "The Plex server took too long to answer."
    if isinstance(exc, httpx.HTTPError):
        return "Couldn't reach the Plex server. Try Reconnect in Settings."
    if isinstance(exc, PlexError):
        return f"Plex returned an error ({exc.status or 'invalid response'})."
    return f"Unexpected error ({type(exc).__name__})."


async def run_sync(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, run_id: int
) -> None:
    run = await db.get(SyncRun, run_id)
    if run is None or run.status != "queued":
        log.info("sync run %s is not queued; skipping", run_id)
        return
    us = await db.get(UserServer, run.user_server_id)
    if us is None:
        return  # cascade-deleted with the server connection

    started = utcnow()
    run.status = "running"
    run.started_at = started
    run.updated_at = started
    await db.commit()

    sync: Sync | None = None
    try:
        cipher = TokenCipher(settings.derive_key("token-encryption"))
        token = cipher.decrypt(us.access_token_enc, purpose="user_server.access_token")
        client = PlexServerClient(settings, http, us.connection_uri, token)
        sync = Sync(db, client, run, us, started=started, full_requested=run.kind == "full")
        for section in list(us.music_section_keys):
            await sync.sync_section(section)
        await recompute_popularity(db, us.server_id)
        run.status = "succeeded"
        run.error = None
    except Exception as exc:  # report every failure on the run, then stop
        log.exception("sync run %s failed", run_id)
        await db.rollback()
        failed = await db.get(SyncRun, run_id, populate_existing=True)
        if failed is None:
            return
        run = failed
        run.status = "failed"
        run.error = describe_error(exc)

    finished = utcnow()
    run.finished_at = finished
    run.updated_at = finished
    if sync is not None:
        run.stats = {**dict(sync.stats), "sections": dict(sync.modes)}
    await db.commit()
    log.info("sync run %s %s in %s", run_id, run.status, finished - started)
