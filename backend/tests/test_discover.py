"""Library → Discover: artists Plex calls similar to many of the library's, not in it yet."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender.db import get_sessionmaker
from plekstender.db.models import PlexServer, SimilarArtist, User
from tests.fakes import CSRF, FakePlex
from tests.test_stats import add_listens
from tests.test_sync import owner_with_library

pytestmark = pytest.mark.db


async def test_artists_to_add(app: FastAPI, plex: FakePlex) -> None:
    owner, sid = await owner_with_library(app, plex)
    url = f"/api/servers/{sid}/library/discover"
    # Plex: Radiohead is like Portishead (in the library) and Muse; Portishead like Massive Attack.
    first = (await owner.get(url)).json()
    assert [(s["name"], s["similar_to"]) for s in first["items"]] == [
        ("Massive Attack", 1),
        ("Muse", 1),
    ]
    async with get_sessionmaker()() as db:
        server_id = await db.scalar(select(PlexServer.id))
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        assert server_id is not None and user_id is not None
        db.add(SimilarArtist(server_id=server_id, artist_rating_key=101, position=5, name="muse"))
        await db.commit()
    await add_listens(user_id, [(1, "Massive Attack", "Mezzanine", "Teardrop")] * 3)

    found = (await owner.get(url)).json()
    # Muse is similar to two library artists now (however Plex spells it); listens next.
    assert [(s["name"], s["similar_to"], s["listens"]) for s in found["items"]] == [
        ("Muse", 2, 0),
        ("Massive Attack", 1, 3),
    ]
    assert [x["name"] for x in found["items"][0]["sources"]] == ["Portishead", "Radiohead"]
    assert (found["total"], found["hidden"]) == (2, 0)

    # Hidden: gone from the list, listed with the hidden ones, and back when unhidden.
    muse = found["items"][0]
    hide = await owner.post(
        f"{url}/hidden", json={"key": muse["key"], "name": muse["name"]}, headers=CSRF
    )
    assert hide.status_code == 204
    after = (await owner.get(url)).json()
    assert [s["name"] for s in after["items"]] == ["Massive Attack"] and after["hidden"] == 1
    hidden = (await owner.get(url, params={"hidden": True})).json()
    assert [s["name"] for s in hidden["items"]] == ["Muse"]
    assert (await owner.delete(f"{url}/hidden/{muse['key']}", headers=CSRF)).status_code == 204
    assert (await owner.get(url)).json()["total"] == 2


MUSE = "9c9f1380-2516-4fc9-a3e6-f9f61941d090"
FANART = "https://assets.fanart.tv/fanart/music"


def picture(fmt: str, size: tuple[int, int]) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA" if fmt == "PNG" else "RGB", size, "navy").save(out, fmt)
    return out.getvalue()


async def test_top_suggestions_get_fanart_artwork(
    app: FastAPI, plex: FakePlex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The top suggestions are found on MusicBrainz by name (only a sure match), then their
    most liked fanart.tv background, banner and HD logo are kept and linked."""
    import httpx

    from plekstender.artwork import fetch as fetch_mod
    from plekstender.artwork import sources
    from plekstender.config import get_settings
    from plekstender.db.instance import set_fanart_api_key
    from plekstender.worker import find_discover_art

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(fetch_mod, "resolver", resolve)
    monkeypatch.setattr(sources, "MB_INTERVAL", 0)

    def search(request: httpx.Request) -> httpx.Response:
        sure = '"Muse"' in request.url.params["query"]
        artists = [{"id": MUSE, "name": "Muse", "score": 100}] if sure else [
            {"id": "00000000-0000-4000-8000-000000000001", "name": "MA Tribute", "score": 60}
        ]  # fmt: skip
        return httpx.Response(200, json={"artists": artists})

    plex.router.get("https://musicbrainz.org/ws/2/artist").mock(side_effect=search)
    plex.router.get(f"https://webservice.fanart.tv/v3/music/{MUSE}").mock(
        return_value=httpx.Response(200, json={
            "artistbackground": [{"url": f"{FANART}/bg-1.jpg", "likes": "2"},
                                 {"url": f"{FANART}/bg-2.jpg", "likes": "9"}],
            "musicbanner": [{"url": f"{FANART}/banner.jpg", "likes": "1"}],
            "musiclogo": [{"url": f"{FANART}/sd.png", "likes": "5"}],
            "hdmusiclogo": [{"url": f"{FANART}/hd.png", "likes": "0"}],
        })
    )  # fmt: skip
    fetched: list[str] = []

    def image(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=picture("PNG", (800, 310)))
        return httpx.Response(200, content=picture("JPEG", (1920, 1080)))

    plex.router.get(url__startswith=FANART).mock(side_effect=image)

    owner, sid = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        await set_fanart_api_key(db, get_settings(), "f" * 32)
        await db.commit()
    url = f"/api/servers/{sid}/library/discover"
    # Not looked up yet: no artwork (and the worker is asked to look).
    assert [s["art"] for s in (await owner.get(url)).json()["items"]] == [None, None]

    await find_discover_art.func()
    # The most liked background, the banner, and the HD logo before the standard one.
    assert sorted(fetched) == ["banner.jpg", "bg-2.jpg", "hd.png"]
    items = {s["name"]: s["art"] for s in (await owner.get(url)).json()["items"]}
    assert items["Massive Attack"] is None  # MusicBrainz wasn't sure who that is
    assert items["Muse"] == {
        "background": f"/api/discover-art/{MUSE}/background",
        "banner": f"/api/discover-art/{MUSE}/banner",
        "logo": f"/api/discover-art/{MUSE}/logo",
    }
    background = await owner.get(items["Muse"]["background"])
    assert background.status_code == 200 and background.headers["content-type"] == "image/jpeg"
    logo = await owner.get(items["Muse"]["logo"])
    assert logo.headers["content-type"] == "image/png"  # transparency kept
    assert (await owner.get(f"/api/discover-art/{MUSE}/poster")).status_code == 422
    # Looked up a moment ago: not again until next month.
    fetched.clear()
    await find_discover_art.func()
    assert fetched == []
