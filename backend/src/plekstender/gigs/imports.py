"""Bringing gigs in (setlist.fm, CSV, iCalendar) and taking them out (CSV, JSON, iCalendar).

No service found has an API for *writing* the gigs someone went to, so exports are files
to take elsewhere. Re-importing never duplicates a gig: each keeps its source's reference.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import hashlib
import io
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.models import Gig
from plekstender.gigs.service import MAX_ARTISTS, library_matcher, link_artists
from plekstender.live import feeds, ical
from plekstender.live.jambase import USER_AGENT
from plekstender.sync.parse import normalise_artist

MAX_ROWS = 5000
SETLISTFM_PAGES = 60  # 20 a page: 1,200 setlists
SETLISTFM_INTERVAL = 0.6  # its limit is about 2 requests a second


class ImportError_(Exception):  # (ImportError is a builtin)
    pass


@dataclass
class Draft:
    date: dt.date
    artists: list[str]
    venue: str | None = None
    city: str | None = None
    country: str | None = None
    name: str | None = None
    notes: str | None = None
    url: str | None = None
    source: str = "csv"
    source_ref: str | None = None
    # What was played, per artist: [{"artist", "url", "songs": [{"name", "cover", "tape"}]}].
    setlists: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)


def _clean(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:300] or None


def _ref(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p or "").lower() for p in parts).encode()).hexdigest()[:40]


# --- setlist.fm ----------------------------------------------------------------------------


MAX_SONGS = 150
# A setlist's page: https://www.setlist.fm/setlist/radiohead/2017/pyramid-stage-…-63de4613.html
SETLIST_PATH = "setlist.fm/setlist/"
# The ID at the end of the page's name (alone, or after the last "-").
SETLIST_PAGE = re.compile(r"(?:^|-)([0-9a-f]{6,10})\.html$", re.IGNORECASE)
MAX_LINK = 2000


def setlist_id(url: str) -> str | None:
    """The setlist's ID in a setlist.fm link, if it is one. Read apart rather than searched
    for with one pattern, which took quadratic time on long, repetitive text."""
    text = (url or "").strip()
    start = text.lower().find(SETLIST_PATH)
    if start == -1 or len(text) > MAX_LINK:
        return None
    path = re.split(r"[?#\s]", text[start + len(SETLIST_PATH) :], maxsplit=1)[0]
    found = SETLIST_PAGE.search(path.rsplit("/", 1)[-1])
    return found[1].lower() if found else None


async def _setlistfm_get(
    http: httpx.AsyncClient, url: str, api_key: str, params: dict[str, Any] | None = None
) -> Any:
    """One request to setlist.fm's API: the JSON, or None for "not found"."""
    try:
        resp = await http.get(
            url,
            params=params,
            headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ImportError_(f"setlist.fm couldn't be reached ({exc.__class__.__name__}).") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code in (401, 403):
        raise ImportError_("setlist.fm didn't accept the API key.")
    if resp.status_code == 429:
        raise ImportError_("setlist.fm asked to slow down; try again in a minute.")
    if resp.status_code >= 400:
        raise ImportError_(f"setlist.fm answered {resp.status_code}.")
    return resp.json()


def _join(by_event: dict[str, Draft], draft: Draft) -> None:
    """Another artist's setlist of the same night: one gig, one more act."""
    key = draft.source_ref or ""
    if key not in by_event:
        by_event[key] = draft
        return
    known = by_event[key]
    known.artists += [a for a in draft.artists if a not in known.artists]
    have = {s["artist"] for s in known.setlists}
    known.setlists += [s for s in draft.setlists if s["artist"] not in have]


async def setlistfm_attended(
    http: httpx.AsyncClient, base_url: str, api_key: str, username: str
) -> list[Draft]:
    """The concerts a setlist.fm user went to (one setlist per artist per concert, joined
    into one gig per date and venue), with what each artist played."""
    by_event: dict[str, Draft] = {}
    last = 0.0
    for page in range(1, SETLISTFM_PAGES + 1):
        wait = last + SETLISTFM_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        last = time.monotonic()
        body = await _setlistfm_get(
            http, f"{base_url}/user/{username}/attended", api_key, {"p": page}
        )
        if body is None:
            if page == 1:
                raise ImportError_(
                    f"setlist.fm has no user {username!r} (or they've been to nothing)."
                )
            break
        setlists = body.get("setlist") or [] if isinstance(body, dict) else []
        for item in setlists:
            draft = _setlist(item)
            if draft is not None:
                _join(by_event, draft)
        total = body.get("total") if isinstance(body, dict) else None
        per_page = body.get("itemsPerPage") if isinstance(body, dict) else None
        if not setlists or (
            isinstance(total, int) and isinstance(per_page, int) and page * per_page >= total
        ):
            break
    return list(by_event.values())


async def setlistfm_gig(http: httpx.AsyncClient, base_url: str, api_key: str, url: str) -> Draft:
    """A gig from a link to one of its setlists: the night's details, and the whole bill
    (the other setlists from that date and venue), headliners (the longest sets) first."""
    sid = setlist_id(url)
    if sid is None:
        raise ImportError_("That isn't a link to a setlist on setlist.fm.")
    item = await _setlistfm_get(http, f"{base_url}/setlist/{sid}", api_key)
    draft = _setlist(item) if item is not None else None
    if draft is None:
        raise ImportError_("setlist.fm has no setlist at that link.")
    by_event = {draft.source_ref or "": draft}
    venue_id = (item.get("venue") or {}).get("id")
    if venue_id:
        await asyncio.sleep(SETLISTFM_INTERVAL)
        found = await _setlistfm_get(
            http,
            f"{base_url}/search/setlists",
            api_key,
            {"date": item.get("eventDate"), "venueId": venue_id},
        )
        for other in (found or {}).get("setlist") or [] if isinstance(found, dict) else []:
            if isinstance(other, dict) and other.get("id") != item.get("id"):
                more = _setlist(other)
                if more is not None and more.date == draft.date:
                    _join(by_event, more)
    sizes = {s["artist"]: len(s["songs"]) for s in draft.setlists}
    draft.artists.sort(key=lambda a: -sizes.get(a, 0))  # stable: the linked one first of equals
    draft.setlists.sort(key=lambda s: -len(s["songs"]))
    draft.url = url
    return draft


@dataclass
class Found:
    """A setlist in setlist.fm's search results."""

    draft: Draft
    artist: str
    songs: int


async def setlistfm_search(
    http: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    *,
    artist: str | None = None,
    year: int | None = None,
    city: str | None = None,
    page: int = 1,
    mbid: str | None = None,
    date: dt.date | None = None,
) -> tuple[list[Found], int]:
    """Setlists matching an artist (by name, or MusicBrainz ID), year, date and town (any of
    them), newest first, 20 a page; and how many there are in all."""
    params: dict[str, Any] = {"p": page}
    if mbid:
        params["artistMbid"] = mbid
    elif artist:
        params["artistName"] = artist
    if date:
        params["date"] = date.strftime("%d-%m-%Y")
    if year:
        params["year"] = year
    if city:
        params["cityName"] = city
    body = await _setlistfm_get(http, f"{base_url}/search/setlists", api_key, params)
    if not isinstance(body, dict):
        return [], 0
    found = []
    for item in body.get("setlist") or []:
        draft = _setlist(item)
        if draft is not None:
            songs = len(draft.setlists[0]["songs"]) if draft.setlists else 0
            found.append(Found(draft, draft.artists[0], songs))
    total = body.get("total")
    return found, total if isinstance(total, int) else len(found)


def _songs(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The songs of a setlist, in order, across its sets and encores."""
    sets = (item.get("sets") or {}).get("set") if isinstance(item.get("sets"), dict) else None
    out: list[dict[str, Any]] = []
    for part in sets if isinstance(sets, list) else []:
        for song in (part.get("song") or []) if isinstance(part, dict) else []:
            name = _clean(song.get("name")) if isinstance(song, dict) else None
            if not name:
                continue
            entry: dict[str, Any] = {"name": name}
            cover = (song.get("cover") or {}).get("name")
            if cover:
                entry["cover"] = _clean(cover)
            if song.get("tape"):
                entry["tape"] = True  # played from a recording, not live
            out.append(entry)
    return out[:MAX_SONGS]


def _setlist(item: Any) -> Draft | None:
    if not isinstance(item, dict):
        return None
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", str(item.get("eventDate") or ""))
    artist = _clean((item.get("artist") or {}).get("name"))
    if not m or not artist:
        return None
    try:
        day = dt.date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    venue = item.get("venue") or {}
    city = venue.get("city") or {}
    country = (city.get("country") or {}).get("code")
    tour = (item.get("tour") or {}).get("name")
    venue_ref = venue.get("id") or _ref(venue.get("name"), city.get("name"))
    url = item.get("url") if isinstance(item.get("url"), str) else None
    songs = _songs(item)
    return Draft(
        date=day,
        artists=[artist],
        venue=_clean(venue.get("name")),
        city=_clean(city.get("name")),
        country=str(country).upper()[:2] if country else None,
        name=_clean(tour) if tour and len(str(tour)) < 120 else None,
        url=url,
        source="setlistfm",
        source_ref=f"{day.isoformat()}:{venue_ref}"[:128],
        setlists=[{"artist": artist, "url": url, "songs": songs}] if songs else [],
    )


# --- CSV -------------------------------------------------------------------------------------

COLUMNS = {
    "date": ("date", "event date", "day", "when", "concert date", "show date", "gig date"),
    "artist": (
        "artist",
        "artists",
        "band",
        "bands",
        "headliner",
        "headliners",
        "performer",
        "performers",
        "act",
    ),
    "support": ("support", "supporting", "support acts", "openers", "opening acts", "with"),
    "venue": ("venue", "venue name", "location", "place"),
    "city": ("city", "town", "venue city"),
    "country": ("country", "country code"),
    "name": ("festival", "event", "event name", "tour", "name", "title"),
    "notes": ("notes", "note", "comments", "comment", "memo"),
    "url": ("url", "link", "setlist", "setlist url"),
}
_SPLIT = re.compile(r"\s*(?:;|\|| / |\n)\s*")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def parse_date(text: str) -> dt.date | None:
    """2019-10-02, 02/10/2019 (day first), 2 Oct 2019, Oct 2, 2019, 02.10.2019."""
    text = text.strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    if m := re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$", text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        day, month = (b, a) if b > 12 >= a else (a, b)  # day first unless it can't be
        try:
            return dt.date(y, month, day)
        except ValueError:
            return None
    words = re.findall(r"[A-Za-z]+|\d+", text)
    numbers = [int(w) for w in words if w.isdigit()]
    months = [_MONTHS[w[:3].lower()] for w in words if w[:3].lower() in _MONTHS]
    found_year = next((n for n in numbers if n > 1900), None)
    found_day = next((n for n in numbers if 1 <= n <= 31), None)
    if found_year and found_day and months:
        try:
            return dt.date(found_year, months[0], found_day)
        except ValueError:
            return None
    return None


def parse_csv(text: str) -> tuple[list[Draft], list[str]]:
    """Gigs from a CSV with a header row (column names in many spellings)."""
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    names = {h: (h or "").strip().lower() for h in reader.fieldnames or []}
    columns: dict[str, str] = {}
    for key, aliases in COLUMNS.items():
        for header, lower in names.items():
            if lower in aliases and key not in columns:
                columns[key] = header
    if "date" not in columns or "artist" not in columns:
        raise ImportError_(
            "The CSV needs a header row with (at least) a date and an artist column."
        )
    drafts, problems = [], []
    for i, row in enumerate(reader, start=2):
        if i > MAX_ROWS + 1:
            problems.append(f"Only the first {MAX_ROWS} rows were read.")
            break
        raw_date = (row.get(columns["date"]) or "").strip()
        day = parse_date(raw_date)
        artists = [a for a in _SPLIT.split(row.get(columns["artist"]) or "") if a.strip()]
        if "support" in columns:
            artists += [
                a for a in re.split(r"\s*[;,|]\s*", row.get(columns["support"]) or "") if a.strip()
            ]
        if not day or not artists:
            if any((v or "").strip() for v in row.values()):
                problems.append(f"Row {i}: no date or artist ({raw_date or 'no date'}).")
            continue

        def get(key: str, row: dict[str, Any] = row) -> str | None:
            return _clean(row.get(columns[key])) if key in columns else None

        country = get("country")
        url = get("url")
        drafts.append(
            Draft(
                date=day,
                artists=artists,
                venue=get("venue"),
                city=get("city"),
                country=country.upper() if country and len(country) == 2 else None,
                name=get("name"),
                notes=(row.get(columns["notes"]) or "").strip()[:4000] or None
                if "notes" in columns
                else None,
                url=url if url and url.startswith(("http://", "https://")) else None,
                source="csv",
                source_ref=_ref(day, get("venue"), normalise_artist(artists[0])),
            )
        )
    return drafts, problems


# --- iCalendar --------------------------------------------------------------------------------


def parse_ical(text: str, today: dt.date) -> list[Draft]:
    """Past events in a calendar file (e.g. a Songkick or Google calendar), as gigs."""
    drafts = []
    for event in feeds.events(text, 0, dt.date.min):
        if event.start_date > today:
            continue
        whole, names, _ = feeds.acts(event.name)
        drafts.append(
            Draft(
                date=event.start_date,
                artists=names or [whole],
                venue=event.venue,
                city=event.city,
                name=event.name if event.festival else None,
                url=event.page_url,
                source="ical",
                source_ref=event.id.split(":", 1)[1],
            )
        )
    return drafts


# --- storing ------------------------------------------------------------------------------------


async def store(db: AsyncSession, user_id: int, drafts: list[Draft]) -> ImportResult:
    """Add the drafts as gigs; ones imported before (same source and reference) are
    brought up to date instead. Gigs added by hand on the same day at the same venue with
    the same headliner are left alone (counted as skipped)."""
    result = ImportResult()
    match = await library_matcher(db, user_id)
    existing = list(await db.scalars(select(Gig).where(Gig.user_id == user_id)))
    by_ref: dict[tuple[str, str | None], Gig] = {
        (g.source, g.source_ref): g for g in existing if g.source_ref
    }
    same_day = {
        (
            g.date,
            (g.venue or "").lower(),
            normalise_artist(g.artists[0]["name"]) if g.artists else "",
        )
        for g in existing
    }
    for draft in drafts:
        artists = link_artists(draft.artists[:MAX_ARTISTS], match)
        if not artists:
            continue
        known = by_ref.get((draft.source, draft.source_ref))
        if known is not None:
            names = {a["name"] for a in known.artists}
            known.artists = known.artists + [a for a in artists if a["name"] not in names]
            for attr in ("venue", "city", "country", "name", "url"):
                if getattr(known, attr) is None and getattr(draft, attr):
                    setattr(known, attr, getattr(draft, attr))
            if draft.setlists:
                fresh = {s["artist"] for s in draft.setlists}
                known.setlists = [
                    s for s in known.setlists if s["artist"] not in fresh
                ] + draft.setlists
            result.updated += 1
            continue
        signature = (draft.date, (draft.venue or "").lower(), normalise_artist(artists[0]["name"]))
        if signature in same_day:
            result.skipped += 1
            continue
        gig = Gig(
            user_id=user_id,
            date=draft.date,
            name=draft.name,
            venue=draft.venue,
            city=draft.city,
            country=draft.country,
            notes=draft.notes,
            url=draft.url,
            artists=artists,
            setlists=draft.setlists,
            source=draft.source,
            source_ref=draft.source_ref,
        )
        db.add(gig)
        by_ref[(draft.source, draft.source_ref)] = gig
        same_day.add(signature)
        result.added += 1
    await db.commit()
    return result


# --- exports -----------------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "date",
    "artists",
    "festival",
    "venue",
    "city",
    "country",
    "latitude",
    "longitude",
    "notes",
    "url",
    "source",
]


def to_csv(gigs: list[Gig]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_COLUMNS)
    for g in gigs:
        writer.writerow(
            [
                g.date.isoformat(),
                "; ".join(a["name"] for a in g.artists),
                g.name or "",
                g.venue or "",
                g.city or "",
                g.country or "",
                g.latitude if g.latitude is not None else "",
                g.longitude if g.longitude is not None else "",
                g.notes or "",
                g.url or "",
                g.source,
            ]
        )
    return out.getvalue()


def to_json(gigs: list[Gig]) -> str:
    return json.dumps(
        {
            "gigs": [
                {
                    "date": g.date.isoformat(),
                    "artists": [a["name"] for a in g.artists],
                    "festival": g.name,
                    "venue": g.venue,
                    "city": g.city,
                    "country": g.country,
                    "latitude": g.latitude,
                    "longitude": g.longitude,
                    "notes": g.notes,
                    "url": g.url,
                    "source": g.source,
                }
                for g in gigs
            ]
        },
        ensure_ascii=False,
        indent=2,
    )


def to_ical(gigs: list[Gig]) -> str:
    shows = [
        ical.CalendarShow(
            uid=f"gig-{g.id}@plekstender",
            title=(g.name + ": " if g.name else "") + " + ".join(a["name"] for a in g.artists[:4]),
            start_date=g.date,
            start_time=None,
            end_date=None,
            status="scheduled",
            location=", ".join(x for x in (g.venue, g.city) if x) or None,
            url=g.url,
            description="\n".join(
                x for x in (", ".join(a["name"] for a in g.artists), g.notes) if x
            ),
            updated=g.updated_at,
            got_ticket=True,
        )
        for g in gigs
    ]
    return ical.calendar(shows, "Plekstender: gigs I went to")
