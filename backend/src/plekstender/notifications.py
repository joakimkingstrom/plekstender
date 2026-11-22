"""Notifications: telling people when something they rely on stops working or nears its
limit, before they notice it themselves; and news (level "info") such as new gigs or top-10
changes, raised by those features (live.refresh, history.top_watch).

The worker looks at every connection's status every few minutes (the same view as Home's
Status card): each one that's amber or red is a notification, raised once per problem.
Repeats update it rather than piling up, a worse level makes it unread again, and it's
resolved by itself when the problem goes away. People's own connections (servers, scrobble
services, calendar links) notify them; the instance's (service keys, limits, SearXNG) notify
admins.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import Notification, NotificationState, User

LEVELS = {"info": 0, "warning": 1, "error": 2}
# The connection states (Home's Status card) that are problems to tell people about.
PROBLEM_STATES = frozenset({"warning", "error"})
NEW_GIGS = "gigs:new:"  # + user id


@dataclass(frozen=True)
class Problem:
    key: str
    user_id: int | None  # None: the instance's, for admins
    level: str  # warning | error (problems) | info (news, such as new gigs)
    title: str
    detail: str | None
    link: str | None


def problem_title(name: str, level: str) -> str:
    return f"{name} isn't working" if level == "error" else f"{name} needs a look"


async def raise_problem(db: AsyncSession, problem: Problem, now: dt.datetime) -> Notification:
    """Raise a problem, or update the notification it already has."""
    found = await db.scalar(select(Notification).where(Notification.key == problem.key))
    if found is None:
        found = Notification(
            key=problem.key,
            user_id=problem.user_id,
            level=problem.level,
            title=problem.title,
            detail=problem.detail,
            link=problem.link,
            first_at=now,
            updated_at=now,
        )
        db.add(found)
        await db.flush()
        return found
    fresh = found.resolved_at is not None
    worse = LEVELS.get(problem.level, 0) > LEVELS.get(found.level, 0)
    changed = (found.level, found.title, found.detail, found.link) != (
        problem.level,
        problem.title,
        problem.detail,
        problem.link,
    )
    if fresh:
        # Back again after being fixed: a new notification as far as anyone's concerned.
        found.first_at = now
        found.resolved_at = None
        found.hidden_at = None
    if fresh or worse:
        await db.execute(
            delete(NotificationState).where(NotificationState.notification_id == found.id)
        )
    if fresh or changed:
        found.level, found.title = problem.level, problem.title
        found.detail, found.link = problem.detail, problem.link
        found.updated_at = now
    return found


async def resolve_others(db: AsyncSession, prefix: str, active: set[str], now: dt.datetime) -> int:
    """Resolve the open notifications under `prefix` whose problem has gone away."""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.key.startswith(prefix, autoescape=True),
            Notification.resolved_at.is_(None),
            Notification.key.not_in(active) if active else Notification.id.is_not(None),
        )
        .values(resolved_at=now, updated_at=now)
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


async def check(db: AsyncSession, http: httpx.AsyncClient, settings: Settings) -> int:
    """Raise and resolve notifications from every connection's status. Returns how many
    problems are open."""
    from plekstender.api.routes.statuses import instance_connections, your_connections

    now = dt.datetime.now(dt.UTC)
    open_problems = 0
    for user_id in list(await db.scalars(select(User.id))):
        problems = [
            Problem(
                key=f"user:{user_id}:{c.name}",
                user_id=user_id,
                level=c.state,
                title=problem_title(c.name, c.state),
                detail=c.detail,
                link=c.link,
            )
            for c in await your_connections(db, user_id)
            if c.state in PROBLEM_STATES
        ]
        for p in problems:
            await raise_problem(db, p, now)
        await resolve_others(db, f"user:{user_id}:", {p.key for p in problems}, now)
        open_problems += len(problems)
    instance = [
        Problem(
            key=f"instance:{c.name}",
            user_id=None,
            level=c.state,
            title=problem_title(c.name, c.state),
            detail=c.detail,
            link=c.link,
        )
        for c in await instance_connections(db, http, settings, admin=True)
        if c.state in PROBLEM_STATES
    ]
    for p in instance:
        await raise_problem(db, p, now)
    await resolve_others(db, "instance:", {p.key for p in instance}, now)
    await db.commit()
    return open_problems + len(instance)


async def notify_new_gigs(
    db: AsyncSession, user_id: int, now: dt.datetime, found_more: bool
) -> None:
    """After a gig refresh: one notification for the gigs first found in the past week (what
    Gigs → Upcoming → New lists). It's made when a refresh finds new gigs, kept up to date
    after each refresh, unread again whenever more are found, and gone when none are new."""
    from plekstender.api.routes.live import _upcoming  # the gigs Upcoming lists, merged

    new = [g for g in await _upcoming(db, user_id) if g.is_new]
    key = f"{NEW_GIGS}{user_id}"
    found = await db.scalar(select(Notification).where(Notification.key == key))
    if not new:
        if found is not None:
            await db.delete(found)
        return
    if found is None and not found_more:
        return
    names = list(
        dict.fromkeys(
            (g.name if g.festival and g.name else g.artists[0].name if g.artists else g.name) or "?"
            for g in new
        )
    )
    detail = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    note = await raise_problem(
        db,
        Problem(
            key=key,
            user_id=user_id,
            level="info",
            title=f"{len(new)} new {'gig' if len(new) == 1 else 'gigs'}",
            detail=detail,
            link="/gigs?show=new",
        ),
        now,
    )
    if found_more and found is not None:  # more found: new again for them
        note.updated_at = now
        await db.execute(
            delete(NotificationState).where(NotificationState.notification_id == note.id)
        )
