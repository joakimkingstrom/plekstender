"""Data Thistle's Publishing API: UK event listings, Live's source for small local shows.

The admin pastes the two tokens from their (free) Data Thistle account: the access token
(a JWT, sent as Bearer) lasts 30 days, the refresh token a month longer. The worker
renews both a few days before the access token runs out (`POST /refresh` with the refresh
token as Bearer gives a new pair). Answers carry the month's quota (`X-RateLimit-*`).

Events have no performer field, so they're matched by name against the library's artists.
Distances are in miles; results come nearest first, 20 a page. The terms allow keeping
listings a day at most, and ask for Data Thistle's logo and its add / update event links.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from plekstender.live.events import Event, Performer, prices
from plekstender.live.jambase import USER_AGENT

TIMEOUT = 30.0
PAGE_SIZE = 20
MILES = 1.609344
# Renew the access token when it has fewer days than this left.
RENEW_BEFORE = dt.timedelta(days=5)


class DataThistleError(Exception):
    pass


class DataThistleAuthError(DataThistleError):
    """The token was refused (401)."""


@dataclass
class Quota:
    limit: int | None
    remaining: int | None
    resets_at: dt.datetime | None


def token_expiry(token: str) -> dt.datetime | None:
    """A JWT's `exp` (read, not verified: only to know when to renew)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return dt.datetime.fromtimestamp(int(payload["exp"]), dt.UTC)
    except ValueError, KeyError, TypeError:
        return None


def _quota(resp: httpx.Response) -> Quota:
    def number(name: str) -> int | None:
        try:
            return int(resp.headers[name])
        except KeyError, ValueError:
            return None

    reset = number("X-RateLimit-Reset")
    return Quota(
        number("X-RateLimit-Limit"),
        number("X-RateLimit-Remaining"),
        dt.datetime.fromtimestamp(reset, dt.UTC) if reset else None,
    )


def _message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip()[:200] or f"HTTP {resp.status_code}"
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return f"HTTP {resp.status_code}"


async def _request(
    http: httpx.AsyncClient, method: str, url: str, token: str, params: dict[str, Any] | None = None
) -> httpx.Response:
    try:
        resp = await http.request(
            method,
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DataThistleError(
            f"Data Thistle couldn't be reached ({exc.__class__.__name__})."
        ) from exc
    if resp.status_code == 401:
        raise DataThistleAuthError(f"Data Thistle refused the token: {_message(resp)}")
    if resp.status_code == 429:
        raise DataThistleError("Data Thistle's monthly requests are used up.")
    if resp.status_code >= 400:
        raise DataThistleError(f"Data Thistle answered {resp.status_code}: {_message(resp)}")
    return resp


async def ping(http: httpx.AsyncClient, base_url: str, access: str) -> Quota:
    """Check the access token; the answer says how many requests are left."""
    return _quota(await _request(http, "GET", f"{base_url}/ping", access))


async def renew(http: httpx.AsyncClient, auth_url: str, refresh: str) -> tuple[str, str]:
    """A new access token and refresh token (each renewal replaces both)."""
    resp = await _request(http, "POST", f"{auth_url}/refresh", refresh)
    try:
        body = resp.json()
        access, new_refresh = body["accessToken"], body["refreshToken"]
    except (ValueError, KeyError, TypeError) as exc:
        raise DataThistleError("Data Thistle's renewal answer had no tokens.") from exc
    if not isinstance(access, str) or not isinstance(new_refresh, str):
        raise DataThistleError("Data Thistle's renewal answer had no tokens.")
    return access, new_refresh


async def events(
    http: httpx.AsyncClient,
    base_url: str,
    access: str,
    *,
    latitude: float,
    longitude: float,
    radius_km: int,
    start: dt.date,
    end: dt.date,
    page: int = 1,
) -> tuple[list[dict[str, Any]], Quota]:
    """One page (20) of music events near a place with performances between two dates,
    nearest first."""
    resp = await _request(
        http,
        "GET",
        f"{base_url}/events",
        access,
        {
            "tags": "music",
            "lat": f"{latitude:.5f}",
            "lon": f"{longitude:.5f}",
            "distance": round(radius_km / MILES, 1),
            # Strict ISO-8601, like its own answers: the spec's "2025-03-01 12:30:00+00:00"
            # (with a space) is refused as "Your timestamp is invalid".
            "min_date": f"{start.isoformat()}T00:00:00+00:00",
            "max_date": f"{end.isoformat()}T23:59:59+00:00",
            "limit": PAGE_SIZE,
            "page": page,
        },
    )
    try:
        body = resp.json()
    except ValueError as exc:
        raise DataThistleError("Data Thistle sent an answer that isn't JSON.") from exc
    found = body if isinstance(body, list) else []
    return [e for e in found if isinstance(e, dict)], _quota(resp)


_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2}))?")
# Billing words in event names: "Radiohead: In Rainbows Tour", "An Evening with X", "X + Y".
_NAME_SPLIT = re.compile(r"\s*(?::|\s[-–]\s|\s\+\s|,|\s&\s|\bwith\b|\bplus\b)\s*", re.I)  # noqa: RUF001
_PREFIX = re.compile(r"^(?:an evening with|an audience with|live:?|in concert:?)\s+", re.I)


def candidate_names(name: str) -> list[str]:
    """Names an event title may be an artist by: the whole title, then its parts."""
    title = _PREFIX.sub("", name.strip())
    parts = [p.strip() for p in _NAME_SPLIT.split(title) if p and p.strip()]
    return list(dict.fromkeys([title, *parts]))


def _distance_km(item: dict[str, Any]) -> float | None:
    value = item.get("_distance")
    return float(value) * MILES if isinstance(value, int | float) else None


def parse(raw: dict[str, Any], start: dt.date, end: dt.date, radius_km: int) -> list[Event]:
    """A listing's shows: one per place and date (a run of nights is a show a night)."""
    event_id = raw.get("event_id")
    name = str(raw.get("name") or "").strip()
    if not event_id or not name:
        return []
    schedules = [s for s in raw.get("schedules") or [] if isinstance(s, dict)]

    def place_of(schedule: dict[str, Any]) -> dict[str, Any]:
        place = schedule.get("place")
        return place if isinstance(place, dict) else {}

    def far(schedule: dict[str, Any]) -> float | None:
        own = _distance_km(schedule)
        return own if own is not None else _distance_km(place_of(schedule))

    if any(far(s) is not None for s in schedules):
        near = [s for s in schedules if (d := far(s)) is None or d <= radius_km]
    else:
        near = schedules[:1]  # no distance per place: the nearest place comes first
    found: dict[tuple[str, dt.date], Event] = {}
    for schedule in near:
        place = place_of(schedule)
        km = far(schedule)
        km = km if km is not None else _distance_km(raw)
        for performance in schedule.get("performances") or []:
            if not isinstance(performance, dict):
                continue
            m = _TS.match(str(performance.get("ts") or ""))
            if not m:
                continue
            day = dt.date.fromisoformat(m[1])
            if not start <= day <= end:
                continue
            key = (str(schedule.get("place_id") or place.get("place_id") or ""), day)
            if key in found:
                continue
            booking = next(
                (
                    str(link["url"])
                    for link in performance.get("links") or []
                    if isinstance(link, dict) and link.get("type") == "booking" and link.get("url")
                ),
                None,
            )
            time = None if performance.get("time_unknown") or not m[2] else f"{m[2]}:{m[3]}"
            tickets = [t for t in performance.get("tickets") or [] if isinstance(t, dict)]
            # "A ticket with no min_price and a description of 'free' is also free."
            low, high, currency = prices(
                [
                    (
                        0
                        if t.get("min_price") is None
                        and "free" in str(t.get("description") or "").lower()
                        else t.get("min_price"),
                        t.get("max_price"),
                        t.get("currency"),
                    )
                    for t in tickets
                ]
            )
            found[key] = Event(
                id=f"{event_id}:{key[0][:24]}:{day.isoformat()}"[:64],
                name=name[:300],
                festival="festival" in name.lower(),
                start_date=day,
                start_time=time,
                end_date=None,
                status="scheduled",
                venue=place.get("name"),
                city=place.get("town"),
                region=None,
                country="GB",
                latitude=None,
                longitude=None,
                ticket_url=booking,
                seller="Data Thistle" if booking else None,
                page_url=raw.get("website") if isinstance(raw.get("website"), str) else None,
                performers=[Performer(name, headliner=True)],
                distance_km=round(km, 1) if km is not None else None,
                price_min=low,
                price_max=high,
                currency=currency,
            )
    return list(found.values())
