"""Where listens happened: country and continent codes for the listens map, and the
stats per area.

Only plays seen live by the session watcher have an address; imported scrobbles don't.
Plays on the home network are placed where the server is (its public address, from
plex.tv).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.config import Settings
from plekstender.db.models import Listen, PlaySession, PlexServer, User, UserServer
from plekstender.history import stats
from plekstender.history.geo import GeoLookup, Place, geo_lookup
from plekstender.plex.tv import PlexError, PlexTvClient
from plekstender.security.crypto import DecryptionError, TokenCipher

log = logging.getLogger(__name__)

FILL_BATCH = 2000
CONTINENTS = {
    "AF": "Africa",
    "AN": "Antarctica",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
}


# --- filling in places ------------------------------------------------------------------


async def refresh_public_addresses(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings
) -> int:
    """Ask plex.tv (as an owner) for the public address of servers that lack one."""
    servers = list(await db.scalars(select(PlexServer).where(PlexServer.public_address.is_(None))))
    if not servers:
        return 0
    cipher = TokenCipher(settings.derive_key("token-encryption"))
    plex = PlexTvClient(settings, http)
    found = 0
    for server in servers:
        owner = await db.scalar(
            select(User)
            .join(UserServer, UserServer.user_id == User.id)
            .where(UserServer.server_id == server.id, UserServer.is_owner.is_(True))
            .limit(1)
        )
        if owner is None:
            continue
        try:
            token = cipher.decrypt(owner.plex_token_enc, purpose="user.plex_token")
            resources = await plex.get_servers(token)
        except (DecryptionError, PlexError, httpx.HTTPError) as exc:
            log.debug("public address of server %s unavailable: %s", server.id, exc)
            continue
        for resource in resources:
            if resource.client_identifier == server.machine_identifier and resource.public_address:
                server.public_address = resource.public_address[:64]
                found += 1
    await db.commit()
    return found


class Placer:
    """Places for addresses, with each server's own place for home-network plays."""

    def __init__(self, db: AsyncSession, geo: GeoLookup) -> None:
        self._db = db
        self._geo = geo
        self._servers: dict[int, Place | None] = {}

    async def server_place(self, server_id: int | None) -> Place | None:
        if server_id is None:
            return None
        if server_id not in self._servers:
            server = await self._db.get(PlexServer, server_id)
            self._servers[server_id] = self._geo.place(server.public_address) if server else None
        return self._servers[server_id]

    async def place(
        self, server_id: int | None, address: str | None, network: str | None
    ) -> Place | None:
        if network == "lan":
            return await self.server_place(server_id)
        return self._geo.place(address) or (
            await self.server_place(server_id) if _is_private(address) else None
        )


def _is_private(address: str | None) -> bool:
    import ipaddress

    try:
        return address is not None and not ipaddress.ip_address(address).is_global
    except ValueError:
        return False


async def fill_places(db: AsyncSession, settings: Settings) -> int:
    """Give play sessions and listens that have an address their country and continent.

    Covers plays recorded before the IP database was downloaded, or before codes existed.
    Addresses the database doesn't know get an empty code, so they aren't looked up again;
    home-network plays wait until the server's own place is known. Returns rows updated.
    """
    geo = geo_lookup(settings)
    if not geo.available():
        return 0
    placer = Placer(db, geo)
    updated = 0
    for model in (PlaySession, Listen):
        after = 0
        while True:
            rows = (
                await db.execute(
                    select(model.id, model.server_id, model.address, model.network)
                    .where(
                        model.id > after,
                        model.country_code.is_(None),
                        (model.address.is_not(None)) | (model.network == "lan"),
                    )
                    .order_by(model.id)
                    .limit(FILL_BATCH)
                )
            ).all()
            if not rows:
                break
            after = rows[-1][0]
            for row_id, server_id, address, network in rows:
                place = await placer.place(server_id, address, network)
                values: dict[str, Any]
                if place is not None and place.country_code:
                    values = {
                        "country_code": place.country_code,
                        "continent_code": place.continent_code,
                    }
                    if network != "lan":
                        values["location"] = place.text
                elif network != "lan" and not _is_private(address):
                    values = {"country_code": ""}  # unknown to the database
                else:
                    continue
                await db.execute(update(model).where(model.id == row_id).values(**values))
                updated += 1
            await db.commit()
    return updated


# --- stats per area ----------------------------------------------------------------------


@dataclass
class CountryCount:
    country_code: str
    continent_code: str | None
    listens: int
    listening_ms: int


@dataclass
class Places:
    countries: list[CountryCount]
    located: int
    total: int


async def countries(
    db: AsyncSession, user_id: int, start: dt.datetime | None, end: dt.datetime
) -> Places:
    conditions = stats.in_period(user_id, start, end)
    rows = await db.execute(
        select(
            Listen.country_code,
            func.mode().within_group(Listen.continent_code),
            func.count(),
            stats.listening_time(),
        )
        .where(*conditions, Listen.country_code != "")
        .group_by(Listen.country_code)
        .order_by(func.count().desc())
    )
    found = [CountryCount(str(c), cont, int(n), int(ms or 0)) for c, cont, n, ms in rows.all() if c]
    total = await db.scalar(select(func.count()).where(*conditions)) or 0
    return Places(found, sum(c.listens for c in found), int(total))


@dataclass
class City:
    name: str
    listens: int


@dataclass
class AreaDetail:
    listens: int
    listening_ms: int
    artists: list[stats.Top]
    tracks: list[stats.Top]
    cities: list[City] = field(default_factory=list)


def area_filter(continent: str | None, country: str | None) -> list[ColumnElement[bool]]:
    if country:
        return [Listen.country_code == country.upper()]
    if continent:
        return [Listen.continent_code == continent.upper()]
    return [Listen.country_code != ""]


async def area_detail(
    db: AsyncSession,
    user_id: int,
    start: dt.datetime | None,
    end: dt.datetime,
    *,
    continent: str | None,
    country: str | None,
    limit: int = 10,
) -> AreaDetail:
    where = area_filter(continent, country)
    conditions = [*stats.in_period(user_id, start, end), *where]
    count, ms = (
        await db.execute(select(func.count(), stats.listening_time()).where(*conditions))
    ).one()
    artists = await stats.top(db, user_id, "artist", start, end, limit, where=where)
    tracks = await stats.top(db, user_id, "track", start, end, limit, where=where)
    cities: list[City] = []
    if country:
        rows = await db.execute(
            select(Listen.location, func.count())
            .where(*conditions, Listen.location.is_not(None))
            .group_by(Listen.location)
            .order_by(func.count().desc())
            .limit(limit)
        )
        cities = [City(str(loc), int(n)) for loc, n in rows.all()]
    return AreaDetail(int(count), int(ms or 0), artists, tracks, cities)
