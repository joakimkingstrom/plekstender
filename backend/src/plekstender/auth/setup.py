"""Claiming a new instance: the setup code.

Until somebody has signed in, whoever signs in first becomes the admin. So that it's the
person running the server, they also have to enter a one-time setup code that only the
server's log shows. A new code is made (and logged) each time the web app starts while
nobody has signed in; only its hash is stored, and it's deleted once the instance is
claimed. The code is entered before the Plex sign-in starts (it's checked then, and its
hash goes along in the signed sign-in cookie), so phones that leave the page for Plex and
come back still finish signing in; the claim itself checks it's still the current code.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.instance import get_instance_value, put_instance_value
from plekstender.db.models import InstanceSetting, User

log = logging.getLogger(__name__)

SETUP_KEY = "setup_code"
# No 0/O, 1/I/L: easy to copy from a log by eye.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_code() -> str:
    """Three groups of four, e.g. "K7QF-2MXA-9WRT" (about 59 bits)."""
    chars = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return "-".join(chars[i : i + 4] for i in range(0, 12, 4))


def code_hash(code: str) -> str:
    """The stored form: spaces, dashes and case don't matter."""
    cleaned = re.sub(r"[^A-Z0-9]", "", code.upper())
    return hashlib.sha256(cleaned.encode()).hexdigest()


async def is_claimed(db: AsyncSession) -> bool:
    return bool(await db.scalar(select(func.count()).select_from(User)))


async def issue(db: AsyncSession, app_url: str) -> str:
    """Make a new setup code (any earlier one stops working), store its hash and log it."""
    code = new_code()
    await put_instance_value(db, SETUP_KEY, {"hash": code_hash(code)})
    await db.commit()
    log.warning(
        "\n\n    Plekstender hasn't been set up yet. To become its admin, open %s,\n"
        "    sign in with Plex and enter this setup code when asked:\n\n"
        "        %s\n\n"
        "    (A new code is made each time the app starts until someone has signed in.)\n",
        app_url,
        code,
    )
    return code


async def current_hash(db: AsyncSession) -> str | None:
    stored = await get_instance_value(db, SETUP_KEY)
    value = (stored or {}).get("hash")
    return str(value) if value else None


async def check(db: AsyncSession, code: str | None) -> str | None:
    """The current code's hash if `code` is it, else None."""
    current = await current_hash(db)
    if not current or not code:
        return None
    return current if hmac.compare_digest(current, code_hash(code)) else None


async def clear(db: AsyncSession) -> None:
    """The instance is claimed: the code is done with."""
    await db.execute(delete(InstanceSetting).where(InstanceSetting.key == SETUP_KEY))
