"""Album covers (Library → Artwork → Album covers): how big each cover in Plex is, where
better ones come from, and saving one to Plex.

Sources, all open: the Cover Art Archive (MusicBrainz), Deezer's and Apple's (iTunes) public
search, fanart.tv (the instance's key), Tidal (a free developer app's client ID and secret),
Plex's own posters and web search. Nothing about the person is sent: only the artist and
album names (or the album's MusicBrainz ID).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender import tidal as tidal_api
from plekstender.artwork import images, sources
from plekstender.artwork.fetch import USER_AGENT, FetchError, fetch, image_size
from plekstender.artwork.images import ImageError
from plekstender.artwork.sources import Candidate, SourceError
from plekstender.config import Settings
from plekstender.db.models import Album, AlbumCover, UserServer
from plekstender.jobs import check_stop
from plekstender.plex.server import PlexServerClient, client_for
from plekstender.plex.tv import PlexError
from plekstender.sync.parse import normalise_artist, normalise_title

log = logging.getLogger(__name__)

CoverSource = Literal["coverartarchive", "deezer", "itunes", "tidal", "fanart", "plex", "search"]
LOW = 600  # px on the shorter side: below this a cover looks soft on a big screen
MAX_SIDE = 3000  # bigger covers are scaled down to this when saved
MAX_BYTES = 40 * 1024 * 1024
MEASURE_BATCH = 1500
MEASURE_AT_ONCE = 4
COUNTRY = "US"  # the store searched on iTunes and Tidal (they differ a little by country)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class CoverError(Exception):
    """Something the user should be told."""


@dataclass(frozen=True)
class AlbumRef:
    title: str
    artist: str | None
    mbid: str | None  # Plex's (a release or a release group)


# --- measuring what Plex has -----------------------------------------------------------------


async def measure_covers(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, limit: int = MEASURE_BATCH
) -> int:
    """Measure the covers not measured yet (or changed since), server by server with the
    owner's connection. Only the start of each image is read. How many were measured."""
    owners = list(await db.scalars(select(UserServer).where(UserServer.is_owner)))
    done = 0
    for us in owners:
        if done >= limit:
            break
        rows = await db.execute(
            select(Album.rating_key, Album.thumb)
            .outerjoin(
                AlbumCover,
                (AlbumCover.server_id == Album.server_id)
                & (AlbumCover.rating_key == Album.rating_key),
            )
            .where(
                Album.server_id == us.server_id,
                Album.section_key.in_(us.music_section_keys),
                Album.thumb.is_not(None),
                (AlbumCover.thumb.is_(None)) | (AlbumCover.thumb != Album.thumb),
            )
            .order_by(Album.added_at.desc().nulls_last())
            .limit(limit - done)
        )
        todo = [(key, thumb) for key, thumb in rows.all() if thumb]
        if not todo:
            continue
        try:
            client = client_for(settings, http, us)
        except Exception as exc:  # the token can't be read: reconnecting fixes it
            log.info("Cover sizes skipped for server %s: %s", us.server_id, exc)
            continue
        gate = asyncio.Semaphore(MEASURE_AT_ONCE)

        async def one(
            thumb: str, client: PlexServerClient = client, gate: asyncio.Semaphore = gate
        ) -> tuple[int, int] | None:
            async with gate:
                try:
                    return await client.image_size(thumb)
                except (PlexError, httpx.HTTPError) as exc:
                    log.debug("Cover %s not measured: %s", thumb, exc)
                    return None

        sizes = await asyncio.gather(*(one(thumb) for _, thumb in todo))
        now = dt.datetime.now(dt.UTC)
        for (key, thumb), size in zip(todo, sizes, strict=True):
            await _save_size(db, us.server_id, key, thumb, size, now)
        await db.commit()
        done += len(todo)
    return done


async def _save_size(
    db: AsyncSession,
    server_id: int,
    rating_key: int,
    thumb: str | None,
    size: tuple[int, int] | None,
    now: dt.datetime,
    **extra: object,
) -> None:
    values: dict[str, object] = {
        "thumb": thumb,
        "width": size[0] if size else None,
        "height": size[1] if size else None,
        "checked_at": now,
        **extra,
    }
    stmt = insert(AlbumCover).values(server_id=server_id, rating_key=rating_key, **values)
    await db.execute(
        stmt.on_conflict_do_update(index_elements=["server_id", "rating_key"], set_=values)
    )


# --- sources -----------------------------------------------------------------------------------


async def _json(
    http: httpx.AsyncClient,
    name: str,
    url: str,
    *,
    params: Any = None,
    headers: dict[str, str] | None = None,
    missing_ok: bool = False,
) -> Any:
    try:
        resp = await http.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"{name} couldn't be reached.") from exc
    if missing_ok and resp.status_code == 404:
        return None
    if resp.status_code in (401, 403):
        raise SourceError(f"{name} refused the request (HTTP {resp.status_code}).")
    if resp.status_code != 200:
        raise SourceError(f"{name} is unavailable (HTTP {resp.status_code}).")
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(f"{name} sent an answer that isn't JSON.") from exc


def _same(a: str | None, b: str | None, artist: bool = False) -> bool:
    if not a or not b:
        return False
    norm = normalise_artist if artist else normalise_title
    return norm(a) == norm(b)


def _close(found: str, wanted: str) -> bool:
    """The same title, or one inside the other ("OK Computer OKNOTOK 1997 2017")."""
    a, b = normalise_title(found), normalise_title(wanted)
    return bool(a and b) and (a == b or a in b or b in a)


def _is_album(title: str, artist: str, album: AlbumRef) -> bool:
    """This very album: the same title (remaster and edition notes aside) by the same
    artist. Strict, for picking a cover without anyone looking."""
    same_title = normalise_title(title or "-") == normalise_title(album.title)
    return same_title and (not album.artist or _same(artist, album.artist, artist=True))


def _rank(found: list[tuple[Candidate, str, str]], album: AlbumRef) -> list[Candidate]:
    """The same album by the same artist first, then the same artist, then the rest. The
    ones that are this very album are marked (extra["match"])."""

    wanted = normalise_title(album.title)
    for candidate, title, artist in found:
        if _is_album(title, artist, album):
            candidate.extra["match"] = True

    def score(entry: tuple[Candidate, str, str]) -> int:
        _, title, artist = entry
        named = (
            4 if normalise_title(title or "-") == wanted else 2 if _close(title, album.title) else 0
        )
        return -(named + 3 * _same(artist, album.artist, artist=True))

    return [c for c, _, _ in sorted(found, key=score)]


async def release_group(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    cover: AlbumCover,
    album: AlbumRef,
) -> str | None:
    """The album's MusicBrainz release group: found by name when Plex has no MBID (kept)."""
    if cover.mbid:
        return cover.mbid
    if not album.artist:
        return None
    query = (
        f'releasegroup:"{sources.lucene(album.title)}" AND artist:"{sources.lucene(album.artist)}"'
    )
    data = await sources.musicbrainz_search(
        http, settings.musicbrainz_api_url, "release-group", query
    )
    for group in data.get("release-groups") or []:
        credit = " ".join(
            str(c.get("name") or "") + str(c.get("joinphrase") or "")
            for c in group.get("artist-credit") or []
        )
        if (
            int(group.get("score") or 0) >= 90
            and _close(str(group.get("title") or ""), album.title)
            and _same(credit.strip(), album.artist, artist=True)
        ):
            cover.mbid = str(group["id"])
            await db.commit()
            return cover.mbid
    return None


async def cover_art_archive(
    http: httpx.AsyncClient, base: str, mbids: list[str]
) -> list[Candidate]:
    """The front (then other) images of a release group or release, originals with their
    thumbnails. The archive doesn't say how big the originals are."""
    for mbid in dict.fromkeys(mbids):
        for kind in ("release-group", "release"):
            data = await _json(
                http, "The Cover Art Archive", f"{base.rstrip('/')}/{kind}/{mbid}", missing_ok=True
            )
            if not data:
                continue
            out = []
            for image in sorted(data.get("images") or [], key=lambda i: not i.get("front")):
                url = image.get("image")
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                thumbs = image.get("thumbnails") or {}
                kinds = ", ".join(image.get("types") or []) or (
                    "Front" if image.get("front") else ""
                )
                out.append(
                    Candidate(
                        url=url.replace("http://", "https://", 1),
                        thumb_url=thumbs.get("500") or thumbs.get("large") or None,
                        label=" · ".join(x for x in (kinds, image.get("comment")) if x) or None,
                        extra={"front": bool(image.get("front"))},
                    )
                )
            return out
    return []


async def deezer(http: httpx.AsyncClient, base: str, album: AlbumRef) -> list[Candidate]:
    """Deezer's public search: 1000 x 1000 covers."""
    queries = [f'artist:"{album.artist}" album:"{album.title}"'] if album.artist else []
    queries.append(f"{album.artist or ''} {album.title}".strip())
    for query in queries:
        data = await _json(
            http, "Deezer", f"{base.rstrip('/')}/search/album", params={"q": query, "limit": 15}
        )
        if isinstance(data, dict) and data.get("error"):
            raise SourceError("Deezer is busy: try again in a moment.")
        found = []
        for item in (data or {}).get("data") or []:
            url = item.get("cover_xl")
            if not isinstance(url, str) or not url.startswith("https://"):
                continue
            artist = (item.get("artist") or {}).get("name") or ""
            title = str(item.get("title") or "")
            found.append(
                (
                    Candidate(
                        url=url,
                        thumb_url=item.get("cover_big") or None,
                        label=f"{title} · {artist}" if artist else title,
                        width=1000,
                        height=1000,
                    ),
                    title,
                    artist,
                )
            )
        if found:
            return _rank(found, album)
    return []


async def itunes(http: httpx.AsyncClient, base: str, album: AlbumRef) -> list[Candidate]:
    """Apple's public iTunes search (no key): the artwork Apple Music shows, asked for at
    3000 px (Apple sends its original when that's smaller)."""
    data = await _json(
        http,
        "iTunes",
        f"{base.rstrip('/')}/search",
        params={
            "term": f"{album.artist or ''} {album.title}".strip(),
            "entity": "album",
            "limit": 15,
            "country": COUNTRY,
        },
    )
    found = []
    for item in (data or {}).get("results") or []:
        art = item.get("artworkUrl100")
        if not isinstance(art, str) or "100x100bb" not in art:
            continue
        title, artist = str(item.get("collectionName") or ""), str(item.get("artistName") or "")
        year = str(item.get("releaseDate") or "")[:4]
        found.append(
            (
                Candidate(
                    url=art.replace("100x100bb", f"{MAX_SIDE}x{MAX_SIDE}bb"),
                    thumb_url=art.replace("100x100bb", "600x600bb"),
                    label=f"{title} · {artist}{f' ({year})' if year else ''}",
                    extra={"up_to": MAX_SIDE},
                ),
                title,
                artist,
            )
        )
    return _rank(found, album)


async def tidal(
    http: httpx.AsyncClient, settings: Settings, client: tuple[str, str], album: AlbumRef
) -> list[Candidate]:
    """Tidal's catalogue: a search, then the albums found with their cover art (each file's
    size given)."""
    try:
        token = await tidal_api.access_token(http, settings.tidal_auth_url, client)
    except tidal_api.TidalError as exc:
        raise SourceError(str(exc)) from exc
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.api+json"}
    base = settings.tidal_api_url.rstrip("/")
    search = await _json(
        http,
        "Tidal",
        f"{base}/searchResults",
        params={
            "filter[query]": f"{album.artist or ''} {album.title}".strip()[:256],
            "countryCode": COUNTRY,
            "include": "albums",
        },
        headers=headers,
    )
    data = (search or {}).get("data")
    result = (data[0] if data else {}) if isinstance(data, list) else (data or {})
    ids = [
        str(r["id"])
        for r in ((result.get("relationships") or {}).get("albums") or {}).get("data") or []
        if r.get("id")
    ][:10]
    if not ids:
        return []
    albums = await _json(
        http,
        "Tidal",
        f"{base}/albums",
        params=[("filter[id]", i) for i in ids]
        + [("include", "coverArt"), ("include", "artists"), ("countryCode", COUNTRY)],
        headers=headers,
    )
    included = {(i.get("type"), i.get("id")): i for i in (albums or {}).get("included") or []}
    found = []
    for item in (albums or {}).get("data") or []:
        rels = item.get("relationships") or {}
        artist = ", ".join(
            str(
                ((included.get(("artists", a.get("id"))) or {}).get("attributes") or {}).get("name")
                or ""
            )
            for a in (rels.get("artists") or {}).get("data") or []
        ).strip(", ")
        title = str((item.get("attributes") or {}).get("title") or "")
        for ref in (rels.get("coverArt") or {}).get("data") or []:
            art = (included.get(("artworks", ref.get("id"))) or {}).get("attributes") or {}
            if art.get("mediaType") != "IMAGE":
                continue
            files = [
                f
                for f in art.get("files") or []
                if isinstance(f.get("href"), str) and f.get("meta")
            ]
            if not files:
                continue
            files.sort(key=lambda f: int(f["meta"].get("width") or 0))
            big = files[-1]
            small = next((f for f in files if int(f["meta"].get("width") or 0) >= 320), big)
            found.append(
                (
                    Candidate(
                        url=big["href"],
                        thumb_url=small["href"],
                        label=f"{title} · {artist}" if artist else title,
                        width=int(big["meta"].get("width") or 0) or None,
                        height=int(big["meta"].get("height") or 0) or None,
                    ),
                    title,
                    artist,
                )
            )
            break
    return _rank(found, album)


async def fanart_covers(
    http: httpx.AsyncClient, base: str, api_key: str, mbids: list[str]
) -> list[Candidate]:
    """fanart.tv's album covers for a release group, most liked first."""
    for mbid in dict.fromkeys(mbids):
        data = await _json(
            http,
            "fanart.tv",
            f"{base.rstrip('/')}/music/albums/{mbid}",
            params={"api_key": api_key},
            missing_ok=True,
        )
        if not data:
            continue
        out = []
        for group in (data.get("albums") or {}).values():
            for item in group.get("albumcover") or []:
                url = item.get("url")
                if not isinstance(url, str) or not url.startswith("https://"):
                    continue
                likes = int(item.get("likes") or 0)
                out.append(
                    (
                        likes,
                        Candidate(
                            url=url,
                            thumb_url=url.replace("/fanart/", "/preview/", 1),
                            label=f"{likes} like{'s' * (likes != 1)}",
                            likes=likes,
                        ),
                    )
                )
        if out:
            return [c for _, c in sorted(out, key=lambda e: -e[0])]
    return []


# --- sizes, and the best match ---------------------------------------------------------------

MEASURE_CANDIDATES = 6  # at once
BEST_MIN = 1000  # px on the shorter side, for a best match
BEST_SQUARE = 1.05  # longer side / shorter side, at most (a little is cropped away)
BEST_ORDER = ("itunes", "tidal", "coverartarchive", "deezer")  # when sizes are equal
BEST_SOURCES = ("deezer", "itunes", "tidal", "coverartarchive")


async def measure_candidates(
    http: httpx.AsyncClient, candidates: list[Candidate], limit: int = 12
) -> None:
    """Fill in the size of candidates that don't say (the Cover Art Archive, fanart.tv,
    iTunes), read from the start of each image. Ones that can't be read stay unknown."""
    gate = asyncio.Semaphore(MEASURE_CANDIDATES)

    async def one(candidate: Candidate) -> None:
        async with gate:
            try:
                w, h = await image_size(http, candidate.url)
            except FetchError as exc:
                log.debug("couldn't measure %s: %s", candidate.url, exc)
                return
            candidate.width, candidate.height = w, h
            candidate.extra.pop("up_to", None)

    unknown = [c for c in candidates if c.width is None and c.url.startswith("https://")]
    await asyncio.gather(*(one(c) for c in unknown[:limit]))


@dataclass
class BestMatch:
    candidate: Candidate
    source: str

    @property
    def size(self) -> tuple[int, int]:
        return self.candidate.width or 0, self.candidate.height or 0


async def best_match(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    tidal_client: tuple[str, str] | None,
    cover: AlbumCover,
    album: AlbumRef,
    use: Sequence[str] = BEST_SOURCES,
) -> tuple[BestMatch | None, list[str]]:
    """The biggest cover of this very album on Deezer, iTunes, Tidal and the Cover Art
    Archive (those in `use`) that is at least BEST_MIN square. Also says which sources
    couldn't be asked."""
    problems: list[str] = []

    async def caa() -> list[Candidate]:
        mbids = [m for m in (album.mbid,) if m]
        if not mbids and (group := await release_group(db, http, settings, cover, album)):
            mbids.append(group)
        found = await cover_art_archive(http, settings.coverartarchive_url, mbids)
        return [c for c in found if c.extra.get("front")][:1]

    asks: dict[str, Any] = {}
    if "deezer" in use:
        asks["deezer"] = deezer(http, settings.deezer_api_url, album)
    if "itunes" in use:
        asks["itunes"] = itunes(http, settings.itunes_api_url, album)
    if "coverartarchive" in use:
        asks["coverartarchive"] = caa()
    if tidal_client and "tidal" in use:
        asks["tidal"] = tidal(http, settings, tidal_client, album)
    results = await asyncio.gather(*asks.values(), return_exceptions=True)
    picked: list[BestMatch] = []
    for name, result in zip(asks, results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, (SourceError, FetchError)):
                raise result
            problems.append(f"{name}: {result}")
            continue
        first = next((c for c in result if name == "coverartarchive" or c.extra.get("match")), None)
        if first is not None:
            picked.append(BestMatch(first, name))
    await measure_candidates(http, [p.candidate for p in picked])

    def good(p: BestMatch) -> bool:
        w, h = p.size
        return min(w, h) >= BEST_MIN and max(w, h) <= min(w, h) * BEST_SQUARE

    fits = [p for p in picked if good(p)]
    fits.sort(key=lambda p: (-min(min(p.size), MAX_SIDE), BEST_ORDER.index(p.source)))
    return (fits[0] if fits else None), problems


def source_of(ref: str) -> str:
    """Which source an image came from, by its address."""
    if ref.startswith("plex:"):
        return "plex"
    host = ref.split("/")[2] if "//" in ref else ""
    for needle, name in (
        ("coverartarchive.org", "coverartarchive"),
        ("archive.org", "coverartarchive"),
        ("dzcdn.net", "deezer"),
        ("deezer.com", "deezer"),
        ("mzstatic.com", "itunes"),
        ("tidal.com", "tidal"),
        ("fanart.tv", "fanart"),
    ):
        if host.endswith(needle):
            return name
    return "search"


# --- images ------------------------------------------------------------------------------------


async def image_bytes(http: httpx.AsyncClient, client: PlexServerClient, ref: str) -> bytes:
    """The full image behind a reference: a public address, or an image in Plex."""
    try:
        if ref.startswith("plex:"):
            return await client.image(ref.removeprefix("plex:"))
        data, _ = await fetch(http, ref, max_bytes=MAX_BYTES)
        return data
    except (FetchError, PlexError) as exc:
        raise CoverError(str(exc) or "The image couldn't be downloaded.") from exc


def cover_jpeg(data: bytes, square: bool = False) -> tuple[bytes, tuple[int, int]]:
    """The cover as saved: RGB, cropped to a square if asked, at most 3000 px, JPEG."""
    try:
        img = images.open_image(data)
    except ImageError as exc:
        raise CoverError("That image couldn't be read.") from exc
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (0, 0, 0))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        img = flat
    else:
        img = img.convert("RGB")
    if square and img.width != img.height:
        side = min(img.size)
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue(), img.size


async def apply(
    db: AsyncSession,
    client: PlexServerClient,
    album: Album,
    jpeg: bytes,
    size: tuple[int, int],
    source: str,
    source_url: str | None,
    user_id: int,
) -> AlbumCover:
    """Make the image the album's poster in Plex, and note its size (so the lists show it
    straight away) and where it came from."""
    try:
        await client.upload_poster(album.rating_key, jpeg)
    except PlexError as exc:
        raise CoverError(f"Plex didn't accept the image: {exc}") from exc
    try:
        [meta] = await client.metadata([album.rating_key])
        album.thumb = meta.get("thumb") or album.thumb
    except PlexError, ValueError:
        pass
    now = dt.datetime.now(dt.UTC)
    await _save_size(
        db,
        album.server_id,
        album.rating_key,
        album.thumb,
        size,
        now,
        source=source,
        source_url=source_url,
        applied_at=now,
        applied_by=user_id,
        kept=False,
    )
    await db.commit()
    row = await db.get(AlbumCover, (album.server_id, album.rating_key), populate_existing=True)
    assert row is not None
    return row


async def cover_row(db: AsyncSession, album: Album) -> AlbumCover:
    """The album's row of cover facts (made when missing)."""
    row = await db.get(AlbumCover, (album.server_id, album.rating_key))
    if row is None:
        row = AlbumCover(server_id=album.server_id, rating_key=album.rating_key, kept=False)
        db.add(row)
        await db.flush()
    return row


@dataclass
class BestOutcome:
    # applied | not_found (nothing this album and big enough) | already (Plex's is as big)
    outcome: Literal["applied", "not_found", "already"]
    match: BestMatch | None = None
    cover: AlbumCover | None = None
    problems: list[str] = field(default_factory=list)


async def apply_best(
    db: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    client: PlexServerClient,
    tidal_client: tuple[str, str] | None,
    album: Album,
    artist: str | None,
    user_id: int,
    use: Sequence[str] = BEST_SOURCES,
) -> BestOutcome:
    """Find the best match (see best_match) and save it to Plex, unless the cover there is
    already at least as big."""
    row = await cover_row(db, album)
    match, problems = await best_match(
        db, http, settings, tidal_client, row, AlbumRef(album.title, artist, album.mbid), use
    )
    if match is None:
        return BestOutcome("not_found", problems=problems)
    measured = row.thumb == album.thumb and row.width and row.height
    if measured and min(row.width or 0, row.height or 0) >= min(match.size):
        return BestOutcome("already", match, row, problems)
    data = await image_bytes(http, client, match.candidate.url)
    jpeg, size = cover_jpeg(data, square=match.size[0] != match.size[1])
    saved = await apply(db, client, album, jpeg, size, match.source, match.candidate.url, user_id)
    return BestOutcome("applied", match, saved, problems)


BEST_PACE = 3.5  # seconds an album at least, in a job: iTunes allows ~20 searches a minute


async def run_best_covers(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings, job: Any, us: UserServer
) -> None:
    """An artwork job: the best match for each of the albums listed, saved to Plex."""
    from plekstender.artwork import undo
    from plekstender.db.instance import get_tidal_client
    from plekstender.db.models import Artist, ArtworkJob

    keys = [int(k) for k in job.params.get("rating_keys") or []]
    use = [s for s in job.params.get("sources") or BEST_SOURCES if s in BEST_SOURCES]
    client = client_for(settings, http, us)
    tidal_client = await get_tidal_client(db, settings)
    stats = {"applied": 0, "not_found": 0, "already": 0, "failed": 0, **job.stats}
    done = int(job.progress.get("done", 0))  # resume after a restart
    job.progress = {"done": done, "total": len(keys)}
    await db.commit()
    for key in keys[done:]:
        await check_stop(db, ArtworkJob, job.id)
        started = time.monotonic()
        album = await db.get(Album, (us.server_id, key))
        if album is not None:
            artist = (
                await db.get(Artist, (us.server_id, album.artist_rating_key))
                if album.artist_rating_key
                else None
            )
            try:
                before = await undo.remember(db, client, job, "albums", key)
                result = await apply_best(
                    db, http, settings, client, tidal_client, album,
                    artist.title if artist else None, us.user_id, use,
                )  # fmt: skip
                stats[result.outcome] += 1
                if result.outcome == "applied":
                    undo.keep(job, "albums", key, before)
            except (CoverError, SourceError, FetchError) as exc:
                log.info("best cover for %s failed: %s", album.title, exc)
                await db.rollback()
                stats["failed"] += 1
        done += 1
        job = await db.merge(job)
        job.progress = {"done": done, "total": len(keys)}
        job.stats = dict(stats)
        job.updated_at = dt.datetime.now(dt.UTC)
        await db.commit()
        await asyncio.sleep(max(0.0, BEST_PACE - (time.monotonic() - started)))
