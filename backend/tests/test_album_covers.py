# ruff: noqa: E501 (the services' answers, as they come)
"""Library → Artwork → Album covers: measuring Plex's covers, the albums most in need,
covers from open sources, and saving one to Plex."""

from __future__ import annotations

import base64
import io
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select, update

from plekstender import tidal as tidal_api
from plekstender.artwork import covers, sources
from plekstender.artwork import fetch as fetch_mod
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import Album
from tests.fakes import CSRF, SERVER_URI, FakePlex, sign_in
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


def jpeg(width: int, height: int, colour: str = "navy") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, "JPEG")
    return out.getvalue()


# The fake library's covers: OK Computer big, OKNOTOK small, Secret Album wide; Dummy has none.
SIZES = {200: (3000, 3000), 201: (300, 300), 203: (1200, 800)}
DEEZER_COVER = "https://cdn-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg"
RG = "b1392450-e666-3926-a536-22c65f834433"


@pytest.fixture
def web(plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[httpx.Request]]:
    """Plex's cover images, and the open cover sources, on public addresses."""

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(fetch_mod, "resolver", resolve)
    monkeypatch.setattr(sources, "MB_INTERVAL", 0)
    tidal_api._tokens.clear()
    asked: dict[str, list[httpx.Request]] = {"deezer": [], "tidal": [], "musicbrainz": []}
    r = plex.router

    def thumb(request: httpx.Request) -> httpx.Response:
        key = int(request.url.path.split("/")[3])
        size = SIZES.get(key, (1000, 1000))
        return httpx.Response(200, content=jpeg(*size), headers={"content-type": "image/jpeg"})

    r.get(url__regex=rf"{re.escape(SERVER_URI)}/library/metadata/\d+/thumb/\d+").mock(
        side_effect=thumb
    )

    def deezer(request: httpx.Request) -> httpx.Response:
        asked["deezer"].append(request)
        return httpx.Response(200, json={"data": [
            {"title": "Pablo Honey", "artist": {"name": "Radiohead"}, "cover_xl": "https://cdn-images.dzcdn.net/images/cover/zzz/1000x1000-000000-80-0-0.jpg", "cover_big": None},
            {"title": "OK Computer OKNOTOK 1997 2017", "artist": {"name": "Radiohead"}, "cover_xl": DEEZER_COVER, "cover_big": DEEZER_COVER.replace("1000x1000", "500x500")},
        ]})  # fmt: skip

    r.get("https://api.deezer.com/search/album").mock(side_effect=deezer)
    r.get(url__regex=r"https://cdn-images\.dzcdn\.net/.*").mock(
        return_value=httpx.Response(
            200, content=jpeg(1000, 1000), headers={"content-type": "image/jpeg"}
        )
    )
    r.get("https://itunes.apple.com/search").mock(return_value=httpx.Response(200, json={"results": [
        {"collectionName": "OK Computer OKNOTOK 1997 2017", "artistName": "Radiohead", "releaseDate": "2017-06-23T07:00:00Z",
         "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/x/cover.jpg/100x100bb.jpg"},
        {"collectionName": "OK Computer OKNOTOK (Remastered)", "artistName": "Radiohead", "releaseDate": "2017-06-23T07:00:00Z",
         "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/exact/cover.jpg/100x100bb.jpg"},
    ]}))  # fmt: skip
    # Images: Apple sends its 1400 px original when asked for 3000; the archive's are big.
    r.get(url__regex=r"https://is1-ssl\.mzstatic\.com/.*").mock(
        return_value=httpx.Response(
            200, content=jpeg(1400, 1400), headers={"content-type": "image/jpeg"}
        )
    )
    r.get("https://coverartarchive.org/release/r1/1.jpg").mock(
        return_value=httpx.Response(
            200, content=jpeg(1200, 1200), headers={"content-type": "image/jpeg"}
        )
    )
    r.get("https://coverartarchive.org/release/r1/2.jpg").mock(
        return_value=httpx.Response(
            200, content=jpeg(800, 600), headers={"content-type": "image/jpeg"}
        )
    )

    def musicbrainz(request: httpx.Request) -> httpx.Response:
        asked["musicbrainz"].append(request)
        return httpx.Response(200, json={"release-groups": [
            {"id": RG, "score": 100, "title": "OK Computer OKNOTOK 1997 2017",
             "artist-credit": [{"name": "Radiohead", "joinphrase": ""}]},
        ]})  # fmt: skip

    r.get("https://musicbrainz.org/ws/2/release-group").mock(side_effect=musicbrainz)
    r.get(f"https://coverartarchive.org/release-group/{RG}").mock(return_value=httpx.Response(200, json={"images": [
        {"front": False, "types": ["Back"], "image": "https://coverartarchive.org/release/r1/2.jpg", "thumbnails": {"500": "https://coverartarchive.org/release/r1/2-500.jpg"}},
        {"front": True, "types": ["Front"], "image": "https://coverartarchive.org/release/r1/1.jpg", "thumbnails": {"500": "https://coverartarchive.org/release/r1/1-500.jpg"}},
    ]}))  # fmt: skip

    def tidal_token(request: httpx.Request) -> httpx.Response:
        expected = "Basic " + base64.b64encode(b"tidalclient1:tidal-secret").decode()
        if (
            request.headers.get("authorization") != expected
            or b"client_credentials" not in request.content
        ):
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": "tt", "expires_in": 3600})

    r.post("https://auth.tidal.com/v1/oauth2/token").mock(side_effect=tidal_token)

    def tidal(request: httpx.Request) -> httpx.Response:
        asked["tidal"].append(request)
        assert request.headers["authorization"] == "Bearer tt"
        if request.url.path.endswith("/searchResults"):
            return httpx.Response(200, json={"data": [{"id": "q", "type": "searchResults", "relationships": {
                "albums": {"data": [{"id": "77", "type": "albums"}]}}}]})  # fmt: skip
        return httpx.Response(200, json={
            "data": [{"id": "77", "type": "albums", "attributes": {"title": "OK Computer OKNOTOK 1997 2017"},
                      "relationships": {"artists": {"data": [{"id": "9", "type": "artists"}]},
                                        "coverArt": {"data": [{"id": "art1", "type": "artworks"}]}}}],
            "included": [
                {"id": "9", "type": "artists", "attributes": {"name": "Radiohead"}},
                {"id": "art1", "type": "artworks", "attributes": {"mediaType": "IMAGE", "files": [
                    {"href": "https://resources.tidal.com/images/a/1280x1280.jpg", "meta": {"width": 1280, "height": 1280}},
                    {"href": "https://resources.tidal.com/images/a/320x320.jpg", "meta": {"width": 320, "height": 320}},
                ]}},
            ],
        })  # fmt: skip

    r.get(url__regex=r"https://openapi\.tidal\.com/v2/.*").mock(side_effect=tidal)
    return asked


async def measure() -> int:
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        return await covers.measure_covers(db, http, get_settings())


async def test_album_covers(
    app: FastAPI, plex: FakePlex, web: dict[str, list[httpx.Request]]
) -> None:
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:  # Dummy has no cover in Plex
        await db.execute(update(Album).where(Album.rating_key == 202).values(thumb=None))
        await db.commit()
    base = f"/api/servers/{sid}/artwork/albums"

    # Before measuring: not checked yet (and a measuring run is queued).
    first = (await owner.get(base, params={"show": "all"})).json()
    assert first["counts"]["unchecked"] == 3 and first["counts"]["missing"] == 1
    assert await measure() == 3
    assert await measure() == 0  # each cover once (until it changes)

    # The ones that most need a better cover: none first, then the smallest.
    needs = (await owner.get(base)).json()
    assert [(a["title"], a["state"], a["width"]) for a in needs["items"]] == [
        ("Dummy", "missing", None),
        ("OK Computer OKNOTOK", "low", 300),
    ]
    assert needs["counts"] == {"missing": 1, "low": 1, "ok": 2, "unchecked": 0, "kept": 0}
    assert needs["items"][1]["artist"] == "Radiohead"
    wide = next(
        a
        for a in (await owner.get(base, params={"show": "all"})).json()["items"]
        if a["rating_key"] == 203
    )
    assert (wide["width"], wide["height"], wide["state"]) == (1200, 800, "ok")  # 800 px is fine

    # Sources: Deezer (the matching album first, 1000 px), iTunes (up to 3000 px), the Cover
    # Art Archive (release group found on MusicBrainz by name; front first).
    album = f"{base}/201"
    deezer = (await owner.get(f"{album}/candidates", params={"source": "deezer"})).json()
    top = deezer["candidates"][0]
    assert (top["label"], top["width"]) == (
        "OK Computer OKNOTOK 1997 2017 · Radiohead", 1000,
    )  # fmt: skip
    assert parse_qs(urlsplit(str(web["deezer"][0].url)).query)["q"] == [
        'artist:"Radiohead" album:"OK Computer OKNOTOK"'
    ]
    itunes = (await owner.get(f"{album}/candidates", params={"source": "itunes"})).json()
    # Measured: Apple sent its 1400 px original (asked for 3000).
    assert [(c["width"], c["height"], c["up_to"]) for c in itunes["candidates"]] == [
        (1400, 1400, None),
        (1400, 1400, None),
    ]
    caa = (await owner.get(f"{album}/candidates", params={"source": "coverartarchive"})).json()
    # The archive doesn't say how big its images are: read from the start of each.
    assert [(c["label"], c["width"], c["height"]) for c in caa["candidates"]] == [
        ("Front", 1200, 1200),
        ("Back", 800, 600),
    ]
    await owner.get(f"{album}/candidates", params={"source": "coverartarchive"})
    assert len(web["musicbrainz"]) == 1  # the release group found is remembered

    # Tidal: needs an admin's client ID and secret (checked with Tidal).
    tidal = (await owner.get(f"{album}/candidates", params={"source": "tidal"})).json()
    assert tidal["candidates"] == [] and "Tidal isn't set up" in tidal["note"]
    wrong = await owner.put(
        "/api/admin/tidal",
        json={"client_id": "tidalclient1", "client_secret": "nope-nope"},
        headers=CSRF,
    )
    assert wrong.status_code == 400
    ok = await owner.put(
        "/api/admin/tidal",
        json={"client_id": "tidalclient1", "client_secret": "tidal-secret"},
        headers=CSRF,
    )
    assert ok.json() == {"configured": True, "client_id": "tidalclient1"}
    tidal = (await owner.get(f"{album}/candidates", params={"source": "tidal"})).json()
    assert [(c["label"], c["width"]) for c in tidal["candidates"]] == [
        ("OK Computer OKNOTOK 1997 2017 · Radiohead", 1280)
    ]
    assert parse_qs(urlsplit(str(web["tidal"][0].url)).query)["filter[query]"] == [
        "Radiohead OK Computer OKNOTOK"
    ]
    assert parse_qs(urlsplit(str(web["tidal"][1].url)).query)["filter[id]"] == ["77"]

    # Preview, then save the Deezer cover to Plex: measured straight away, out of the list.
    token = deezer["candidates"][0]["token"]
    shown = await owner.post(
        f"{album}/preview", json={"source": {"kind": "candidate", "token": token}}, headers=CSRF
    )
    assert shown.status_code == 200 and shown.headers["x-image-width"] == "1000"
    saved = await owner.post(
        f"{album}/apply", json={"source": {"kind": "candidate", "token": token}}, headers=CSRF
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["width"], saved.json()["state"], saved.json()["source"]) == (
        1000,
        "ok",
        "deezer",
    )
    assert plex.library.uploaded[-1][0] == 201
    assert [a["title"] for a in (await owner.get(base)).json()["items"]] == ["Dummy"]

    # A wide image cropped to a square.
    async def upload(data: bytes) -> str:
        resp = await owner.post(
            f"/api/servers/{sid}/artwork/uploads", content=data,
            headers={**CSRF, "content-type": "application/octet-stream"},
        )  # fmt: skip
        return str(resp.json()["upload_id"])

    wide_upload = await upload(jpeg(1600, 900))
    square = await owner.post(
        f"{base}/202/preview",
        json={"source": {"kind": "upload", "upload_id": wide_upload}, "square": True},
        headers=CSRF,
    )
    assert (square.headers["x-image-width"], square.headers["x-image-height"]) == ("900", "900")
    huge = await upload(jpeg(4000, 4000))
    big = await owner.post(
        f"{base}/202/preview", json={"source": {"kind": "upload", "upload_id": huge}}, headers=CSRF
    )
    assert big.headers["x-image-width"] == "3000"  # at most 3000 px

    # "Keep it": out of the lists to improve.
    assert (
        await owner.put(f"{base}/202/keep", json={"kept": True}, headers=CSRF)
    ).status_code == 204
    after = (await owner.get(base)).json()
    assert after["items"] == [] and after["counts"]["kept"] == 1

    # Only the server's owner.
    friend, _ = await sign_in(app, plex, "friend-token")
    assert (await friend.get(base)).status_code in (403, 404)
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(select(Album.thumb).where(Album.rating_key == 201))
            != "/library/metadata/201/thumb/1700000000"
        )


async def test_best_match(
    app: FastAPI,
    plex: FakePlex,
    web: dict[str, list[httpx.Request]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The best match: the biggest cover of this very album (same title, remaster notes
    aside, and artist) at least 1000 x 1000, from Deezer, iTunes, Tidal and the archive."""
    from plekstender.worker import artwork_job

    monkeypatch.setattr(covers, "BEST_PACE", 0)
    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:  # Dummy has no cover in Plex
        await db.execute(update(Album).where(Album.rating_key == 202).values(thumb=None))
        await db.commit()
    await measure()
    base = f"/api/servers/{sid}/artwork/albums"

    # The sources to use, and the one albums open on, are the user's preferences.
    me = (await owner.get("/api/auth/me")).json()["preferences"]
    assert (me["cover_source"], me["best_cover_sources"]) == (
        "deezer", ["deezer", "itunes", "tidal", "coverartarchive"],
    )  # fmt: skip
    prefs = {**me, "cover_source": "itunes", "best_cover_sources": ["deezer", "coverartarchive"]}
    saved = await owner.put("/api/auth/me/preferences", json=prefs, headers=CSRF)
    assert saved.json()["preferences"]["cover_source"] == "itunes"
    none_chosen = {**prefs, "best_cover_sources": []}
    assert (
        await owner.put("/api/auth/me/preferences", json=none_chosen, headers=CSRF)
    ).status_code == 422
    # Without iTunes: the archive's 1200 px front cover.
    archive = (await owner.post(f"{base}/201/best", headers=CSRF)).json()
    assert (archive["outcome"], archive["source"], archive["width"]) == (
        "applied",
        "coverartarchive",
        1200,
    )
    await owner.put(
        "/api/auth/me/preferences",
        json={**prefs, "best_cover_sources": ["deezer", "itunes", "coverartarchive"]},
        headers=CSRF,
    )

    # OKNOTOK: Deezer has only the 1997-2017 edition (not this album); iTunes has it at
    # 1400 px, the archive at 1200: iTunes wins (bigger than the 1200 px one saved).
    best = (await owner.post(f"{base}/201/best", headers=CSRF)).json()
    assert (best["outcome"], best["source"], best["width"], best["height"]) == (
        "applied", "itunes", 1400, 1400,
    )  # fmt: skip
    assert (best["cover"]["width"], best["cover"]["state"], best["cover"]["source"]) == (
        1400, "ok", "itunes",
    )  # fmt: skip
    assert plex.library.uploaded[-1][0] == 201
    uploads = len(plex.library.uploaded)
    # Asked again: Plex's is as big already, so nothing is saved.
    again = (await owner.post(f"{base}/201/best", headers=CSRF)).json()
    assert again["outcome"] == "already" and len(plex.library.uploaded) == uploads

    # Dummy: nothing anywhere is this album.
    none = (await owner.post(f"{base}/202/best", headers=CSRF)).json()
    assert (none["outcome"], none["cover"], none["source"]) == ("not_found", None, None)

    # Every album the list shows, in the background.
    started = await owner.post(
        f"{base}/best", json={"show": "needs", "sources": ["deezer", "itunes"]}, headers=CSRF
    )
    assert started.status_code == 201, started.text
    job = started.json()
    assert job["kind"] == "covers"
    await artwork_job.func(job_id=job["id"])  # what the worker runs
    [done] = (await owner.get(f"/api/servers/{sid}/artwork/jobs")).json()[:1]
    assert (done["status"], done["progress"]) == ("succeeded", {"done": 1, "total": 1})
    assert done["stats"] == {"applied": 0, "not_found": 1, "already": 0, "failed": 0}
    nothing = await owner.post(f"{base}/best", json={"show": "low"}, headers=CSRF)
    assert nothing.status_code == 422  # OKNOTOK isn't low any more
