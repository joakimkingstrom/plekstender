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
# When venues started to be searched as named places too: places found only as their town
# before this are looked up once more.
VENUE_SEARCH = dt.datetime(2026, 11, 21, 12, tzinfo=dt.UTC)

_lock = asyncio.Lock()
_last = 0.0


class PlaceError(Exception):
    pass


async def _search(
    http: httpx.AsyncClient, base_url: str, params: dict[str, Any], limit: int = 1
) -> list[dict[str, Any]]:
    global _last
    async with _lock:
        wait = _last + INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()
        try:
            resp = await http.get(
                f"{base_url}/search",
                params={**params, "format": "jsonv2", "limit": limit, "addressdetails": 1},
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
        return []
    try:
        found = resp.json()
    except ValueError:
        return []
    return [f for f in found if isinstance(f, dict)] if isinstance(found, list) else []


async def lookup(
    http: httpx.AsyncClient, base_url: str, venue: str | None, city: str | None, country: str | None
) -> tuple[float | None, float | None, str | None]:
    """(latitude, longitude, "venue" | "city" | None). The venue is tried as free text
    ("Debaser Slussen, Stockholm") and then as a named place in the town (which finds
    venues the free text misses); only then the town itself."""
    codes = {"countrycodes": country.lower()} if country else {}
    if venue:
        query = ", ".join(x for x in (venue, city) if x)
        tries: list[dict[str, Any]] = [{"q": query, **codes}]
        if city:
            tries.append({"amenity": venue, "city": city, **codes})
        for params in tries:
            if hits := await _search(http, base_url, params):
                return float(hits[0]["lat"]), float(hits[0]["lon"]), "venue"
    if city:
        if hits := await _search(http, base_url, {"city": city, **codes}):
            return float(hits[0]["lat"]), float(hits[0]["lon"]), "city"
    return None, None, None


def _address(hit: dict[str, Any]) -> str:
    """A short address under the name: street, town, country."""
    a = hit.get("address") if isinstance(hit.get("address"), dict) else {}
    assert isinstance(a, dict)
    street = " ".join(x for x in (a.get("road"), a.get("house_number")) if x)
    town = a.get("city") or a.get("town") or a.get("village") or a.get("municipality")
    parts = [p for p in (street, town, a.get("country")) if p]
    return ", ".join(dict.fromkeys(parts)) or str(hit.get("display_name") or "")


async def search_places(
    http: httpx.AsyncClient, base_url: str, query: str, country: str | None = None
) -> list[dict[str, Any]]:
    """Places matching what someone typed, for picking a gig's place by hand."""
    codes = {"countrycodes": country.lower()} if country else {}
    hits = await _search(http, base_url, {"q": query, **codes}, limit=8)
    if not hits and codes:  # the country might be wrong: try without it
        hits = await _search(http, base_url, {"q": query}, limit=8)
    out = []
    for hit in hits:
        try:
            lat, lon = float(hit["lat"]), float(hit["lon"])
        except KeyError, TypeError, ValueError:
            continue
        name = str(hit.get("name") or str(hit.get("display_name") or "").split(",")[0]).strip()
        out.append(
            {
                "name": name or query,
                "address": _address(hit),
                "kind": str(hit.get("type") or hit.get("category") or ""),
                "latitude": lat,
                "longitude": lon,
            }
        )
    return out


async def place_gigs(
    db: AsyncSession, http: httpx.AsyncClient, base_url: str, user_id: int | None = None
) -> int:
    """Give unplaced gigs their place (looking up venues not known yet). How many placed."""
    now = dt.datetime.now(dt.UTC)
    # Gigs placed only at their town are looked at again: their venue may be found now.
    query = (
        select(Gig)
        .where(
            or_(Gig.place_precision.is_(None), Gig.place_precision == "city"),
            or_(Gig.venue.is_not(None), Gig.city.is_not(None)),
        )
        .order_by(Gig.id)
    )
    if user_id is not None:
        query = query.where(Gig.user_id == user_id)
    gigs = list(await db.scalars(query))
    placed, looked_up = 0, 0
    for gig in gigs:
        key = place_key(gig.venue, gig.city, gig.country)
        known = await db.get(GigPlace, key)
        stale = known is not None and (
            (known.precision is None and known.looked_up_at < now - RETRY_AFTER)
            # Only the town was found, before venues were also searched as named places.
            or (known.precision == "city" and gig.venue and known.looked_up_at < VENUE_SEARCH)
        )
        if known is None or stale:
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
            # (populate_existing: a place looked up again is already loaded, with old values)
            known = await db.get(GigPlace, key, populate_existing=True)
            await db.commit()
        if known is not None and known.precision:
            if gig.place_precision != known.precision or gig.latitude != known.latitude:
                placed += 1
            gig.latitude, gig.longitude, gig.place_precision = (
                known.latitude, known.longitude, known.precision,
            )  # fmt: skip
    await db.commit()
    return placed
