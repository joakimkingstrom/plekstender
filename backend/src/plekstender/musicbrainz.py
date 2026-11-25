"""MusicBrainz's web service (no key needed): searching releases and reading one release with
its tracks, the way Picard offers them.

MusicBrainz allows one request per second per client, so requests wait their turn; answers
are kept for a day (in memory) so going back and forth between releases is quick. When it's
busy (503, over its rate limit) a request waits and tries again a few times. Requests carry
Plekstender's User-Agent, with the admin's contact (MUSICBRAINZ_CONTACT) if one is set, and
nothing about the person using it.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from plekstender import __version__

URL = "https://musicbrainz.org/ws/2"
COVER_URL = "https://coverartarchive.org/release/{id}/front-250"
USER_AGENT = f"Plekstender/{__version__} (self-hosted music library tool)"
# Waits (seconds) before trying again when MusicBrainz is busy, unless it says how long.
BUSY_WAITS = (3.0, 10.0, 30.0)
MAX_RETRY_AFTER = 60.0
MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
INTERVAL = 1.0  # seconds between requests (MusicBrainz's limit)
CACHE_FOR = 24 * 3600
CACHE_SIZE = 500
TIMES = "\u00d7"  # the multiplication sign, as in 2xCD

_lock = asyncio.Lock()
_last = 0.0
_cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()


class MusicBrainzError(Exception):
    pass


class NotFound(MusicBrainzError):
    """MusicBrainz has no such thing (400 / 404), as opposed to being unreachable or busy."""


def clear_cache() -> None:
    _cache.clear()


async def _get(
    http: httpx.AsyncClient, path: str, params: dict[str, str], *, cache: bool = True
) -> Any:
    global _last
    key = f"{path}?{urlencode(sorted(params.items()))}"
    hit = _cache.get(key) if cache else None
    if hit is not None and time.monotonic() - hit[0] < CACHE_FOR:
        _cache.move_to_end(key)
        return hit[1]
    # Waits hold the lock: while MusicBrainz says it's busy, nothing else here asks it.
    async with _lock:
        for attempt in range(len(BUSY_WAITS) + 1):
            wait = _last + INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await http.get(
                    f"{URL}{path}",
                    params={**params, "fmt": "json"},
                    headers={"User-Agent": user_agent(), "Accept": "application/json"},
                    timeout=20,
                )
            except httpx.HTTPError as exc:
                raise MusicBrainzError(f"MusicBrainz can't be reached: {exc}") from exc
            finally:
                _last = time.monotonic()
            if resp.status_code != 503 or attempt == len(BUSY_WAITS):
                break
            await asyncio.sleep(busy_wait(resp, attempt))
    if resp.status_code in (400, 404):
        raise NotFound("MusicBrainz doesn't have that release.")
    if resp.status_code == 503:
        raise MusicBrainzError("MusicBrainz is busy; try again in a moment.")
    if resp.status_code >= 400:
        raise MusicBrainzError(f"MusicBrainz answered {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError as exc:
        raise MusicBrainzError("MusicBrainz's answer wasn't readable.") from exc
    if cache:
        _cache[key] = (time.monotonic(), data)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return data


def user_agent() -> str:
    """Plekstender's User-Agent for MusicBrainz, with the admin's contact if one is set."""
    from plekstender.config import get_settings

    contact = (get_settings().musicbrainz_contact or "").strip()
    if not contact:
        return USER_AGENT
    return f"Plekstender/{__version__} ( {contact} )"


def busy_wait(resp: httpx.Response, attempt: int) -> float:
    """How long to wait after a 503: what MusicBrainz says (Retry-After, in seconds), else
    a little longer each time."""
    try:
        said = float(resp.headers.get("retry-after", ""))
    except ValueError:
        said = 0.0
    if said > 0:
        return min(said, MAX_RETRY_AFTER)
    return BUSY_WAITS[attempt]


def credit(artist_credit: list[dict[str, Any]] | None) -> str:
    """ "Artist A feat. Artist B", as credited on the release."""
    return "".join(
        f"{c.get('name') or (c.get('artist') or {}).get('name', '')}{c.get('joinphrase', '')}"
        for c in artist_credit or []
    ).strip()


def _first_artist_id(artist_credit: list[dict[str, Any]] | None) -> str | None:
    for c in artist_credit or []:
        artist_id = (c.get("artist") or {}).get("id")
        if artist_id:
            return str(artist_id)
    return None


def _formats(media: list[dict[str, Any]]) -> str:
    """The media: "CD", two CDs, "CD + DVD", "Digital Media"."""
    counts: dict[str, int] = {}
    for m in media:
        name = m.get("format") or "(unknown format)"
        counts[name] = counts.get(name, 0) + 1
    return " + ".join(f"{n}{TIMES}{f}" if n > 1 else f for f, n in counts.items())


@dataclass
class Candidate:
    id: str
    score: int
    title: str
    artist: str
    date: str | None
    country: str | None
    formats: str
    track_count: int
    label: str | None
    catalog_number: str | None
    type: str | None
    status: str | None
    disambiguation: str | None
    barcode: str | None


def _candidate(r: dict[str, Any], score: int | None = None) -> Candidate:
    labels = r.get("label-info") or []
    label = next(
        (li["label"]["name"] for li in labels if (li.get("label") or {}).get("name")), None
    )
    catalog = next((li["catalog-number"] for li in labels if li.get("catalog-number")), None)
    group = r.get("release-group") or {}
    media = r.get("media") or []
    kinds = [group.get("primary-type"), *(group.get("secondary-types") or [])]
    return Candidate(
        id=r["id"],
        score=int(r.get("score", 100) if score is None else score),
        title=r.get("title") or "",
        artist=credit(r.get("artist-credit")),
        date=r.get("date") or None,
        country=r.get("country") or None,
        formats=_formats(media),
        track_count=int(r.get("track-count") or sum(int(m.get("track-count") or 0) for m in media)),
        label=label,
        catalog_number=catalog,
        type=" + ".join(k for k in kinds if k) or None,
        status=r.get("status") or None,
        disambiguation=r.get("disambiguation") or None,
        barcode=r.get("barcode") or None,
    )


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def search(
    http: httpx.AsyncClient, album: str, artist: str | None, limit: int = 15
) -> list[Candidate]:
    """Releases by album title (and artist). A MusicBrainz release link or ID finds that one."""
    found = MBID.search(album.lower())
    if found:
        return [_candidate(_raw_release_summary(await _release_raw(http, found.group(0))))]
    album = " ".join(album.split())
    if not album:
        return []
    query = f"release:{_quote(album)}"
    if artist and artist.strip():
        query += f" AND artist:{_quote(' '.join(artist.split()))}"
    data = await _get(http, "/release", {"query": query, "limit": str(limit)})
    releases = data.get("releases") or []
    if not releases:  # not word for word: any of the words
        words = re.sub(r'[+\-!(){}\[\]^"~*?:\\/&|]', " ", album)
        query = f"release:({words})" + (
            f" AND artist:{_quote(' '.join(artist.split()))}" if artist and artist.strip() else ""
        )
        data = await _get(http, "/release", {"query": query, "limit": str(limit)})
        releases = data.get("releases") or []
    return [_candidate(r) for r in releases]


@dataclass
class ReleaseTrack:
    id: str  # the track on this release
    recording_id: str
    disc: int
    position: int
    title: str
    artist: str
    artist_id: str | None
    length_ms: int | None


@dataclass
class Release:
    id: str
    title: str
    artist: str
    artist_id: str | None
    date: str | None
    original_date: str | None
    country: str | None
    release_group_id: str | None
    discs: int
    summary: Candidate
    tracks: list[ReleaseTrack] = field(default_factory=list)

    def tracks_on(self, disc: int) -> int:
        return sum(1 for t in self.tracks if t.disc == disc)


async def _release_raw(http: httpx.AsyncClient, mbid: str) -> dict[str, Any]:
    if not MBID.fullmatch(mbid):
        raise MusicBrainzError("That isn't a MusicBrainz release ID.")
    data: dict[str, Any] = await _get(
        http, f"/release/{mbid}", {"inc": "artist-credits+labels+recordings+release-groups"}
    )
    return data


def _raw_release_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "track-count": sum(len(m.get("tracks") or []) for m in r.get("media") or [])}


async def release(http: httpx.AsyncClient, mbid: str) -> Release:
    """A release with every track (disc by disc)."""
    r = await _release_raw(http, mbid)
    media = [m for m in r.get("media") or [] if m.get("tracks")]
    tracks = []
    for disc, m in enumerate(media, start=1):
        for t in m.get("tracks") or []:
            recording = t.get("recording") or {}
            artist_credit = t.get("artist-credit") or recording.get("artist-credit")
            tracks.append(
                ReleaseTrack(
                    id=t["id"],
                    recording_id=recording.get("id", ""),
                    disc=int(m.get("position") or disc),
                    position=int(t.get("position") or 0),
                    title=t.get("title") or recording.get("title") or "",
                    artist=credit(artist_credit) or credit(r.get("artist-credit")),
                    artist_id=_first_artist_id(artist_credit)
                    or _first_artist_id(r.get("artist-credit")),
                    length_ms=t.get("length") or recording.get("length"),
                )
            )
    group = r.get("release-group") or {}
    return Release(
        id=r["id"],
        title=r.get("title") or "",
        artist=credit(r.get("artist-credit")),
        artist_id=_first_artist_id(r.get("artist-credit")),
        date=r.get("date") or None,
        original_date=group.get("first-release-date") or None,
        country=r.get("country") or None,
        release_group_id=group.get("id"),
        discs=len(media),
        summary=_candidate(_raw_release_summary(r)),
        tracks=tracks,
    )


async def cover(http: httpx.AsyncClient, mbid: str) -> bytes | None:
    """The release's front cover (250 px) from the Cover Art Archive, if it has one."""
    if not MBID.fullmatch(mbid):
        return None
    try:
        resp = await http.get(
            COVER_URL.format(id=mbid),
            headers={"User-Agent": user_agent()},
            timeout=20,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
        return None
    return resp.content


# --- artists' countries ------------------------------------------------------------------------


def _area_country(area: dict[str, Any] | None) -> str | None:
    """An area's country: its ISO 3166-1 code, else the country part of its 3166-2 code
    (a city or region: "GB-ENG")."""
    if not area:
        return None
    for code in area.get("iso-3166-1-codes") or []:
        return str(code)[:2].upper()
    for code in area.get("iso-3166-2-codes") or []:
        return str(code)[:2].upper()
    return None


def artist_country_of(artist: dict[str, Any]) -> str | None:
    """Where an artist is from: MusicBrainz's country, else their area's or begin area's."""
    country = artist.get("country")
    if country:
        return str(country).upper()
    return _area_country(artist.get("area")) or _area_country(artist.get("begin-area"))


async def artist_country(http: httpx.AsyncClient, mbid: str) -> str | None:
    """An artist's country by MusicBrainz ID (NotFound when MusicBrainz doesn't know it)."""
    if not MBID.fullmatch(mbid):
        raise NotFound("Not a MusicBrainz ID.")
    return artist_country_of(await _get(http, f"/artist/{mbid}", {}))


def _fold(name: str) -> str:
    return " ".join(name.casefold().split())


async def find_artist_country(http: httpx.AsyncClient, name: str) -> str | None:
    """An artist's country by name: only when MusicBrainz is sure (a 100 score for the exact
    name) and every artist of that name it's sure about is from the same country."""
    name = " ".join(name.split())
    if not name:
        return None
    data = await _get(http, "/artist", {"query": f"artist:{_quote(name)}", "limit": "5"})
    exact = [
        a
        for a in data.get("artists") or []
        if int(a.get("score") or 0) == 100 and _fold(str(a.get("name") or "")) == _fold(name)
    ]
    countries = {artist_country_of(a) for a in exact}
    return countries.pop() if len(countries) == 1 else None


async def release_groups(http: httpx.AsyncClient, artist_mbid: str) -> list[dict[str, Any]]:
    """Every release group credited to an artist (albums, EPs, singles, live...), a hundred a
    request. Not cached: the worker reads each artist rarely, and the cache is for the tag
    editor."""
    out: list[dict[str, Any]] = []
    while True:
        data = await _get(
            http,
            "/release-group",
            {"artist": artist_mbid, "limit": "100", "offset": str(len(out))},
            cache=False,
        )
        page = data.get("release-groups") or []
        out.extend(page)
        if not page or len(out) >= int(data.get("release-group-count") or 0):
            return out
