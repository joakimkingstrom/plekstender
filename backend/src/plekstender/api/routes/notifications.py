"""The bell: each person's notifications (and admins' about the instance)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from plekstender.api.deps import DbDep, UserDep
from plekstender.api.schemas import NotificationOut, NotificationsOut, NotificationsReadIn
from plekstender.db.models import Notification, NotificationState, User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# Fixed problems stay listed (as fixed) this long.
KEEP_FIXED = dt.timedelta(days=3)


def _theirs(user: User) -> ColumnElement[bool]:
    mine = Notification.user_id == user.id
    return or_(mine, Notification.user_id.is_(None)) if user.is_admin else mine


async def _visible(db: DbDep, user: User) -> list[tuple[Notification, NotificationState | None]]:
    since = dt.datetime.now(dt.UTC) - KEEP_FIXED
    rows = await db.execute(
        select(Notification, NotificationState)
        .outerjoin(
            NotificationState,
            and_(
                NotificationState.notification_id == Notification.id,
                NotificationState.user_id == user.id,
            ),
        )
        .where(
            _theirs(user),
            Notification.hidden_at.is_(None),
            or_(Notification.resolved_at.is_(None), Notification.resolved_at > since),
            or_(NotificationState.dismissed_at.is_(None), NotificationState.user_id.is_(None)),
        )
        .order_by(Notification.resolved_at.is_not(None), Notification.updated_at.desc())
    )
    return [(n, s) for n, s in rows.all()]


@router.get("", response_model=NotificationsOut)
async def list_notifications(db: DbDep, user: UserDep) -> NotificationsOut:
    """Open problems first (newest first), then ones fixed in the last few days."""
    found = await _visible(db, user)
    items = [
        NotificationOut(
            id=n.id,
            level=n.level,
            title=n.title,
            detail=n.detail,
            link=n.link,
            instance=n.user_id is None,
            first_at=n.first_at,
            updated_at=n.updated_at,
            resolved_at=n.resolved_at,
            read=bool(s and s.read_at),
        )
        for n, s in found
    ]
    unread = sum(1 for i in items if not i.read and i.resolved_at is None)
    return NotificationsOut(items=items, unread=unread)


async def _set_state(db: DbDep, user: User, ids: list[int], **values: dt.datetime) -> None:
    if not ids:
        return
    rows = [{"notification_id": i, "user_id": user.id, **values} for i in ids]
    stmt = insert(NotificationState).values(rows)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["notification_id", "user_id"],
            set_={k: stmt.excluded[k] for k in values},
        )
    )
    await db.commit()


@router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(body: NotificationsReadIn, db: DbDep, user: UserDep) -> None:
    visible = {n.id for n, _ in await _visible(db, user)}
    ids = visible if body.ids is None else visible & set(body.ids)
    await _set_state(db, user, sorted(ids), read_at=dt.datetime.now(dt.UTC))


async def _own(db: DbDep, user: User, notification_id: int) -> Notification:
    found = await db.get(Notification, notification_id)
    if found is None or not (found.user_id == user.id or (found.user_id is None and user.is_admin)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    return found


@router.post("/{notification_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(notification_id: int, db: DbDep, user: UserDep) -> None:
    """Hide it for you (it comes back if the problem is fixed and then happens again)."""
    await _own(db, user, notification_id)
    now = dt.datetime.now(dt.UTC)
    await _set_state(db, user, [notification_id], read_at=now, dismissed_at=now)


@router.post("/{notification_id}/hide", status_code=status.HTTP_204_NO_CONTENT)
async def hide_for_everyone(notification_id: int, db: DbDep, user: UserDep) -> None:
    """Admins: hide one about the instance for every admin."""
    found = await _own(db, user, notification_id)
    if found.user_id is not None or not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only instance notifications, by admins")
    found.hidden_at = dt.datetime.now(dt.UTC)
    await db.commit()
