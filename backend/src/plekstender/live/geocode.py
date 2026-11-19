"""Finding a place by name for the Live tab (Open-Meteo's geocoding: GeoNames, no key).

Only the typed text is sent. "Use my location" uses the browser instead and sends nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx

from plekstender.live.jambase import USER_AGENT

TIMEOUT = 10.0


class GeocodeError(Exception):
    pass


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    country: str | None


async def search(http: httpx.AsyncClient, base_url: str, query: str, limit: int = 8) -> list[Place]:
    try:
        resp = await http.get(
            f"{base_url}/search",
            params={"name": query, "count": limit, "language": "en", "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        body: Any = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GeocodeError("The place search isn't available right now.") from exc
    places = []
    for r in (body.get("results") if isinstance(body, dict) else None) or []:
        lat, lon = r.get("latitude"), r.get("longitude")
        if (
            not r.get("name")
            or not isinstance(lat, int | float)
            or not isinstance(lon, int | float)
        ):
            continue
        parts = [r["name"], r.get("admin1"), r.get("country")]
        label = ", ".join(dict.fromkeys(str(p) for p in parts if p))
        places.append(Place(label, float(lat), float(lon), r.get("country_code")))
    return places


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))
