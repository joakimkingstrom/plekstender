"""Calendar links (iCal) as a source of shows: Songkick's calendar export, or any other.

Feeds are fetched like any address a user gives (public hosts only, redirects re-checked,
size capped; see artwork.fetch). Each event's title is split into acts ("Radiohead, Caribou
and Floating Points at Alexandra Palace (4 Mar 27)" → three acts at Alexandra Palace).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from plekstender.artwork.fetch import FetchError, fetch
from plekstender.live.events import Event, Performer

MAX_BYTES = 5 * 1024 * 1024
MAX_EVENTS = 2000
PURPOSE = "live_feed.url"


class FeedError(Exception):
    pass


def normalise_url(url: str) -> str:
    """webcal:// links are https://; anything else must already be http(s)."""
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FeedError("Paste a calendar address starting with https:// or webcal://.")
    return url


async def read(http: httpx.AsyncClient, url: str) -> str:
    try:
        body, _ = await fetch(http, url, max_bytes=MAX_BYTES)
    except FetchError as exc:
        raise FeedError(str(exc)) from exc
    text = body.decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text[:2000].upper():
        raise FeedError("That address isn't a calendar (iCal) file.")
    return text


@dataclass
class Prop:
    params: dict[str, str]
    value: str


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _prop(line: str) -> tuple[str, Prop] | None:
    # NAME;PARAM=value;PARAM="quoted:value":VALUE (the first colon outside quotes)
    quoted, split = False, -1
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            split = i
            break
    if split < 0:
        return None
    head, value = line[:split], line[split + 1 :]
    name, *params = head.split(";")
    found = {}
    for param in params:
        key, _, val = param.partition("=")
        found[key.upper()] = val.strip('"')
    return name.upper(), Prop(found, value)


def _text(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m[1] in "nN" else m[1], value).strip()


def calendar_name(text: str) -> str | None:
    for line in _unfold(text):
        if (parsed := _prop(line)) and parsed[0] == "X-WR-CALNAME":
            return _text(parsed[1].value)[:100] or None
        if line.upper() == "BEGIN:VEVENT":
            break
    return None


def _when(prop: Prop | None, default_zone: ZoneInfo | None) -> tuple[dt.date | None, str | None]:
    """The local date and time ("19:30") of a DTSTART / DTEND."""
    if prop is None:
        return None, None
    value = prop.value.strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$", value)
    if not m:
        return None, None
    try:
        day = dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None, None
    if not m[4] or prop.params.get("VALUE", "").upper() == "DATE":
        return day, None
    moment = dt.datetime.combine(day, dt.time(int(m[4]), int(m[5])))
    if m[7] and default_zone:  # UTC: shown in the calendar's own time zone, if it says
        moment = moment.replace(tzinfo=dt.UTC).astimezone(default_zone).replace(tzinfo=None)
    return moment.date(), f"{moment:%H:%M}"


_PAREN = re.compile(r"\s*\([^()]*\)\s*$")
_SPLIT = re.compile(r"\s*(?:,|\s&\s|\s\+\s|\s/\s|\band\b|\bwith\b|\bfeat\.?|\bft\.?)\s*", re.I)


def acts(summary: str) -> tuple[str, list[str], str | None]:
    """An event title's billing (before "at …"), the acts in it (split at commas, "and",
    "with"…; matching tries the whole billing first, for names like "Florence and the
    Machine"), and the venue when it says "… at Venue"."""
    title = _PAREN.sub("", summary).strip()
    venue = None
    if " at " in title:
        title, venue = title.rsplit(" at ", 1)
    title = title.strip()
    names = [n.strip(" -\u2013") for n in _SPLIT.split(title) if n and n.strip(" -\u2013")]
    return title or summary.strip(), names or [summary.strip()], (venue or "").strip() or None


def events(text: str, feed_id: int, today: dt.date) -> list[Event]:
    """The upcoming events in a calendar (ids are "<feed id>:<hash of its UID>")."""
    zone: ZoneInfo | None = None
    found: list[Event] = []
    current: dict[str, Prop] | None = None
    for line in _unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            continue
        if upper == "END:VEVENT":
            if current is not None and (event := _event(current, feed_id, zone, today)):
                found.append(event)
                if len(found) >= MAX_EVENTS:
                    break
            current = None
            continue
        parsed = _prop(line)
        if parsed is None:
            continue
        name, prop = parsed
        if current is None:
            if name == "X-WR-TIMEZONE":
                try:
                    zone = ZoneInfo(prop.value.strip())
                except ZoneInfoNotFoundError, ValueError:
                    zone = None
        else:
            current.setdefault(name, prop)
    return found


def _event(
    props: dict[str, Prop], feed_id: int, zone: ZoneInfo | None, today: dt.date
) -> Event | None:
    start, time = _when(props.get("DTSTART"), zone)
    if start is None:
        return None
    end, _ = _when(props.get("DTEND"), zone)
    if end and "T" not in (props["DTEND"].value if "DTEND" in props else ""):
        end -= dt.timedelta(days=1)  # all-day DTEND is the day after
    last = end if end and end > start else start
    if last < today:
        return None
    summary = _text(props["SUMMARY"].value) if "SUMMARY" in props else ""
    if not summary:
        return None
    _, names, venue_from_title = acts(summary)
    location = _text(props["LOCATION"].value) if "LOCATION" in props else ""
    parts = [p.strip() for p in location.split(",") if p.strip()]
    venue = parts[0] if parts else venue_from_title
    city = parts[1] if len(parts) > 1 else None
    lat = lon = None
    if "GEO" in props:
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[;,]\s*(-?\d+(?:\.\d+)?)\s*$", props["GEO"].value)
        if m:
            lat, lon = float(m[1]), float(m[2])
    uid = props["UID"].value if "UID" in props else f"{summary}|{start}|{location}"
    url = props["URL"].value.strip() if "URL" in props else None
    status = props["STATUS"].value.strip().upper() if "STATUS" in props else ""
    return Event(
        id=f"{feed_id}:{hashlib.sha256(uid.encode()).hexdigest()[:24]}",
        name=summary[:300],
        festival=len(names) > 6 or "festival" in summary.lower(),
        start_date=start,
        start_time=time,
        end_date=last if last != start else None,
        status="cancelled" if status == "CANCELLED" else "scheduled",
        venue=venue,
        city=city,
        region=None,
        country=None,
        latitude=lat,
        longitude=lon,
        ticket_url=None,
        seller=None,
        page_url=url if url and url.startswith(("https://", "http://")) else None,
        performers=[Performer(n, headliner=i == 0) for i, n in enumerate(names)],
    )
