"""Artist artwork: transforms, text logos, the safe fetcher, sources and the editor API."""

from __future__ import annotations

import io
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
        text.TextLogo(text="x", font="Comic Sans MS")


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
    web.get("https://fonts.googleapis.com/css2").mock(
        return_value=httpx.Response(
            200,
            text="@font-face { src: url(https://fonts.gstatic.com/s/anton/v1/a.ttf); }",
        )
    )
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
    assert (
        await friend.put("/api/admin/fanart", json={"api_key": FANART_KEY}, headers=CSRF)
    ).status_code == 403
