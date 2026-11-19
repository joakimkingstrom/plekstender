"""JamBase's concert data (Data API v3): upcoming shows by artist near a place.

The key (`jbd_…`) is sent as a Bearer token. The free Developer plan has 1,000 requests a
month and bills requests past that, so callers count every request (see
`db.instance.reserve_jambase_call`); `GET /quota` is free and says what JamBase has counted.
On that plan the events don't carry MusicBrainz IDs, so performers are matched by name.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import httpx

from plekstender import __version__
from plekstender.live.events import Event, Performer, prices

USER_AGENT = f"Plekstender/{__version__} (self-hosted music library tool)"
TIMEOUT = 30.0
PER_PAGE = 100
# Artists per request (artistId / artistName take several, separated by "|").
BATCH = 20
# The Developer plan sees six months ahead (a few days' margin on "six months").
DAYS_AHEAD = 175
# "The identifier `musicbrainz:…` is invalid."
# How JamBase names an ID it doesn't know: "No artist found for source `musicbrainz` artist
# id `<mbid>`" (one error per ID, as it answers in practice) or "The identifier
# `jambase:87121` is invalid." (as documented).
_UNKNOWN_ID = (
    re.compile(r"source `([a-z0-9-]+)` artist id `([^`\s]+)`"),
    re.compile(r"`([a-z0-9-]+):([^`\s]+)`"),
)


class JamBaseError(Exception):
    pass


class JamBaseKeyError(JamBaseError):
    """The key was refused (401) or the plan doesn't allow the request (403)."""


class JamBaseQuotaError(JamBaseError):
    """JamBase's own monthly quota is used up (429 quota-exceeded)."""


class JamBaseRequestError(JamBaseError):
    """JamBase refused the request's parameters (400)."""


class JamBaseIdError(JamBaseRequestError):
    """Artist IDs JamBase doesn't know (400 identifier_invalid): the whole request fails.
    `identifiers` are the ones it named ("musicbrainz:<mbid>"), lower-cased."""

    def __init__(self, message: str, identifiers: list[str]) -> None:
        super().__init__(message)
        self.identifiers = identifiers


def _unknown_ids(resp: httpx.Response) -> tuple[bool, list[str]]:
    """Whether the answer is about unknown IDs, and the IDs it names."""
    try:
        body = resp.json()
    except ValueError:
        return False, []
    errors = body.get("errors") if isinstance(body, dict) else None
    about_ids, found = False, []
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        code = error.get("code") or error.get("errorCode")
        message = str(error.get("message") or error.get("errorMessage") or "")
        if code != "identifier_invalid":
            continue
        about_ids = True
        for pattern in _UNKNOWN_ID:
            if m := pattern.search(message):
                found.append(f"{m[1]}:{m[2]}".lower())
                break
    return about_ids, found


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def _first(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if item.get(key):
            return str(item[key])
    return None


def _problem_code(resp: httpx.Response) -> tuple[str, str | None]:
    """The message and error code in an error answer (`errors[]` as documented, other field
    names JamBase uses in practice, or an RFC 7807 problem); else the answer itself."""
    try:
        body = resp.json()
    except ValueError:
        text = resp.text.strip()[:300]
        return text or f"HTTP {resp.status_code}", None
    if isinstance(body, dict):
        errors = body.get("errors") or body.get("error")
        first = errors[0] if isinstance(errors, list) and errors else errors
        for item in (first, body):
            if isinstance(item, dict):
                message = _first(item, "errorMessage", "message", "detail", "title", "description")
                code = _first(item, "errorCode", "code", "type")
                if message or code:
                    return message or code or "", code
        if isinstance(first, str) and first:
            return first[:300], None
    raw = json.dumps(body, separators=(",", ":"))[:300]
    return raw, None


def _problem(resp: httpx.Response) -> str:
    return _problem_code(resp)[0]


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
        message = _problem(resp)
        if resp.status_code == 400:
            about_ids, identifiers = _unknown_ids(resp)
            if about_ids:
                more = f" (and {len(identifiers) - 1} more)" if len(identifiers) > 1 else ""
                raise JamBaseIdError(f"JamBase: {message}{more}", identifiers)
            raise JamBaseRequestError(f"JamBase refused the request: {message}")
        raise JamBaseError(f"JamBase answered {resp.status_code}: {message}")
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
    specs = [
        _dict(o.get("priceSpecification")) for o in raw.get("offers") or [] if isinstance(o, dict)
    ]  # not on the free Developer plan, but read if there
    low, high, currency = prices(
        [
            (
                s.get("minPrice", s.get("price")),
                s.get("maxPrice", s.get("price")),
                s.get("priceCurrency"),
            )
            for s in specs
            if s
        ]
    )
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
        price_min=low,
        price_max=high,
        currency=currency,
    )
