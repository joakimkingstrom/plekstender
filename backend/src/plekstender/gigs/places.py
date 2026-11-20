"""Where gigs were: each venue looked up once on OpenStreetMap's Nominatim (shared by
everyone's gigs), falling back to the town. Nominatim asks for at most one request a
second and an identifying User-Agent; only the venue, town and country are sent.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Gig, GigPlace
from plekstender.gigs.service import place_key
from plekstender.live.jambase import USER_AGENT

log = logging.getLogger(__name__)

INTERVAL = 1.1  # seconds between requests (Nominatim: at most one a second)
TIMEOUT = 20.0
RETRY_AFTER = dt.timedelta(days=30)  # look a place that wasn't found up again after this
PER_RUN = 300

_lock = asyncio.Lock()
_last = 0.0


class PlaceError(Exception):
    pass


async def _search(
    http: httpx.AsyncClient, base_url: str, params: dict[str, Any]
) -> dict[str, Any] | None:
    global _last
    async with _lock:
        wait = _last + INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()
        try:
            resp = await http.get(
                f"{base_url}/search",
                params={**params, "format": "jsonv2", "limit": 1},
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise PlaceError(
                f"The place search couldn't be reached ({exc.__class__.__name__})."
            ) from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        raise PlaceError(f"The place search is busy (HTTP {resp.status_code}).")
    if resp.status_code >= 400:
        return None
    try:
        found = resp.json()
    except ValueError:
        return None
    return found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else None


async def lookup(
    http: httpx.AsyncClient, base_url: str, venue: str | None, city: str | None, country: str | None
) -> tuple[float | None, float | None, str | None]:
    """(latitude, longitude, "venue" | "city" | None)."""
    codes = {"countrycodes": country.lower()} if country else {}
    if venue:
        query = ", ".join(x for x in (venue, city) if x)
        if hit := await _search(http, base_url, {"q": query, **codes}):
            return float(hit["lat"]), float(hit["lon"]), "venue"
    if city:
        if hit := await _search(http, base_url, {"city": city, **codes}):
            return float(hit["lat"]), float(hit["lon"]), "city"
    return None, None, None


async def place_gigs(
    db: AsyncSession, http: httpx.AsyncClient, base_url: str, user_id: int | None = None
) -> int:
    """Give unplaced gigs their place (looking up venues not known yet). How many placed."""
    now = dt.datetime.now(dt.UTC)
    query = (
        select(Gig)
        .where(Gig.place_precision.is_(None), or_(Gig.venue.is_not(None), Gig.city.is_not(None)))
        .order_by(Gig.id)
    )
    if user_id is not None:
        query = query.where(Gig.user_id == user_id)
    gigs = list(await db.scalars(query))
    placed, looked_up = 0, 0
    for gig in gigs:
        key = place_key(gig.venue, gig.city, gig.country)
        known = await db.get(GigPlace, key)
        if known is None or (known.precision is None and known.looked_up_at < now - RETRY_AFTER):
            if looked_up >= PER_RUN:
                continue
            try:
                lat, lon, precision = await lookup(http, base_url, gig.venue, gig.city, gig.country)
            except PlaceError as exc:
                log.warning("Gig places: %s", exc)
                break  # try again on the next run
            looked_up += 1
            stmt = insert(GigPlace).values(
                key=key, latitude=lat, longitude=lon, precision=precision, looked_up_at=now
            )
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=[GigPlace.key],
                    set_={
                        "latitude": lat,
                        "longitude": lon,
                        "precision": precision,
                        "looked_up_at": now,
                    },
                )
            )
            known = await db.get(GigPlace, key)
            await db.commit()
        if known is not None and known.precision:
            gig.latitude, gig.longitude, gig.place_precision = (
                known.latitude, known.longitude, known.precision,
            )  # fmt: skip
            placed += 1
    await db.commit()
    return placed
