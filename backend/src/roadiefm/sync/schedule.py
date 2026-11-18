"""When should a server connection sync next?

The worker checks every few minutes and asks `due_sync` for each connection. Waiting
since the last *attempt* (not the last success) means a failing server is retried once
per interval instead of on every check.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from roadiefm.db.instance import LibrarySettings

SyncKind = Literal["full", "incremental"]


def due_sync(
    now: dt.datetime,
    *,
    last_attempt: dt.datetime | None,
    last_full: dt.datetime | None,
    settings: LibrarySettings,
) -> SyncKind | None:
    """The kind of sync that's due now, or None.

    `last_full` is the oldest successful full sync across the connection's libraries
    (None if any library never had one).
    """
    if last_attempt is not None and now - last_attempt < dt.timedelta(
        minutes=settings.incremental_minutes
    ):
        return None
    if last_full is None or now - last_full >= dt.timedelta(days=settings.full_days):
        return "full"
    return "incremental"
