"""JamBase's concert data (Data API v3): upcoming shows by artist near a place.

The key (`jbd_…`) is sent as a Bearer token. The free Developer plan has 1,000 requests a
month and bills requests past that, so callers count every request (see
`db.instance.reserve_jambase_call`); `GET /quota` is free and says what JamBase has counted.
On that plan the events don't carry MusicBrainz IDs, so performers are matched by name.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from plekstender import __version__

USER_AGENT = f"Plekstender/{__version__} (self-hosted music library tool)"
TIMEOUT = 30.0
PER_PAGE = 100
# Artists per request (artistId / artistName take several, separated by "|").
BATCH = 20
# The Developer plan sees six months ahead.
DAYS_AHEAD = 180


class JamBaseError(Exception):
    pass


class JamBaseKeyError(JamBaseError):
    """The key was refused (401) or the plan doesn't allow the request (403)."""


class JamBaseQuotaError(JamBaseError):
    """JamBase's own monthly quota is used up (429 quota-exceeded)."""


@dataclass
class Performer:
    name: str
    headliner: bool


@dataclass
class Event:
    id: str
    name: str
    festival: bool
    start_date: dt.date
    start_time: str | None
    end_date: dt.date | None
    status: str
    venue: str | None
    city: str | None
    region: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    ticket_url: str | None
    seller: str | None
    page_url: str | None
    performers: list[Performer] = field(default_factory=list)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def _problem(resp: httpx.Response) -> str:
    """The message in an error answer (`errors[]` or an RFC 7807 problem)."""
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("errorMessage") or errors[0].get("errorCode"))
        for key in ("detail", "title"):
            if body.get(key):
                return str(body[key])
    return f"HTTP {resp.status_code}"


async def _get(
    http: httpx.AsyncClient, url: str, api_key: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    try:
        resp = await http.get(url, params=params, headers=_headers(api_key), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise JamBaseError(f"JamBase couldn't be reached ({exc.__class__.__name__}).") from exc
    if resp.status_code == 401:
        raise JamBaseKeyError("JamBase didn't accept the API key.")
    if resp.status_code == 403:
        raise JamBaseKeyError(f"JamBase refused the request: {_problem(resp)}")
    if resp.status_code == 429:
        message = _problem(resp)
        if "quota" in resp.text.lower():
            raise JamBaseQuotaError(f"JamBase's monthly quota is used up: {message}")
        raise JamBaseError("JamBase is busy (too many requests); try again later.")
    if resp.status_code >= 400:
        raise JamBaseError(f"JamBase answered {resp.status_code}: {_problem(resp)}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise JamBaseError("JamBase sent an answer that isn't JSON.") from exc
    if not isinstance(body, dict):
        raise JamBaseError("JamBase sent an unexpected answer.")
    return body


async def quota(http: httpx.AsyncClient, base_url: str, api_key: str) -> dict[str, Any]:
    """The plan's quota and usage (doesn't count against the quota). Also checks the key."""
    body = await _get(http, f"{base_url}/quota", api_key)
    keys = (
        "plan", "quota", "usedCalls", "remainingCalls", "overageCalls",
        "periodStart", "periodEnd", "limitType", "blocksAtQuota",
    )  # fmt: skip
    return {k: body.get(k) for k in keys}


async def events(
    http: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    latitude: float,
    longitude: float,
    radius_km: int,
    date_from: dt.date,
    date_to: dt.date,
    artist_ids: list[str] | None = None,
    artist_names: list[str] | None = None,
    page: int = 1,
) -> tuple[list[Event], int]:
    """One page of upcoming events near a place by some artists, and the number of pages.
    `artist_ids` are `{source}:{id}` (e.g. `musicbrainz:<mbid>`); `artist_names` is a
    keyword search, so callers check the performers' names."""
    if bool(artist_ids) == bool(artist_names):
        raise ValueError("Pass artist IDs or artist names (not both).")
    params: dict[str, Any] = {
        "geoLatitude": f"{latitude:.4f}",
        "geoLongitude": f"{longitude:.4f}",
        "geoRadiusAmount": max(1, min(radius_km, 5000)),
        "geoRadiusUnits": "km",
        "eventDateFrom": date_from.isoformat(),
        "eventDateTo": date_to.isoformat(),
        "perPage": PER_PAGE,
        "page": page,
    }
    if artist_ids:
        params["artistId"] = "|".join(artist_ids)
    else:
        params["artistName"] = "|".join(n.replace("|", " ") for n in artist_names or [])
    body = await _get(http, f"{base_url}/events", api_key, params)
    found = [e for raw in body.get("events") or [] if (e := parse_event(raw))]
    pagination = body.get("pagination") or {}
    pages = pagination.get("totalPages") if isinstance(pagination, dict) else None
    return found, pages if isinstance(pages, int) and pages > 0 else 1


_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}))?")


def _date(value: Any) -> tuple[dt.date | None, str | None]:
    """A date and the local time ("20:00") from "2026-05-08T20:00:00-04:00" or "2026-08-07".
    The venue's own time is kept: shows are listed in local time."""
    m = _DATE.match(value) if isinstance(value, str) else None
    if not m:
        return None, None
    try:
        day = dt.date.fromisoformat(m[1])
    except ValueError:
        return None, None
    return day, (f"{m[2]}:{m[3]}" if m[2] else None)


def _place(value: Any) -> tuple[str | None, str | None]:
    """(name, code) from a State / Country object or a plain string."""
    if isinstance(value, dict):
        name, code = value.get("name"), value.get("alternateName") or value.get("identifier")
        return (str(name) if name else None), (str(code) if code else None)
    if isinstance(value, str) and value:
        return value, value if len(value) == 2 else None
    return None, None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _ticket(offers: Any) -> tuple[str | None, str | None]:
    """The ticket link exactly as given (the primary one first) and who sells it."""
    usable = (
        [o for o in offers if isinstance(o, dict) and o.get("url")]
        if isinstance(offers, list)
        else []
    )
    if not usable:
        return None, None
    usable.sort(key=lambda o: o.get("category") != "ticketingLinkPrimary")
    offer = usable[0]
    seller = offer.get("seller")
    name = seller.get("name") if isinstance(seller, dict) else None
    return str(offer["url"]), str(name or offer.get("name") or "") or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_event(raw: Any) -> Event | None:
    if not isinstance(raw, dict) or not raw.get("identifier"):
        return None
    start, time = _date(raw.get("startDate"))
    if not start:
        return None
    end, _ = _date(raw.get("endDate"))
    location = _dict(raw.get("location"))
    address = _dict(location.get("address"))
    geo = _dict(location.get("geo"))
    region, _ = _place(address.get("addressRegion"))
    _, country = _place(address.get("addressCountry"))
    ticket, seller = _ticket(raw.get("offers"))
    performers = [
        Performer(str(p["name"]), bool(p.get("x-isHeadliner")))
        for p in raw.get("performer") or []
        if isinstance(p, dict) and p.get("name")
    ]
    return Event(
        id=str(raw["identifier"])[:64],
        name=str(raw.get("name") or ", ".join(p.name for p in performers[:3]) or "Show"),
        festival=raw.get("@type") == "Festival",
        start_date=start,
        start_time=time,
        end_date=end if end and end != start else None,
        status=str(raw.get("eventStatus") or "scheduled")[:16],
        venue=location.get("name"),
        city=address.get("addressLocality"),
        region=region,
        country=(country or "")[:2].upper() or None,
        latitude=_float(geo.get("latitude")),
        longitude=_float(geo.get("longitude")),
        ticket_url=ticket,
        seller=seller,
        page_url=raw.get("url") if isinstance(raw.get("url"), str) else None,
        performers=performers,
    )
