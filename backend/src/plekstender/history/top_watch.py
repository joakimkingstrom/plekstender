"""Telling people when their all-time top 10s change: an artist, album or song entering its
top 10, or a new #1 (notifications in the bell, linked to History's all-time overview).

Each top 10 is kept as last seen (TopSeen) and worked out again when there are newer
listens. The first time only notes it, so nobody gets thirty notifications at once; entries
are told about once there are ten to enter among; and many entering together (an import)
make one notification. These notifications go by themselves after a while.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Listen, Notification, NotificationState, TopSeen, User
from plekstender.history import stats
from plekstender.notifications import Problem, raise_problem

Kind = Literal["artist", "album", "track"]
KINDS: tuple[Kind, ...] = ("artist", "album", "track")
WORDS = {"artist": ("artist", "artists"), "album": ("album", "albums"), "track": ("song", "songs")}
TOP = 10
PREFIX = "top:"  # + user id: kind: …
KEEP = dt.timedelta(days=14)
LINK = "/history?range=all"
ONE_BY_ONE = 3  # more entering at once than this: one notification for them all


def _listens(n: int) -> str:
    return f"{n:,} {'listen' if n == 1 else 'listens'}"


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _called(item: dict[str, Any]) -> str:
    """ "Radiohead"; "OK Computer by Radiohead"."""
    return f"{item['name']} by {item['artist']}" if item.get("artist") else str(item["name"])


async def _tell(db: AsyncSession, problem: Problem, now: dt.datetime) -> None:
    """Raise it as news, unread (again, if the same thing was told recently)."""
    note = await raise_problem(db, problem, now)
    note.first_at = note.updated_at = now
    await db.execute(delete(NotificationState).where(NotificationState.notification_id == note.id))


async def _check_kind(
    db: AsyncSession, user_id: int, kind: Kind, latest: int, now: dt.datetime
) -> int:
    seen = await db.get(TopSeen, (user_id, kind))
    if seen is not None and seen.last_listen_id == latest:
        return 0
    tops = await stats.top(db, user_id, kind, None, now, TOP)
    current: list[dict[str, Any]] = [
        {
            "key": "/".join(str(k) for k in t.keys),
            "name": t.name,
            "artist": t.artist,
            "listens": t.listens,
        }
        for t in tops
    ]
    if seen is None:  # the first look: only note it
        db.add(
            TopSeen(
                user_id=user_id, kind=kind, items=current, last_listen_id=latest, updated_at=now
            )
        )
        return 0
    one, many = WORDS[kind]
    before = [i["key"] for i in seen.items]
    told = 0
    new_first = bool(before) and bool(current) and current[0]["key"] != before[0]
    if new_first:
        first, was = current[0], seen.items[0]
        await _tell(
            db,
            Problem(
                key=f"{PREFIX}{user_id}:{kind}:first:{first['key']}",
                user_id=user_id,
                level="info",
                title=f"{_called(first)} is your new #1 {one}",
                detail=(
                    f"Taking over from {_called(was)}, "
                    f"with {_listens(first['listens'])} of all time."
                ),
                link=LINK,
            ),
            now,
        )
        told += 1
    entered = [
        (rank, i)
        for rank, i in enumerate(current, 1)
        if i["key"] not in before and not (new_first and rank == 1)
    ]
    if entered and len(before) >= TOP:  # with fewer, everything new would "enter"
        if len(entered) <= ONE_BY_ONE:
            for rank, i in entered:
                await _tell(
                    db,
                    Problem(
                        key=f"{PREFIX}{user_id}:{kind}:top10:{i['key']}",
                        user_id=user_id,
                        level="info",
                        title=f"{_called(i)} is in your top 10 {many}",
                        detail=f"#{rank} of all time, with {_listens(i['listens'])}.",
                        link=LINK,
                    ),
                    now,
                )
                told += 1
        else:
            await _tell(
                db,
                Problem(
                    key=f"{PREFIX}{user_id}:{kind}:top10:{now.isoformat()}",
                    user_id=user_id,
                    level="info",
                    title=f"{len(entered)} {many} are new in your top 10",
                    detail=_names([_called(i) for _, i in entered]),
                    link=LINK,
                ),
                now,
            )
            told += 1
    seen.items = current
    seen.last_listen_id = latest
    seen.updated_at = now
    return told


async def check_user(db: AsyncSession, user_id: int, now: dt.datetime) -> int:
    """Compare each top 10 with the one last seen and tell what changed. Returns how many
    notifications were raised."""
    latest = await db.scalar(select(func.max(Listen.id)).where(Listen.user_id == user_id))
    if latest is None:
        return 0
    return sum([await _check_kind(db, user_id, kind, latest, now) for kind in KINDS])


async def check(db: AsyncSession, now: dt.datetime) -> int:
    """Everyone (nothing to do for those without newer listens); old top-10 news goes."""
    await db.execute(
        delete(Notification).where(
            Notification.key.startswith(PREFIX, autoescape=True),
            Notification.updated_at < now - KEEP,
        )
    )
    told = 0
    for user_id in await db.scalars(select(User.id)):
        told += await check_user(db, user_id, now)
    await db.commit()
    return told
