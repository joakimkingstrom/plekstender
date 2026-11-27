"""Artist artwork: transforms, text logos, the safe fetcher, sources and the editor API."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from PIL import Image, ImageFont

from plekstender.artwork import fetch as fetch_mod
from plekstender.artwork import sources, text
from plekstender.artwork.images import ImageError, Transform, apply_transform, open_image
from tests.fakes import CSRF, FakePlex, sample_image, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs

FANART_KEY = "f" * 32
RADIOHEAD = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
PORTISHEAD = "8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"


def pixel_box(img: Image.Image, background: tuple[int, int, int]) -> tuple[int, int, int, int]:
    """Bounding box of pixels that differ from the background."""
    bg = Image.new("RGB", img.size, background)
    from PIL import ImageChops

    box = ImageChops.difference(img.convert("RGB"), bg).getbbox()
    assert box is not None
    return box


# --- images ---------------------------------------------------------------------------------


def test_transform_fits_logo_into_80_percent_of_a_square() -> None:
    logo = open_image(sample_image("PNG", (300, 120)))  # white box 200 x 80 inside
    out = apply_transform(logo, Transform())
    assert out.size == (1000, 1000)
    left, top, right, bottom = pixel_box(out, (0, 0, 0))
    assert right - left == 800  # the visible box is cropped, then fitted to 80%
    assert (left + right) // 2 == 500 and (top + bottom) // 2 == 500


def test_transform_options() -> None:
    logo = open_image(sample_image("PNG", (300, 120)))
    small = apply_transform(logo, Transform(fit=False, zoom=1.0))
    left, _, right, _ = pixel_box(small, (0, 0, 0))
    assert abs((right - left) - 200) <= 1  # zoom 1 = original size
    inverted = apply_transform(logo, Transform(invert=True, background="#ffffff"))
    assert inverted.getpixel((500, 500)) == (0, 0, 0)  # white logo turned black
    assert inverted.getpixel((5, 5)) == (255, 255, 255)
    tinted = apply_transform(logo, Transform(tint="#ff0000"))
    r, g, b = tinted.getpixel((500, 500))  # type: ignore[misc]
    assert r == 255 and g < 200 and b < 200
    with pytest.raises(ValueError):
        Transform(tint="red")


def test_open_image_rejects_non_images() -> None:
    with pytest.raises(ImageError):
        open_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    with pytest.raises(ImageError):
        open_image(b"not an image at all")
    assert open_image(sample_image("JPEG")).format == "JPEG"


def test_text_logo(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    # Any font will do for layout: use Pillow's bundled one instead of a Google font.
    bundled = ImageFont.load_default(400)
    monkeypatch.setattr(text.ImageFont, "truetype", lambda *_a, **_k: bundled)
    spec = text.TextLogo(text="Guns N' Roses", font="Anton", rows=2, case="upper", color="#ff0000")
    img = text.render_text(spec, tmp_path / "unused.ttf")
    assert img.mode == "RGBA"
    assert img.height > img.width / 3  # two lines
    with pytest.raises(ValueError):
        text.TextLogo(text="x", font="../../etc/passwd")


# --- safe fetcher ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1::1", True),
        ("127.0.0.1", False),
        ("10.0.0.2", False),
        ("192.168.1.10", False),
        ("169.254.169.254", False),
        ("::1", False),
        ("::ffff:10.0.0.1", False),
        ("fd00::1", False),
        ("0.0.0.0", False),  # noqa: S104
    ],
)
def test_public_addresses(address: str, public: bool) -> None:
    assert fetch_mod.is_public(address) is public


async def test_fetch_refuses_private_targets_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = {"cdn.example.com": ["93.184.216.34"], "internal.example.com": ["10.0.0.5"]}

    async def resolve(host: str) -> list[str]:
        return hosts.get(host, ["127.0.0.1"])

    monkeypatch.setattr(fetch_mod, "resolver", resolve)
    with respx.mock() as router:
        router.get("https://cdn.example.com/logo.png").mock(
            return_value=httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
        )
        router.get("https://cdn.example.com/hop").mock(
            return_value=httpx.Response(302, headers={"location": "https://internal.example.com/x"})
        )
        router.get("https://cdn.example.com/huge").mock(
            return_value=httpx.Response(200, content=b"x" * 2000)
        )
        async with httpx.AsyncClient() as http:
            assert await fetch_mod.fetch(http, "https://cdn.example.com/logo.png") == (
                b"png",
                "image/png",
            )
            for url in (
                "file:///etc/passwd",
                "http://localhost/admin",
                "https://cdn.example.com:8443/logo.png",
                "https://user:pw@cdn.example.com/logo.png",
                "https://cdn.example.com/hop",
            ):
                with pytest.raises(fetch_mod.FetchError):
                    await fetch_mod.fetch(http, url)
            with pytest.raises(fetch_mod.FetchError, match="too large"):
                await fetch_mod.fetch(http, "https://cdn.example.com/huge", max_bytes=1000)


# --- sources --------------------------------------------------------------------------------


def fanart_response(mbid: str) -> dict[str, Any]:
    def logo(n: int, likes: int, lang: str = "en") -> dict[str, Any]:
        return {
            "url": f"https://assets.fanart.tv/fanart/music/{mbid}/logo-{n}.png",
            "likes": str(likes),
            "lang": lang,
        }

    return {"hdmusiclogo": [logo(1, 2), logo(2, 9)], "musiclogo": [logo(3, 9), logo(4, 30)]}


async def test_fanart_logos_are_sorted_by_likes() -> None:
    with respx.mock() as router:
        router.get(f"https://webservice.fanart.tv/v3/music/{RADIOHEAD}").mock(
            return_value=httpx.Response(200, json=fanart_response(RADIOHEAD))
        )
        async with httpx.AsyncClient() as http:
            logos = await sources.fanart_logos(
                http, "https://webservice.fanart.tv/v3", FANART_KEY, RADIOHEAD
            )
    assert [c.url.rsplit("/", 1)[1] for c in logos] == [
        "logo-4.png",  # 30 likes
        "logo-2.png",  # 9 likes, HD before SD
        "logo-3.png",
        "logo-1.png",
    ]
    assert logos[0].thumb_url and "/preview/" in logos[0].thumb_url


async def test_image_search_ranking() -> None:
    shop = "https://shop.redbubble.com/t.jpg"
    logo = "https://logos.example.org/radiohead.png"
    photo = "https://photos.example.org/band.jpg"
    results = [
        {"img_src": shop, "url": "https://www.redbubble.com/x", "title": "Radiohead logo T-shirt"},
        {"img_src": logo, "url": "https://logos.example.org", "title": "Radiohead logo PNG"},
        {"img_src": logo, "url": "https://dup.example.org", "title": "dup"},
        {"img_src": photo, "url": "https://photos.example.org", "resolution": "1200 x 800"},
    ]
    with respx.mock() as router:
        router.get("http://searxng:8080/search").mock(
            return_value=httpx.Response(200, json={"results": results})
        )
        async with httpx.AsyncClient() as http:
            found = await sources.search_images(http, "http://searxng:8080", "Radiohead logo")
    assert [c.url for c in found] == [
        "https://logos.example.org/radiohead.png",
        "https://photos.example.org/band.jpg",
        "https://shop.redbubble.com/t.jpg",
    ]
    assert (found[1].width, found[1].height) == (1200, 800)


# --- API ------------------------------------------------------------------------------------


@pytest.fixture
def web(plex: FakePlex, monkeypatch: pytest.MonkeyPatch) -> respx.MockRouter:
    """fanart.tv, MusicBrainz and their image hosts, all on public addresses."""

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(fetch_mod, "resolver", resolve)
    monkeypatch.setattr(sources, "MB_INTERVAL", 0)
    router = plex.router
    for mbid in (RADIOHEAD, "a74b1b7f", PORTISHEAD):  # the fake library's MBID is short
        router.get(f"https://webservice.fanart.tv/v3/music/{mbid}").mock(
            return_value=httpx.Response(200, json=fanart_response(mbid))
        )
    router.get(url__regex=r"https://assets\.fanart\.tv/(fanart|preview)/.*").mock(
        return_value=httpx.Response(
            200, content=sample_image("PNG"), headers={"content-type": "image/png"}
        )
    )
    router.get("https://images.example-agent.com/radiohead.jpg").mock(
        return_value=httpx.Response(200, content=sample_image("JPEG"))
    )
    router.get("https://musicbrainz.org/ws/2/artist").mock(
        return_value=httpx.Response(
            200,
            json={
                "artists": [
                    {
                        "id": "11111111-2222-4333-8444-555555555555",
                        "name": "Portishead Tribute",
                        "score": 95,
                    },
                    {"id": PORTISHEAD, "name": "Portishead", "score": 100},
                ]
            },
        )
    )
    return router


async def setup(app: FastAPI, plex: FakePlex) -> tuple[httpx.AsyncClient, int]:
    owner, sid = await owner_with_library(app, plex)
    resp = await owner.put("/api/admin/fanart", json={"api_key": FANART_KEY}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    return owner, sid


def jpeg_size(data: bytes) -> tuple[int, int]:
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG"
    return img.size


@pytest.mark.db
async def test_artwork_editor_flow(app: FastAPI, plex: FakePlex, web: respx.MockRouter) -> None:
    owner, sid = await setup(app, plex)
    base = f"/api/servers/{sid}/artwork"

    page = (await owner.get(f"{base}/artists")).json()
    assert [(a["title"], a["status"], a["has_mbid"]) for a in page["items"]] == [
        ("Portishead", "none", False),
        ("Radiohead", "none", True),
        ("Secret Band", "none", False),
    ]
    assert page["counts"] == {"none": 3}

    # fanart.tv, most liked first; thumbnails come through the signed image proxy.
    fanart = (await owner.get(f"{base}/artists/100/candidates", params={"source": "fanart"})).json()
    assert [c["likes"] for c in fanart["candidates"]] == [30, 9, 9, 2]
    thumb = await owner.get(fanart["candidates"][0]["thumb"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/png"
    assert (await owner.get(f"{base}/image", params={"t": "forged.123.abc"})).status_code == 502

    # No MBID in Plex: found on MusicBrainz by exact name, and remembered.
    portishead = (
        await owner.get(f"{base}/artists/101/candidates", params={"source": "fanart"})
    ).json()
    assert len(portishead["candidates"]) == 4
    assert (await owner.get(f"{base}/artists")).json()["items"][0]["has_mbid"] is True

    plex_posters = (
        await owner.get(f"{base}/artists/100/candidates", params={"source": "plex"})
    ).json()
    assert [c["label"] for c in plex_posters["candidates"]] == ["agent", "Current · upload"]
    search = (await owner.get(f"{base}/artists/100/candidates", params={"source": "search"})).json()
    assert search["candidates"] == [] and "isn't set up" in search["note"]

    # Preview and apply a fanart logo.
    body = {
        "source": {"kind": "candidate", "token": fanart["candidates"][0]["token"]},
        "transform": {"invert": True, "contrast": 1.2},
    }
    preview = await owner.post(f"{base}/artists/100/preview", json=body, headers=CSRF)
    assert preview.status_code == 200, preview.text
    assert jpeg_size(preview.content) == (1000, 1000)
    applied = await owner.post(f"{base}/artists/100/apply", json=body, headers=CSRF)
    assert applied.status_code == 200, applied.text
    assert (applied.json()["status"], applied.json()["source"]) == ("done", "fanart")
    assert applied.json()["thumb"] == "/library/metadata/100/thumb/1"  # the new poster
    [(key, image)] = plex.library.uploaded
    assert key == 100 and jpeg_size(image) == (1000, 1000)

    # Upload your own, and a Plex poster.
    upload = await owner.post(
        f"{base}/uploads",
        content=sample_image("PNG"),
        headers={**CSRF, "content-type": "image/png"},
    )
    upload_id = upload.json()["upload_id"]
    poster_token = plex_posters["candidates"][0]["token"]
    for source in (
        {"kind": "upload", "upload_id": upload_id},
        {"kind": "candidate", "token": poster_token},
        {"kind": "current"},
    ):
        resp = await owner.post(
            f"{base}/artists/100/preview", json={"source": source}, headers=CSRF
        )
        assert resp.status_code == 200, (source, resp.text)
    bad = await owner.post(f"{base}/uploads", content=b"%PDF-1.4", headers=CSRF)
    assert bad.status_code == 422

    # Status by hand, and the filter.
    assert (
        await owner.put(f"{base}/artists/102/status", json={"status": "custom"}, headers=CSRF)
    ).status_code == 204
    custom = (await owner.get(f"{base}/artists", params={"status": "custom"})).json()
    assert [a["title"] for a in custom["items"]] == ["Secret Band"]
    assert custom["counts"] == {"none": 1, "done": 1, "custom": 1}

    # Everything still to do, in one go: only "none" changes.
    bulk = await owner.post(f"{base}/artists/bulk-status", json={"status": "done"}, headers=CSRF)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json() == {"changed": 1}
    after = (await owner.get(f"{base}/artists")).json()
    assert after["counts"] == {"done": 2, "custom": 1}
    again = await owner.post(f"{base}/artists/bulk-status", json={"status": "custom"}, headers=CSRF)
    assert again.json() == {"changed": 0}
    refused = await owner.post(f"{base}/artists/bulk-status", json={"status": "none"}, headers=CSRF)
    assert refused.status_code == 422


@pytest.mark.db
async def test_text_logo_preview(
    app: FastAPI,
    plex: FakePlex,
    web: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from plekstender.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    bundled = ImageFont.load_default(400)
    monkeypatch.setattr(text.ImageFont, "truetype", lambda *_a, **_k: bundled)

    def google_css(request: httpx.Request) -> httpx.Response:
        if request.url.params["family"] in ("Not A Real Font", "Buda"):
            return httpx.Response(400, text="Font family not found")
        return httpx.Response(
            200,
            text="@font-face { src: url(https://fonts.gstatic.com/s/anton/v1/a.ttf); }",
        )

    web.get("https://fonts.googleapis.com/css2").mock(side_effect=google_css)
    font_file = web.get("https://fonts.gstatic.com/s/anton/v1/a.ttf").mock(
        return_value=httpx.Response(200, content=b"ttf-bytes")
    )
    owner, sid = await setup(app, plex)
    body = {
        "source": {"kind": "text", "text": {"text": "Radiohead", "font": "Anton", "case": "upper"}}
    }
    for _ in range(2):
        resp = await owner.post(
            f"/api/servers/{sid}/artwork/artists/100/preview", json=body, headers=CSRF
        )
        assert resp.status_code == 200, resp.text
    assert font_file.call_count == 1  # downloaded once, then cached
    assert (tmp_path / "fonts" / "Anton.ttf").read_bytes() == b"ttf-bytes"
    applied = await owner.post(
        f"/api/servers/{sid}/artwork/artists/100/apply", json=body, headers=CSRF
    )
    assert (applied.json()["status"], applied.json()["source"]) == ("custom", "text")
    fonts = (await owner.get("/api/artwork/fonts")).json()
    assert "Anton" in next(g["fonts"] for g in fonts if g["name"] == "Sans serif")

    # Admins choose the fonts, from Google Fonts' catalogue: any capitals, and the group
    # comes from the catalogue.
    def group(fonts: list[dict[str, Any]], name: str) -> list[str]:
        return next(g["fonts"] for g in fonts if g["name"] == name)

    def family(
        name: str, category: str, popularity: int, weights: tuple[str, ...] = ("400",)
    ) -> dict[str, Any]:
        return {
            "family": name,
            "category": category,
            "popularity": popularity,
            "subsets": ["latin"],
            "fonts": {w: {} for w in weights},
        }

    catalogue_route = web.get("https://fonts.google.com/metadata/fonts").mock(
        return_value=httpx.Response(
            200,
            text=")]}'\n"
            + json.dumps(
                {
                    "familyMetadataList": [
                        family("Roboto", "Sans Serif", 1),
                        family("Bungee Shade", "Display", 300),
                        family("Buda", "Display", 900, ("300",)),
                        family("Anton", "Sans Serif", 40),
                        {**family("Noto Sans JP", "Sans Serif", 5), "subsets": ["japanese"]},
                    ]
                }
            ),
        )
    )
    resp = await owner.post("/api/admin/fonts", json={"name": "Not A Real Font"}, headers=CSRF)
    assert resp.status_code == 400
    assert "no font called" in resp.json()["detail"]
    resp = await owner.post("/api/admin/fonts", json={"name": " roboto "}, headers=CSRF)
    assert resp.status_code == 200, resp.text
    assert "Roboto" in group(resp.json(), "Sans serif")  # its exact name, in its group
    resp = await owner.post("/api/admin/fonts", json={"name": "bungee  SHADE"}, headers=CSRF)
    assert "Bungee Shade" in group(resp.json(), "Display")
    again = await owner.post("/api/admin/fonts", json={"name": "Bungee Shade"}, headers=CSRF)
    assert again.status_code == 409

    # Browsing: most used first, Latin fonts only, what's offered marked.
    page = (await owner.get("/api/admin/fonts/catalogue")).json()
    assert [(f["name"], f["offered"]) for f in page["items"]] == [
        ("Roboto", True),
        ("Anton", True),
        ("Bungee Shade", True),
        ("Buda", False),
    ]
    display = (
        await owner.get("/api/admin/fonts/catalogue", params={"group": "Display", "q": "bu"})
    ).json()
    assert [f["name"] for f in display["items"]] == ["Bungee Shade", "Buda"]
    assert catalogue_route.call_count == 1  # fetched once, then kept

    # Previews: a line in the font, for offered fonts and catalogue ones.
    preview = await owner.get(
        "/api/artwork/fonts/preview.png", params={"family": "Anton", "ink": "light"}
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(preview.content)).mode == "RGBA"
    buda = await owner.get(
        "/api/artwork/fonts/preview.png", params={"family": "Buda", "text": "Radiohead"}
    )
    assert buda.status_code == 200
    # Buda has no regular weight: its 300 was asked for.
    assert any(call.request.url.params.get("family") == "Buda:wght@300" for call in web.calls)
    unknown = await owner.get("/api/artwork/fonts/preview.png", params={"family": "Comic Sans"})
    assert unknown.status_code == 404

    resp = await owner.delete("/api/admin/fonts/Anton", headers=CSRF)
    assert group(resp.json(), "Sans serif") == ["Raleway", "Roboto"]
    assert group((await owner.get("/api/artwork/fonts")).json(), "Display")[0] == "Allerta Stencil"
    refused = await owner.post(
        f"/api/servers/{sid}/artwork/artists/100/preview", json=body, headers=CSRF
    )
    assert refused.status_code == 400  # Anton isn't offered any more
    resp = await owner.delete("/api/admin/fonts", headers=CSRF)
    assert "Anton" in group(resp.json(), "Sans serif")
    assert "Bungee Shade" not in group(resp.json(), "Display")


@pytest.mark.db
async def test_only_the_owner_edits_artwork(
    app: FastAPI, plex: FakePlex, web: respx.MockRouter
) -> None:
    await setup(app, plex)
    friend, _ = await sign_in(app, plex, "friend-token")
    fsid = await connect(friend)
    await run_queued_syncs()
    assert (await friend.get(f"/api/servers/{fsid}/artwork/artists")).status_code == 403
    resp = await friend.post(
        f"/api/servers/{fsid}/artwork/artists/100/apply",
        json={"source": {"kind": "current"}},
        headers=CSRF,
    )
    assert resp.status_code == 403
    bulk = await friend.post(
        f"/api/servers/{fsid}/artwork/artists/bulk-status", json={"status": "done"}, headers=CSRF
    )
    assert bulk.status_code == 403
    assert (
        await friend.put("/api/admin/fanart", json={"api_key": FANART_KEY}, headers=CSRF)
    ).status_code == 403


@pytest.mark.db
async def test_bulk_fanart_job(app: FastAPI, plex: FakePlex, web: respx.MockRouter) -> None:
    from plekstender.worker import artwork_job

    owner, sid = await setup(app, plex)
    base = f"/api/servers/{sid}/artwork"
    await owner.put(f"{base}/artists/102/status", json={"status": "custom"}, headers=CSRF)

    started = await owner.post(f"{base}/jobs/fanart", json={}, headers=CSRF)
    assert started.status_code == 201, started.text
    job = started.json()
    assert (job["kind"], job["status"]) == ("fanart", "queued")
    busy = await owner.post(f"{base}/jobs/fanart", json={}, headers=CSRF)
    assert busy.status_code == 409

    await artwork_job.func(job_id=job["id"])  # what the worker runs
    [done] = (await owner.get(f"{base}/jobs")).json()
    assert done["status"] == "succeeded", done["error"]
    assert done["progress"] == {"done": 2, "total": 2}  # the two "to do" artists
    assert done["stats"] == {
        "applied": 2,
        "no_mbid": 0,
        "no_logo": 0,
        "failed": 0,
        "folder_errors": 0,
    }
    assert sorted(k for k, _ in plex.library.uploaded) == [100, 101]
    statuses = {
        a["title"]: a["status"] for a in (await owner.get(f"{base}/artists")).json()["items"]
    }
    assert statuses == {"Portishead": "done", "Radiohead": "done", "Secret Band": "custom"}

    # Chosen artists only; Secret Band has no MusicBrainz match.
    picked = (
        await owner.post(f"{base}/jobs/fanart", json={"rating_keys": [102]}, headers=CSRF)
    ).json()
    await artwork_job.func(job_id=picked["id"])
    latest = (await owner.get(f"{base}/jobs")).json()[0]
    assert latest["stats"]["no_mbid"] == 1


# --- artist folders ---------------------------------------------------------------------------


def test_artist_folder_from_track_paths() -> None:
    from pathlib import PurePosixPath

    from plekstender.artwork.folders import artist_plex_folder, map_path

    two_albums = ["/music/Radiohead/OK Computer/01.flac", "/music/Radiohead/Kid A/01.flac"]
    assert artist_plex_folder(two_albums) == PurePosixPath("/music/Radiohead")
    one_album = ["/music/Portishead/Dummy/01.flac", "/music/Portishead/Dummy/02.flac"]
    assert artist_plex_folder(one_album) == PurePosixPath("/music/Portishead")
    assert artist_plex_folder([]) is None
    assert str(map_path("/music/Radiohead", "/music", "/srv/media")) == "/srv/media/Radiohead"
    assert map_path("/other/Radiohead", "/music", "/srv/media") is None


@pytest.mark.db
async def test_saving_to_artist_folders(
    app: FastAPI, plex: FakePlex, web: respx.MockRouter, tmp_path: Any
) -> None:
    from sqlalchemy import text as sql

    from plekstender.db import get_sessionmaker

    owner, sid = await setup(app, plex)
    base = f"/api/servers/{sid}/artwork"
    async with get_sessionmaker()() as db:  # realistic paths: Artist/Album/track
        await db.execute(
            sql(
                "UPDATE tracks SET file_path = '/music/' || CASE artist_rating_key "
                "WHEN 100 THEN 'Radiohead' WHEN 101 THEN 'Portishead' ELSE 'Compilations' END "
                "|| '/Album ' || album_rating_key || '/' || rating_key || '.flac'"
            )
        )
        await db.commit()
    music = tmp_path / "music"
    (music / "Radiohead").mkdir(parents=True)

    from plekstender.worker import check_artist_folders, save_artist_folder

    server_id = await _server_id()

    async def worker_writes(rating_key: int) -> dict:
        """What the worker does after an apply; returns the artist's folder outcome."""
        await save_artist_folder.func(server_id=server_id, rating_key=rating_key)
        async with get_sessionmaker()() as db:
            row = (
                await db.execute(
                    sql(
                        "SELECT folder_path, folder_error FROM artist_artwork "
                        "WHERE rating_key = :key"
                    ),
                    {"key": rating_key},
                )
            ).one()
        return {"path": row.folder_path, "error": row.folder_error}

    off = (await owner.get(f"{base}/folder")).json()
    assert (off["enabled"], off["plex_root"]) == (False, "/music")

    # The web app doesn't look at music folders: the worker checks the mapping.
    missing = await owner.put(
        f"{base}/folder",
        json={"enabled": True, "plex_prefix": "/music", "local_prefix": "/nope"},
        headers=CSRF,
    )
    assert missing.status_code == 200
    assert (missing.json()["checking"], missing.json()["example"]) == (True, None)
    await check_artist_folders.func(server_id=server_id)
    checked = (await owner.get(f"{base}/folder")).json()
    assert checked["checking"] is False
    assert "doesn't exist in the worker container" in checked["problem"]

    on = await owner.put(
        f"{base}/folder",
        json={"enabled": True, "plex_prefix": "/music/", "local_prefix": str(music)},
        headers=CSRF,
    )
    assert on.status_code == 200, on.text
    await check_artist_folders.func(server_id=server_id)
    checked = (await owner.get(f"{base}/folder")).json()
    assert checked["problem"] is None
    assert (checked["example"]["artist"], checked["example"]["exists"]) == ("Radiohead", True)

    body = {"source": {"kind": "current"}}
    radiohead = (await owner.post(f"{base}/artists/100/apply", json=body, headers=CSRF)).json()
    assert (radiohead["folder"], radiohead["folder_queued"]) == (None, True)
    assert not (music / "Radiohead" / "artist.jpg").exists()  # not by the web app
    written = await worker_writes(100)
    assert written == {"path": str((music / "Radiohead" / "artist.jpg").resolve()), "error": None}
    assert jpeg_size((music / "Radiohead" / "artist.jpg").read_bytes()) == (1000, 1000)
    listed = (await owner.get(f"{base}/artists")).json()["items"]
    saved = next(a for a in listed if a["rating_key"] == 100)
    assert saved["folder"] == written["path"] and saved["folder_saved_at"]
    from plekstender.artwork.folders import queue_dir
    from plekstender.config import get_settings

    assert not any(queue_dir(get_settings().data_dir).glob("*.jpg"))  # handed over

    # Still saved to Plex when the folder can't be used, with the reason.
    await owner.post(f"{base}/artists/101/apply", json=body, headers=CSRF)
    assert "doesn't exist" in (await worker_writes(101))["error"]
    await owner.post(f"{base}/artists/102/apply", json=body, headers=CSRF)
    assert "doesn't match" in (await worker_writes(102))["error"]
    assert len(plex.library.uploaded) == 3

    # A symlink can't lead outside the mapped folder.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (music / "Portishead").symlink_to(outside)
    await owner.post(f"{base}/artists/101/apply", json=body, headers=CSRF)
    assert "outside" in (await worker_writes(101))["error"]
    assert not (outside / "artist.jpg").exists()


async def _server_id() -> int:
    from sqlalchemy import select

    from plekstender.db import get_sessionmaker
    from plekstender.db.models import PlexServer

    async with get_sessionmaker()() as db:
        server_id = await db.scalar(select(PlexServer.id))
    assert server_id is not None
    return server_id


# --- logo packs -------------------------------------------------------------------------------


@pytest.mark.db
async def test_export_and_import_logo_packs(
    app: FastAPI, plex: FakePlex, web: respx.MockRouter
) -> None:
    import json
    import zipfile

    from plekstender.worker import artwork_job

    owner, sid = await setup(app, plex)
    base = f"/api/servers/{sid}/artwork"
    await owner.post(
        f"{base}/artists/100/apply", json={"source": {"kind": "current"}}, headers=CSRF
    )
    await owner.put(f"{base}/artists/101/status", json={"status": "custom"}, headers=CSRF)

    job = (await owner.post(f"{base}/jobs/export", headers=CSRF)).json()
    await artwork_job.func(job_id=job["id"])
    [done] = (await owner.get(f"{base}/jobs")).json()[:1]
    assert done["status"] == "succeeded", done["error"]
    assert done["stats"] == {"exported": 2, "skipped": 0}
    download = await owner.get(done["download"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(download.content)) as pack:
        manifest = json.loads(pack.read("manifest.json"))
        images_in_pack = {
            name: pack.read(name) for name in pack.namelist() if name != "manifest.json"
        }
    assert manifest["version"] == 1
    assert [(e["artist_name"], e["mbid"]) for e in manifest["entries"]] == [
        ("Portishead", None),
        ("Radiohead", "a74b1b7f"),
    ]
    # The images are what Plex has now (here the fake server's small JPEG).
    assert all(Image.open(io.BytesIO(data)).format == "JPEG" for data in images_in_pack.values())

    # A pack from elsewhere: matched by MBID or name, plus an artist this server lacks.
    manifest["entries"].append(
        {"mbid": None, "artist_name": "Unknown Band", "file": manifest["entries"][0]["file"]}
    )
    manifest["entries"][1]["artist_name"] = "Radiohead (renamed)"  # still matched by MBID
    shared = io.BytesIO()
    with zipfile.ZipFile(shared, "w") as pack:
        pack.writestr("manifest.json", json.dumps(manifest))
        for name, data in images_in_pack.items():
            pack.writestr(name, data)
    preview = await owner.post(
        f"{base}/imports",
        content=shared.getvalue(),
        headers={**CSRF, "content-type": "application/zip"},
    )
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert (result["matched"], result["total"]) == (2, 3)
    assert [
        (e["artist_name"], e["matched_by"], e["match"] and e["match"]["rating_key"])
        for e in result["entries"]
    ] == [
        ("Portishead", "name", 101),
        ("Radiohead (renamed)", "mbid", 100),
        ("Unknown Band", None, None),
    ]
    thumb = await owner.get(result["entries"][0]["image"])
    assert thumb.status_code == 200

    before = len(plex.library.uploaded)
    items = [
        {"index": e["index"], "rating_key": e["match"]["rating_key"]}
        for e in result["entries"]
        if e["match"]
    ]
    started = await owner.post(
        f"{base}/imports/{result['import_id']}/apply", json={"items": items}, headers=CSRF
    )
    assert started.status_code == 201, started.text
    await artwork_job.func(job_id=started.json()["id"])
    latest = (await owner.get(f"{base}/jobs")).json()[0]
    assert (latest["kind"], latest["status"]) == ("import", "succeeded")
    assert latest["stats"]["applied"] == 2
    assert len(plex.library.uploaded) == before + 2
    statuses = {
        a["title"]: (a["status"], a["source"])
        for a in (await owner.get(f"{base}/artists")).json()["items"]
    }
    assert statuses["Portishead"] == ("done", "import")

    bad = await owner.post(f"{base}/imports", content=b"not a zip", headers=CSRF)
    assert bad.status_code == 422
    gone = await owner.post(f"{base}/imports/{'0' * 32}/apply", json={"items": items}, headers=CSRF)
    assert gone.status_code == 404


@pytest.mark.db
async def test_search_thumbnails_with_query_strings(
    app: FastAPI, plex: FakePlex, web: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search thumbnails have "&" in their URLs; their links must survive that, and a
    thumbnail host that refuses falls back to the full image."""
    from plekstender.config import get_settings

    monkeypatch.setattr(get_settings(), "searxng_url", "http://searxng:8080")
    results = [
        {
            "img_src": "https://logos.example.org/radiohead.png?w=800&h=400",
            "thumbnail_src": "https://tse1.mm.bing.net/th?id=OIP.abc&pid=Api",
            "url": "https://logos.example.org",
            "title": "Radiohead logo",
        },
        {
            "img_src": "https://logos.example.org/other.png",
            "thumbnail_src": "https://blocked.example.net/th?id=1&x=2",
            "url": "https://logos.example.org",
            "title": "Radiohead logo 2",
        },
    ]
    web.get("http://searxng:8080/search").mock(
        return_value=httpx.Response(200, json={"results": results})
    )
    png = httpx.Response(200, content=sample_image("PNG"), headers={"content-type": "image/png"})
    web.get("https://tse1.mm.bing.net/th?id=OIP.abc&pid=Api").mock(return_value=png)
    web.get("https://blocked.example.net/th?id=1&x=2").mock(return_value=httpx.Response(403))
    web.get("https://logos.example.org/other.png").mock(return_value=png)

    owner, sid = await setup(app, plex)
    found = (
        await owner.get(
            f"/api/servers/{sid}/artwork/artists/100/candidates", params={"source": "search"}
        )
    ).json()
    assert len(found["candidates"]) == 2
    for candidate in found["candidates"]:
        thumb = await owner.get(candidate["thumb"])
        assert thumb.status_code == 200, (candidate["label"], thumb.text)
        assert thumb.headers["content-type"] == "image/png"


@pytest.mark.db
async def test_artists_by_name_or_newest_first(
    app: FastAPI, plex: FakePlex, web: respx.MockRouter
) -> None:
    owner, sid = await setup(app, plex)
    base = f"/api/servers/{sid}/artwork/artists"
    by_name = [a["title"] for a in (await owner.get(base)).json()["items"]]
    assert by_name == sorted(by_name, key=str.lower)
    newest = (await owner.get(base, params={"sort": "added"})).json()["items"]
    # Added a second apart in the fake library, by key: the highest is the newest.
    keys = [a["rating_key"] for a in newest]
    assert keys == sorted(keys, reverse=True) and newest[0]["added_at"] is not None
