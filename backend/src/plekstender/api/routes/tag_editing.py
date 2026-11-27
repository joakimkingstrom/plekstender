"""Library → Tag editing: albums whose files need a look, and MusicBrainz's best match for
each (so several albums can be fixed in a row). Server owners only, like the other file
pages; the edits themselves go through the album endpoints in files.py, one edit per album.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from plekstender import musicbrainz
from plekstender.api.deps import DbDep, HttpDep, UserDep
from plekstender.api.routes.artwork import owner_server
from plekstender.api.routes.files import _album_files, _mb_error, _mb_plan, differences
from plekstender.api.routes.identify import suggested_releases
from plekstender.api.schemas import (
    AlbumIssues,
    AlbumIssuesPage,
    MbApplyIn,
    MbBestOut,
    MbReleaseSummary,
    SkipAlbumsIn,
)
from plekstender.db.models import Album, Artist, ItemTag, MediaFile, SkippedAlbum, Tag, Track

router = APIRouter(prefix="/api/servers/{server_id}/files", tags=["files"])

Issue = Literal["any", "differs", "no_mbid", "missing", "errors", "all"]
# Tags every song should have; one missing is worth a look.
NEEDED = ("title", "artist", "album", "tracknumber")


@dataclass
class _Counts:
    files: int = 0
    differs: int = 0
    no_mbid: int = 0
    missing: int = 0
    errors: int = 0
    fields: set[str] = field(default_factory=set)

    def has(self, issue: Issue) -> bool:
        if issue == "all":
            return True
        if issue == "any":
            return bool(self.differs or self.no_mbid or self.missing or self.errors)
        return bool(getattr(self, issue))


@router.get("/albums", response_model=AlbumIssuesPage)
async def album_issues(
    server_id: int,
    db: DbDep,
    user: UserDep,
    issue: Issue = "any",
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    skipped_within: int = Query(
        default=0,
        ge=0,
        le=3650,
        description="Leave out albums you skipped in a MusicBrainz lookup this many days ago "
        "or less (0: show them).",
    ),
) -> AlbumIssuesPage:
    """Albums with scanned files, and what's off about them: files whose tags differ from
    Plex's metadata, without a MusicBrainz release ID, missing a title, artist, album or
    track number, or that couldn't be read. Sorted by artist, then year."""
    us = await owner_server(db, user, server_id)
    sections = us.music_section_keys

    # Each album's genres in Plex, as the album page compares them.
    genres: dict[int, list[str]] = defaultdict(list)
    for key, name in await db.execute(
        select(ItemTag.rating_key, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .join(
            Album, (Album.server_id == ItemTag.server_id) & (Album.rating_key == ItemTag.rating_key)
        )
        .where(ItemTag.server_id == us.server_id, Tag.kind == "genre")
    ):
        genres[key].append(name)

    albums = {
        a.rating_key: a
        for a in await db.scalars(
            select(Album).where(Album.server_id == us.server_id, Album.section_key.in_(sections))
        )
    }
    artists = dict(
        (
            await db.execute(
                select(Artist.rating_key, Artist.title).where(
                    Artist.server_id == us.server_id, Artist.section_key.in_(sections)
                )
            )
        ).all()
    )

    counts: dict[int, _Counts] = defaultdict(_Counts)
    track_artist = aliased(Artist)
    rows = await db.execute(
        select(
            Track.album_rating_key,
            Track.title,
            Track.number,
            Track.disc,
            Track.original_title,
            track_artist.title,
            MediaFile.tags,
            MediaFile.error,
        )
        .join(
            MediaFile,
            (MediaFile.server_id == Track.server_id) & (MediaFile.rating_key == Track.rating_key),
        )
        .outerjoin(
            track_artist,
            (track_artist.server_id == Track.server_id)
            & (track_artist.rating_key == Track.artist_rating_key),
        )
        .where(Track.server_id == us.server_id, Track.section_key.in_(sections))
    )
    for album_key, title, number, disc, original, artist_name, tags, error in rows:
        album = albums.get(album_key) if album_key is not None else None
        if album is None:
            continue
        c = counts[album.rating_key]
        c.files += 1
        if error:
            c.errors += 1
            continue
        tags = tags or {}
        plex = {
            "title": title,
            "artist": original or artist_name,
            "album": album.title,
            "albumartist": artists.get(album.artist_rating_key)
            if album.artist_rating_key
            else None,
            "track": number,
            "disc": disc,
            "year": album.year,
            "genre": genres.get(album.rating_key, []),
        }
        diff = differences(plex, tags)
        if diff:
            c.differs += 1
            c.fields.update(diff)
        if not tags.get("musicbrainz_albumid"):
            c.no_mbid += 1
        if any(not tags.get(f) for f in NEEDED):
            c.missing += 1

    skipped = dict(
        (
            await db.execute(
                select(SkippedAlbum.rating_key, SkippedAlbum.skipped_at).where(
                    SkippedAlbum.user_id == user.id, SkippedAlbum.server_id == us.server_id
                )
            )
        ).all()
    )
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=skipped_within)
    needle = (q or "").strip().lower()
    found = []
    skipped_hidden = 0
    for key, c in counts.items():
        album = albums[key]
        artist = artists.get(album.artist_rating_key) if album.artist_rating_key else None
        if needle and needle not in album.title.lower() and needle not in (artist or "").lower():
            continue
        if not c.has(issue):
            continue
        if skipped_within and (when := skipped.get(key)) is not None and when >= since:
            skipped_hidden += 1
            continue
        found.append((album, artist, c))
    found.sort(
        key=lambda x: ((x[1] or "").lower(), x[0].year or 9999, x[0].title.lower(), x[0].rating_key)
    )

    not_in_plex = await db.scalar(
        select(func.count()).where(
            MediaFile.server_id == us.server_id,
            MediaFile.rating_key.is_(None),
            MediaFile.error.is_(None),
        )
    )
    totals = {
        name: sum(1 for c in counts.values() if c.has(name))
        for name in ("any", "differs", "no_mbid", "missing", "errors")
    }
    return AlbumIssuesPage(
        items=[
            AlbumIssues(
                rating_key=album.rating_key,
                title=album.title,
                year=album.year,
                thumb=album.thumb,
                artist_rating_key=album.artist_rating_key,
                artist_title=artist,
                files=c.files,
                differs=c.differs,
                differing_fields=sorted(c.fields),
                no_mbid=c.no_mbid,
                missing=c.missing,
                errors=c.errors,
                skipped_at=skipped.get(album.rating_key),
            )
            for album, artist, c in found[offset : offset + limit]
        ],
        total=len(found),
        offset=offset,
        limit=limit,
        albums=len(counts),
        counts=totals,
        not_in_plex=not_in_plex or 0,
        skipped_hidden=skipped_hidden,
    )


# How sure a proposal must be to be accepted with the others ("a clear match").
CLEAR_SEARCH_SCORE = 95
CLEAR_SOUND_SHARE = 80


@router.post("/albums/{album_key}/musicbrainz/best", response_model=MbBestOut)
async def best_release(
    server_id: int, album_key: int, db: DbDep, user: UserDep, http: HttpDep
) -> MbBestOut:
    """MusicBrainz's most likely release for an album's files, and whether it's a clear
    match: every file matched to a track of a release with as many tracks, found by the
    release ID already in the files, by sound (most files), or by a near-certain search.
    Nothing is written."""
    us = await owner_server(db, user, server_id)
    album = await db.get(Album, (us.server_id, album_key))
    if album is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Album not found")
    files = await _album_files(db, us.server_id, album_key)
    if not files:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No scanned files for this album (scan the folder first)."
        )
    n = len(files)

    def pick(candidates: list[MbReleaseSummary], least: int) -> MbReleaseSummary | None:
        good = [c for c in candidates if c.score >= least]
        same = [c for c in good if c.track_count == n]
        best = same or good
        return best[0] if best else None

    chosen: MbReleaseSummary | None = None
    source: Literal["tags", "sound", "search"] | None = None
    ids = {f.tags.get("musicbrainz_albumid") for f in files}
    try:
        if len(ids) == 1 and (tagged := ids.pop()):
            try:
                release = await musicbrainz.release(http, tagged)
                chosen = MbReleaseSummary.model_validate(release.summary, from_attributes=True)
                chosen.score = 100
                source = "tags"
            except musicbrainz.MusicBrainzError:
                chosen = None  # gone from MusicBrainz or mistyped: look again
        if chosen is None:
            by_sound = await suggested_releases(server_id, album_key, db, user)
            chosen, source = pick(by_sound, 50), "sound"
        if chosen is None:
            artist = (
                await db.get(Artist, (us.server_id, album.artist_rating_key))
                if album.artist_rating_key
                else None
            )
            found = await musicbrainz.search(http, album.title, artist.title if artist else None)
            searched = [MbReleaseSummary.model_validate(c, from_attributes=True) for c in found]
            chosen, source = pick(searched, 80), "search"
    except musicbrainz.MusicBrainzError as exc:
        raise _mb_error(exc) from exc

    if chosen is None:
        return MbBestOut(
            release=None, source=None, files=n, matched=0, changes=0, clear=False,
            note="No likely release found on MusicBrainz.",
        )  # fmt: skip
    _release, _files, mapping, planned = await _mb_plan(
        db, http, us.server_id, album_key, MbApplyIn(release_id=chosen.id)
    )
    matched = sum(1 for t in mapping.values() if t is not None)
    sure = (
        source == "tags"
        or (source == "sound" and chosen.score >= CLEAR_SOUND_SHARE)
        or (source == "search" and chosen.score >= CLEAR_SEARCH_SCORE)
    )
    clear = sure and matched == n and chosen.track_count == n
    note = None
    if matched < n:
        note = f"{n - matched} of {n} files didn't match a track."
    elif chosen.track_count != n:
        note = f"The release has {chosen.track_count} tracks; the album has {n} files."
    elif not sure:
        note = "Not a certain match: check it."
    return MbBestOut(
        release=chosen,
        source=source,
        files=n,
        matched=matched,
        changes=len(planned),
        clear=clear,
        note=note,
    )


@router.post("/albums/skipped", status_code=status.HTTP_204_NO_CONTENT)
async def skip_albums(server_id: int, body: SkipAlbumsIn, db: DbDep, user: UserDep) -> Response:
    """Remember that you looked these albums up and skipped them (now), so the list can
    leave them out for a while."""
    us = await owner_server(db, user, server_id)
    now = dt.datetime.now(dt.UTC)
    stmt = insert(SkippedAlbum).values(
        [
            {"user_id": user.id, "server_id": us.server_id, "rating_key": k, "skipped_at": now}
            for k in sorted(set(body.album_keys))
        ]
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "server_id", "rating_key"],
            set_={"skipped_at": stmt.excluded.skipped_at},
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/albums/skipped/{album_key}", status_code=status.HTTP_204_NO_CONTENT)
async def unskip_album(server_id: int, album_key: int, db: DbDep, user: UserDep) -> Response:
    """Forget that you skipped an album: it's listed again."""
    us = await owner_server(db, user, server_id)
    await db.execute(
        delete(SkippedAlbum).where(
            SkippedAlbum.user_id == user.id,
            SkippedAlbum.server_id == us.server_id,
            SkippedAlbum.rating_key == album_key,
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
