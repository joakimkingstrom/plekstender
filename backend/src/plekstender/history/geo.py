"""Where an IP address is, from a local copy of DB-IP's free city database.

Lookups never leave the server. The database ("IP to City Lite", CC BY 4.0: credit
"IP geolocation by DB-IP" where locations are shown) is downloaded monthly by the worker.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import ipaddress
import logging
import shutil
from pathlib import Path
from typing import Any

import httpx
import maxminddb

from plekstender.config import Settings

log = logging.getLogger(__name__)

URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
FILE = "dbip-city-lite.mmdb"
MAX_AGE = dt.timedelta(days=32)
MAX_DOWNLOAD = 300 * 1024 * 1024
ATTRIBUTION = "IP geolocation by DB-IP"


def db_path(settings: Settings) -> Path:
    return settings.data_dir / "geoip" / FILE


def _is_fresh(path: Path, now: dt.datetime) -> bool:
    if not path.exists():
        return False
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
    return now - modified < MAX_AGE


def _unpack(gz: Path, target: Path) -> None:
    tmp = target.with_suffix(".part")
    with gzip.open(gz, "rb") as src, tmp.open("wb") as out:
        shutil.copyfileobj(src, out)
    maxminddb.open_database(str(tmp)).close()  # refuse a broken file
    tmp.replace(target)


async def update_database(
    http: httpx.AsyncClient, settings: Settings, now: dt.datetime | None = None
) -> bool:
    """Download this month's database unless the copy is recent. Returns True if updated."""
    now = now or dt.datetime.now(dt.UTC)
    target = db_path(settings)
    if await asyncio.to_thread(_is_fresh, target, now):
        return False
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    first_of_month = now.replace(day=1)
    # Early in a month the new file may not be out yet: fall back to last month's.
    months = [now, first_of_month - dt.timedelta(days=1)]
    gz = target.with_suffix(".mmdb.gz")
    for month in months:
        url = URL.format(month=month.strftime("%Y-%m"))
        try:
            async with http.stream("GET", url, timeout=httpx.Timeout(120, connect=15)) as resp:
                if resp.status_code != 200:
                    continue
                size = 0
                with gz.open("wb") as out:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            raise ValueError("IP database download too large")
                        out.write(chunk)
            await asyncio.to_thread(_unpack, gz, target)
            log.info("IP location database updated from %s", url)
            return True
        except (httpx.HTTPError, OSError, ValueError, maxminddb.InvalidDatabaseError) as exc:
            log.warning("IP location database download failed (%s): %s", url, exc)
        finally:
            gz.unlink(missing_ok=True)
    return False


class GeoLookup:
    """City and country for public IP addresses (None when unknown)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._reader: Any = None
        self._mtime = 0.0

    def _open(self) -> Any:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return None
        if self._reader is None or mtime != self._mtime:
            if self._reader is not None:
                self._reader.close()
            self._reader = maxminddb.open_database(str(self._path))
            self._mtime = mtime
        return self._reader

    def lookup(self, address: str | None) -> str | None:
        if not address:
            return None
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        if not ip.is_global:
            return "Local network"
        reader = self._open()
        if reader is None:
            return None
        record = reader.get(str(ip)) or {}
        city = ((record.get("city") or {}).get("names") or {}).get("en")
        country = ((record.get("country") or {}).get("names") or {}).get("en")
        return ", ".join(p for p in (city, country) if p) or None


_lookups: dict[Path, GeoLookup] = {}


def geo_lookup(settings: Settings) -> GeoLookup:
    path = db_path(settings)
    return _lookups.setdefault(path, GeoLookup(path))
