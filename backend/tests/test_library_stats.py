"""Library → Stats: growth, decades, words."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from plekstender.db import get_sessionmaker
from plekstender.db.models import Album
from plekstender.library_stats import words
from tests.fakes import SERVER_URI, FakePlex
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_growth_and_decades(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/library/stats"
    # Dummy was added two months before the rest.
    async with get_sessionmaker()() as db:
        ok_computer = await db.scalar(select(Album).where(Album.rating_key == 200))
        assert ok_computer is not None and ok_computer.added_at is not None
        early = ok_computer.added_at - dt.timedelta(days=62)
        await db.execute(update(Album).where(Album.rating_key == 202).values(added_at=early))
        await db.commit()

    stats = (await owner.get(base)).json()
    assert (stats["artists"], stats["albums"], stats["tracks"]) == (3, 4, 6)
    growth = stats["growth"]
    assert len(growth) == 3  # every month from the first to the last, gaps included
    assert growth[0]["albums"] == 1 and growth[1]["albums"] == 1
    assert growth[-1] == growth[-1] | {"artists": 3, "albums": 4, "tracks": 6}
    assert [(d["decade"], d["albums"]) for d in stats["decades"]] == [
        (1990, 2),
        (2010, 1),
        (2020, 1),
    ]

    nineties = (await owner.get(f"{base}/released", params={"decade": 1990})).json()
    assert [(b["decade"], b["albums"]) for b in nineties["buckets"] if b["albums"]] == [
        (1994, 1),
        (1997, 1),
    ]
    # OK Computer has a full date (May); Dummy only a year in the fake (month unknown).
    year = (await owner.get(f"{base}/released", params={"year": 1997})).json()
    assert [(b["decade"], b["albums"]) for b in year["buckets"] if b["albums"]] == [(5, 1)]
    assert [(a["title"], a["artist_title"]) for a in year["albums"]] == [
        ("OK Computer", "Radiohead")
    ]
    dummy = (await owner.get(f"{base}/released", params={"year": 1994})).json()
    assert [(b["decade"], b["albums"]) for b in dummy["buckets"] if b["albums"]] == [(0, 1)]


async def test_words_in_names(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    found = (
        await owner.get(f"/api/servers/{sid}/library/stats/words", params={"kind": "track"})
    ).json()
    # Karma Police and its remaster count once; "(Remastered)" is left out.
    counts = {w["word"]: w["count"] for w in found}
    assert counts["Karma"] == 1 and counts["Police"] == 1
    assert "Remastered" not in counts
    albums = (
        await owner.get(f"/api/servers/{sid}/library/stats/words", params={"kind": "album"})
    ).json()
    assert {"word": "Computer", "count": 2} in albums


def test_word_counting() -> None:
    names = [
        "The Wind Cries Mary",
        "Mary Had a Little Lamb (2011 Remaster)",
        "Don\u2019t Stop Me Now",
        "don't stop believin'",
        "Mary, Mary [feat. Someone]",
        "Det är vi som är här",
    ]
    counted = dict(words(names))
    assert counted["Mary"] == 3  # once per name
    assert counted["Stop"] == 2 and "Don't" not in counted  # a stop word, whichever apostrophe
    assert "Remaster" not in counted and "Someone" not in counted and "feat" not in counted
    assert "är" not in counted and "the" not in {k.lower() for k in counted}


async def test_where_artists_are_from(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from plekstender import artist_countries, musicbrainz

    monkeypatch.setattr(musicbrainz, "INTERVAL", 0.0)
    monkeypatch.setattr(artist_countries, "PAUSE", 0.0)
    musicbrainz.clear_cache()
    searched: list[str] = []

    def search(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        searched.append(query)
        if query == 'artist:"Portishead"':
            found = [
                {"name": "Portishead", "score": 100, "area": {"iso-3166-2-codes": ["GB-ENG"]}},
                {"name": "Portishead Tribute", "score": 60, "country": "US"},
            ]
        else:
            found = []
        return httpx.Response(200, json={"artists": found})

    plex.router.get("https://musicbrainz.org/ws/2/artist").mock(side_effect=search)

    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/library/stats/countries"
    # Radiohead's country comes from Plex; the others wait for MusicBrainz.
    before = (await owner.get(base)).json()
    assert before == {
        "countries": [{"country_code": "GB", "artists": 1}],
        "unknown": 2,
        "pending": 2,
    }

    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        assert await artist_countries.look_up(db, http) == 2
        assert await artist_countries.look_up(db, http) == 0  # not again (for a while)
    assert sorted(searched) == ['artist:"Portishead"', 'artist:"Secret Band"']

    after = (await owner.get(base)).json()
    assert after == {
        "countries": [{"country_code": "GB", "artists": 2}],
        "unknown": 1,
        "pending": 0,
    }
    gb = (await owner.get(f"{base}/gb")).json()
    assert {a["title"] for a in gb} == {"Radiohead", "Portishead"}
    assert (await owner.get(f"{base}/SE")).json() == []


def test_country_names_and_areas() -> None:
    from plekstender.countries import country_code
    from plekstender.musicbrainz import artist_country_of

    assert [country_code(n) for n in ("United Kingdom", "USA", "Côte d'Ivoire", "gb")] == [
        "GB",
        "US",
        "CI",
        "GB",
    ]
    assert country_code("Atlantis") is None and country_code(None) is None
    assert artist_country_of({"country": "se"}) == "SE"
    assert artist_country_of({"area": {"iso-3166-2-codes": ["GB-SCT"]}}) == "GB"
    assert artist_country_of({"begin-area": {"iso-3166-1-codes": ["IS"]}}) == "IS"
    assert artist_country_of({}) is None


def _cover(*parts: tuple[tuple[int, int, int], float]) -> bytes:
    """A JPEG in horizontal bands: (colour, share of the height)."""
    import io

    from PIL import Image

    image = Image.new("RGB", (64, 64))
    top = 0
    for colour, share in parts:
        height = round(64 * share)
        image.paste(colour, (0, top, 64, min(64, top + height)))
        top += height
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


def test_main_colours() -> None:
    from plekstender.cover_colours import family, main_colour

    def fam(data: bytes) -> str:
        c = main_colour(data)
        return family(c.hue, c.saturation, c.lightness)

    assert fam(_cover(((200, 30, 30), 1))) == "red"
    assert fam(_cover(((40, 80, 200), 1))) == "blue"
    # A small red logo on a black sleeve stays black; half orange, half grey reads orange.
    assert fam(_cover(((10, 10, 10), 0.85), ((200, 30, 30), 0.15))) == "black"
    assert fam(_cover(((128, 128, 128), 0.5), ((240, 140, 20), 0.5))) == "orange"
    assert fam(_cover(((250, 250, 248), 1))) == "white"
    assert family(350, 0.8, 0.5) == "red" and family(10, 0.8, 0.5) == "red"
    assert family(200, 0.05, 0.5) == "grey"


async def test_cover_colours(app: FastAPI, plex: FakePlex) -> None:
    import httpx

    from plekstender.config import get_settings
    from plekstender.cover_colours import colour_covers

    # OK Computer and its reissue blue, Dummy red, Secret Album unreadable.
    def photo(request: httpx.Request) -> httpx.Response:
        url = request.url.params["url"]
        if "/203/" in url:
            return httpx.Response(
                200, content=b"not an image", headers={"content-type": "image/jpeg"}
            )
        colour = (200, 30, 30) if "/202/" in url else (40, 80, 200)
        return httpx.Response(
            200, content=_cover((colour, 1)), headers={"content-type": "image/jpeg"}
        )

    owner, sid = await owner_with_library(app, plex)
    plex.router.get(f"{SERVER_URI}/photo/:/transcode").mock(side_effect=photo)
    base = f"/api/servers/{sid}/library/stats/colours"
    before = (await owner.get(base)).json()
    assert (before["albums"], before["waiting"], before["families"]) == (0, 4, [])

    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        assert await colour_covers(db, http, get_settings()) == 4
        assert await colour_covers(db, http, get_settings()) == 0  # only new or changed covers

    after = (await owner.get(base)).json()
    assert [(f["name"], f["albums"]) for f in after["families"]] == [("red", 1), ("blue", 2)]
    assert (after["albums"], after["waiting"], len(after["strip"])) == (3, 0, 3)
    blue = (await owner.get(f"{base}/blue")).json()
    assert {a["title"] for a in blue} == {"OK Computer", "OK Computer OKNOTOK"}
    assert all(a["artist_title"] == "Radiohead" and a["hex"].startswith("#") for a in blue)
    assert (await owner.get(f"{base}/mauve")).status_code == 422


async def test_recently_added(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    base = f"/api/servers/{sid}/library/recent"
    # The fake adds items in key order: the highest key last.
    albums = (await owner.get(base, params={"limit": 2})).json()
    assert [(a["title"], a["subtitle"]) for a in albums] == [
        ("Secret Album", "Secret Band"),
        ("Dummy", "Portishead"),
    ]
    artists = (await owner.get(base, params={"kind": "artist"})).json()
    assert [a["title"] for a in artists] == ["Secret Band", "Portishead", "Radiohead"]
