"""Ticketmaster's Discovery API: music events near a place, the second source for Live.

Unlike JamBase it isn't searched artist by artist: all music events near the user are read
(a month at a time, 200 a page) and their line-ups matched against the artists the user
follows. A free key allows 5,000 requests a day and 5 a second; results are paged up to
the 1,000th (`size * page < 1000`), so a busy month is split into shorter windows.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any

import httpx

from plekstender.live.events import Event, Performer, prices
from plekstender.live.jambase import USER_AGENT

TIMEOUT = 30.0
PAGE_SIZE = 200
MAX_ITEMS = 1000  # deep paging limit
INTERVAL = 0.25  # seconds between requests (the limit is 5 a second)
STATUS = {"cancelled": "cancelled", "canceled": "cancelled", "postponed": "postponed",
          "rescheduled": "rescheduled"}  # fmt: skip

_lock = asyncio.Lock()
_last = 0.0


class TicketmasterError(Exception):
    pass


class TicketmasterKeyError(TicketmasterError):
    pass


_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(latitude: float, longitude: float, precision: int = 9) -> str:
    """The geohash for `geoPoint` (the older `latlong` filter is deprecated)."""
    lat, lon = [-90.0, 90.0], [-180.0, 180.0]
    out: list[str] = []
    bits, bit, even = 0, 0, True
    while len(out) < precision:
        rng, value = (lon, longitude) if even else (lat, latitude)
        mid = (rng[0] + rng[1]) / 2
        if value >= mid:
            bits = bits * 2 + 1
            rng[0] = mid
        else:
            bits *= 2
            rng[1] = mid
        even, bit = not even, bit + 1
        if bit == 5:
            out.append(_BASE32[bits])
            bits, bit = 0, 0
    return "".join(out)


def _fault(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip()[:200] or f"HTTP {resp.status_code}"
    fault = body.get("fault") if isinstance(body, dict) else None
    if isinstance(fault, dict) and fault.get("faultstring"):
        return str(fault["faultstring"])
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("detail") or errors[0].get("code") or errors[0])
    return f"HTTP {resp.status_code}"


async def _get(
    http: httpx.AsyncClient, url: str, api_key: str, params: dict[str, Any]
) -> dict[str, Any]:
    global _last
    async with _lock:
        wait = _last + INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()
    try:
        resp = await http.get(
            url,
            params={**params, "apikey": api_key},
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise TicketmasterError(
            f"Ticketmaster couldn't be reached ({exc.__class__.__name__})."
        ) from exc
    if resp.status_code in (401, 403):
        raise TicketmasterKeyError(f"Ticketmaster didn't accept the API key ({_fault(resp)}).")
    if resp.status_code == 429:
        raise TicketmasterError(f"Ticketmaster's request limit was reached ({_fault(resp)}).")
    if resp.status_code >= 400:
        raise TicketmasterError(f"Ticketmaster answered {resp.status_code}: {_fault(resp)}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise TicketmasterError("Ticketmaster sent an answer that isn't JSON.") from exc
    if not isinstance(body, dict):
        raise TicketmasterError("Ticketmaster sent an unexpected answer.")
    return body


async def check_key(http: httpx.AsyncClient, base_url: str, api_key: str) -> None:
    await _get(http, f"{base_url}/events.json", api_key, {"size": 1, "classificationName": "music"})


async def events(
    http: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    latitude: float,
    longitude: float,
    radius_km: int,
    start: dt.date,
    end: dt.date,
    page: int = 0,
) -> tuple[list[Event], int, int]:
    """One page of music events near a place starting between two dates (inclusive), the
    number of events in all, and of pages."""
    body = await _get(
        http,
        f"{base_url}/events.json",
        api_key,
        {
            "classificationName": "music",
            "geoPoint": geohash(latitude, longitude),
            "radius": max(1, radius_km),
            "unit": "km",
            "startDateTime": f"{start.isoformat()}T00:00:00Z",
            "endDateTime": f"{end.isoformat()}T23:59:59Z",
            "size": PAGE_SIZE,
            "page": page,
            "sort": "date,asc",
            "locale": "*",
        },
    )
    found = [
        e for raw in _dict(body.get("_embedded")).get("events") or [] if (e := parse_event(raw))
    ]
    info = _dict(body.get("page"))
    total, pages = info.get("totalElements"), info.get("totalPages")
    return found, total if isinstance(total, int) else 0, pages if isinstance(pages, int) else 0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except TypeError, ValueError:
        return None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def parse_event(raw: Any) -> Event | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    dates = _dict(raw.get("dates"))
    start = _dict(dates.get("start"))
    day = _date(start.get("localDate"))
    if day is None:
        return None
    local_time = start.get("localTime")
    end = _date(_dict(dates.get("end")).get("localDate"))
    status = str(_dict(dates.get("status")).get("code") or "")
    embedded = _dict(raw.get("_embedded"))
    venues = embedded.get("venues") or []
    venue = _dict(venues[0]) if isinstance(venues, list) and venues else {}
    location = _dict(venue.get("location"))
    performers = []
    for i, attraction in enumerate(embedded.get("attractions") or []):
        if not isinstance(attraction, dict) or not attraction.get("name"):
            continue
        links = _dict(attraction.get("externalLinks")).get("musicbrainz") or []
        mbid = _dict(links[0]).get("id") if isinstance(links, list) and links else None
        performers.append(
            Performer(str(attraction["name"]), headliner=i == 0, mbid=str(mbid) if mbid else None)
        )
    low, high, currency = prices(
        [
            (r.get("min"), r.get("max"), r.get("currency"))
            for r in raw.get("priceRanges") or []
            if isinstance(r, dict)
        ]
    )
    return Event(
        id=f"ticketmaster:{raw['id']}"[:64],
        name=str(raw.get("name") or ", ".join(p.name for p in performers[:3]) or "Gig"),
        festival=len(performers) > 6,
        start_date=day,
        start_time=local_time[:5] if isinstance(local_time, str) and len(local_time) >= 5 else None,
        end_date=end if end and end != day else None,
        status=STATUS.get(status.lower(), "scheduled"),
        venue=venue.get("name"),
        city=_dict(venue.get("city")).get("name"),
        region=_dict(venue.get("state")).get("name"),
        country=(str(_dict(venue.get("country")).get("countryCode") or "")[:2].upper() or None),
        latitude=_float(location.get("latitude")),
        longitude=_float(location.get("longitude")),
        ticket_url=raw.get("url") if isinstance(raw.get("url"), str) else None,
        seller="Ticketmaster",
        page_url=None,
        performers=performers,
        price_min=low,
        price_max=high,
        currency=currency,
    )
