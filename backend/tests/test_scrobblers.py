# ruff: noqa: S324 (MD5 is what the Last.fm-style sign-in uses)
"""More scrobble services: ListenBrainz-compatible servers and Libre.fm."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from plekstender.artwork import fetch as fetch_mod
from plekstender.config import get_settings
from plekstender.db import get_sessionmaker
from plekstender.db.models import Listen, ScrobbleTarget, User
from plekstender.history import scrobblers
from tests.fakes import CSRF, LASTFM_KEY, LASTFM_URL, FakeLastFm, FakePlex, sign_in
from tests.test_sync import connect, owner_with_library, run_queued_syncs

MALOJA = "https://maloja.example/apis/listenbrainz"
KOITO = "https://koito.example/apis/listenbrainz"
LIBREFM = "https://libre.fm/2.0/"

# Where the fake hosts resolve: a home server (fine for scrobble servers), unless a test
# says otherwise; addresses are themselves.
HOSTS: dict[str, str] = {}


@pytest.fixture(autouse=True)
def resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(host: str) -> list[str]:
        try:
            return [str(ipaddress.ip_address(host.strip("[]")))]
        except ValueError:
            return [HOSTS.get(host, "192.168.1.20")]

    HOSTS.clear()
    monkeypatch.setattr(fetch_mod, "resolver", fake)


def test_addresses() -> None:
    assert (
        scrobblers.listenbrainz_base("https://m.example/apis/listenbrainz/1/")
        == "https://m.example/apis/listenbrainz"
    )
    assert (
        scrobblers.listenbrainz_base("http://ms.local:9078/1/submit-listens")
        == "http://ms.local:9078"
    )
    with pytest.raises(scrobblers.ScrobblerError):
        scrobblers.listenbrainz_base("maloja.local")
    assert scrobblers.librefm_base(None) == LIBREFM
    assert scrobblers.librefm_base("https://gnufm.example/2.0") == "https://gnufm.example/2.0/"


class FakeServers:
    def __init__(self, plex: FakePlex) -> None:
        self.maloja: list[dict] = []
        self.koito: list[dict] = []
        self.librefm: list[dict[str, list[str]]] = []
        self.librefm_sessions_valid = True
        r = plex.router
        r.get(f"{MALOJA}/1/validate-token").mock(return_value=httpx.Response(404))  # not there
        r.post(f"{MALOJA}/1/submit-listens").mock(side_effect=self._maloja)
        r.get(f"{KOITO}/1/validate-token").mock(side_effect=self._koito_validate)
        r.post(f"{KOITO}/1/submit-listens").mock(side_effect=self._koito)
        r.post(LIBREFM).mock(side_effect=self._librefm)

    def _maloja(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Token maloja-key"
        self.maloja.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    def _koito_validate(self, request: httpx.Request) -> httpx.Response:
        ok = request.headers["authorization"] == "Token koito-key"
        return httpx.Response(200, json={"valid": ok, "user_name": "joakim" if ok else None})

    def _koito(self, request: httpx.Request) -> httpx.Response:
        self.koito.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    def _librefm(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert len(form["api_key"][0]) == 32 and len(form["api_sig"][0]) == 32
        method = form["method"][0]
        if method == "auth.getMobileSession":
            expected = hashlib.md5(
                ("joakim" + hashlib.md5(b"s3cret").hexdigest()).encode()
            ).hexdigest()
            if form["authToken"][0] != expected:
                return httpx.Response(
                    200, json={"error": 4, "message": "Invalid authentication token supplied"}
                )
            return httpx.Response(
                200, json={"session": {"name": "Joakim", "key": "sk-123", "subscriber": 0}}
            )
        if method == "track.scrobble":
            if not self.librefm_sessions_valid or form["sk"][0] != "sk-123":
                return httpx.Response(200, json={"error": 9, "message": "Invalid session key"})
            self.librefm.append(form)
            return httpx.Response(
                200, json={"scrobbles": {"@attr": {"accepted": "2", "ignored": "0"}}}
            )
        return httpx.Response(200, json={"error": 3, "message": "Invalid method"})


async def add_plays(user_id: int, count: int) -> None:
    now = dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as db:
        for i in range(count):
            db.add(Listen(
                user_id=user_id, listened_at=now + dt.timedelta(minutes=i), source="plex",
                artist_name="Radiohead", track_name=f"Song {i}", album_name="OK Computer",
                artist_key="radiohead", track_key=f"song {i}", duration_ms=240_000,
            ))  # fmt: skip
        await db.commit()


async def send(user_id: int) -> dict:
    async with get_sessionmaker()() as db, httpx.AsyncClient() as http:
        return await scrobblers.submit_targets(db, http, get_settings(), user_id)


@pytest.mark.db
async def test_scrobble_services(app: FastAPI, plex: FakePlex) -> None:
    servers = FakeServers(plex)
    owner, _ = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        assert user_id

    # Maloja doesn't answer validate-token: added without an account name.
    maloja = await owner.post(
        "/api/scrobblers",
        json={"service": "maloja", "url": MALOJA + "/1", "token": "maloja-key"},
        headers=CSRF,
    )
    assert maloja.status_code == 201, maloja.text
    assert (maloja.json()["url"], maloja.json()["username"], maloja.json()["label"]) == (
        MALOJA,
        None,
        "Maloja",
    )
    # Koito says whose token it is, and refuses a wrong one.
    wrong = await owner.post(
        "/api/scrobblers", json={"service": "koito", "url": KOITO, "token": "nope"}, headers=CSRF
    )
    assert wrong.status_code == 400 and "didn't accept" in wrong.json()["detail"]
    koito = await owner.post(
        "/api/scrobblers",
        json={"service": "koito", "url": KOITO, "token": "koito-key"},
        headers=CSRF,
    )
    assert koito.json()["username"] == "joakim"
    # Libre.fm: signed in once with the password, which isn't kept.
    bad = await owner.post(
        "/api/scrobblers",
        json={"service": "librefm", "username": "joakim", "password": "nope"},
        headers=CSRF,
    )
    assert bad.status_code == 400 and "Invalid authentication" in bad.json()["detail"]
    libre = await owner.post(
        "/api/scrobblers",
        json={"service": "librefm", "username": "joakim", "password": "s3cret"},
        headers=CSRF,
    )
    assert libre.status_code == 201 and libre.json()["username"] == "Joakim"
    async with get_sessionmaker()() as db:
        stored = list(await db.scalars(select(ScrobbleTarget.token_enc)))
        assert not any("s3cret" in t or "sk-123" in t or "maloja-key" in t for t in stored)

    # New plays go to all three.
    await add_plays(user_id, 2)
    results = await send(user_id)
    assert [r.sent for r in results.values()] == [2, 2, 2]
    assert [p["track_metadata"]["track_name"] for p in servers.maloja[0]["payload"]] == [
        "Song 0",
        "Song 1",
    ]
    assert servers.koito[0]["listen_type"] == "import"
    form = servers.librefm[0]
    assert (form["artist[0]"], form["track[1]"], form["album[0]"], form["duration[0]"]) == (
        ["Radiohead"], ["Song 1"], ["OK Computer"], ["240"],
    )  # fmt: skip
    listed = (await owner.get("/api/scrobblers")).json()
    assert [(t["service"], t["sent"], t["pending"], t["paused"]) for t in listed] == [
        ("maloja", 2, 0, False), ("koito", 2, 0, False), ("librefm", 2, 0, False),
    ]  # fmt: skip

    # Libre.fm drops the session: that one pauses, the others carry on.
    servers.librefm_sessions_valid = False
    await add_plays(user_id, 1)
    await send(user_id)
    listed = {t["service"]: t for t in (await owner.get("/api/scrobblers")).json()}
    assert (
        listed["librefm"]["paused"] and "Libre.fm rejected the token" in listed["librefm"]["error"]
    )
    assert listed["maloja"]["sent"] == 3 and listed["librefm"]["pending"] == 1
    # Signing in again resumes it where it stopped.
    servers.librefm_sessions_valid = True
    again = await owner.post(
        "/api/scrobblers",
        json={"service": "librefm", "username": "joakim", "password": "s3cret"},
        headers=CSRF,
    )
    assert (
        again.status_code == 201
        and again.json()["id"] == libre.json()["id"]
        and not again.json()["paused"]
    )
    await send(user_id)
    assert {t["service"]: t["sent"] for t in (await owner.get("/api/scrobblers")).json()}[
        "librefm"
    ] == 3

    # Someone else's are their own.
    friend, _ = await sign_in(app, plex, "friend-token")
    await connect(friend)
    await run_queued_syncs()
    assert (await friend.get("/api/scrobblers")).json() == []
    assert (
        await friend.delete(f"/api/scrobblers/{maloja.json()['id']}", headers=CSRF)
    ).status_code == 404
    assert (
        await owner.delete(f"/api/scrobblers/{maloja.json()['id']}", headers=CSRF)
    ).status_code == 204
    assert len((await owner.get("/api/scrobblers")).json()) == 2


LASTFM_SECRET = "fedcba9876543210fedcba9876543210"


class FakeLastFmWrite:
    """Last.fm's signed calls (POST): auth.getToken, auth.getSession and track.scrobble,
    checking each signature against the shared secret."""

    def __init__(self, plex: FakePlex) -> None:
        self.scrobbles: list[dict[str, list[str]]] = []
        self.session_valid = True
        plex.router.post(LASTFM_URL).mock(side_effect=self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form["api_key"] != LASTFM_KEY:
            return httpx.Response(200, json={"error": 10, "message": "Invalid API key"})
        signed = "".join(f"{k}{form[k]}" for k in sorted(form) if k not in ("format", "api_sig"))
        if hashlib.md5((signed + LASTFM_SECRET).encode()).hexdigest() != form["api_sig"]:
            return httpx.Response(200, json={"error": 13, "message": "Invalid method signature"})
        method = form["method"]
        if method == "auth.getToken":
            return httpx.Response(200, json={"token": "unused"})
        if method == "auth.getSession":
            if form["token"] != "approved":
                return httpx.Response(200, json={"error": 14, "message": "Unauthorized Token"})
            return httpx.Response(200, json={"session": {"name": "Joakim", "key": "lfm-sk"}})
        assert method == "track.scrobble"
        if not self.session_valid or form["sk"] != "lfm-sk":
            return httpx.Response(200, json={"error": 9, "message": "Invalid session key"})
        self.scrobbles.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"scrobbles": {"@attr": {"accepted": 1, "ignored": 0}}})


def _location(resp: httpx.Response) -> str:
    assert resp.status_code == 303, resp.text
    return resp.headers["location"]


@pytest.mark.db
async def test_lastfm_scrobbling(app: FastAPI, plex: FakePlex, lastfm: FakeLastFm) -> None:
    fake = FakeLastFmWrite(plex)
    owner, _ = await owner_with_library(app, plex)
    async with get_sessionmaker()() as db:
        user_id = await db.scalar(select(User.id).where(User.username == "owner"))
        assert user_id

    # The key alone is enough to import, not to scrobble.
    key = await owner.put("/api/admin/lastfm", json={"api_key": LASTFM_KEY}, headers=CSRF)
    assert key.json() == {
        "configured": True,
        "source": "settings",
        "scrobbling": False,
        "note": None,
    }
    assert (await owner.get("/api/scrobblers/lastfm")).json() == {"available": False}
    assert _location(await owner.get("/api/scrobblers/lastfm/connect")).endswith(
        "?lastfm=unavailable"
    )
    # Its shared secret is checked with a signed call, then kept with the key.
    wrong = await owner.put("/api/admin/lastfm", json={"shared_secret": "0" * 32}, headers=CSRF)
    assert wrong.status_code == 400 and "shared secret" in wrong.json()["detail"]
    ok = await owner.put("/api/admin/lastfm", json={"shared_secret": LASTFM_SECRET}, headers=CSRF)
    assert ok.json() == {"configured": True, "source": "settings", "scrobbling": True, "note": None}
    assert (await owner.get("/api/scrobblers/lastfm")).json() == {"available": True}
    nope = await owner.post("/api/scrobblers", json={"service": "lastfm"}, headers=CSRF)
    assert nope.status_code == 422

    # Connecting: off to last.fm, which comes back with a token for the callback.
    auth = urlsplit(_location(await owner.get("/api/scrobblers/lastfm/connect")))
    assert f"{auth.scheme}://{auth.netloc}{auth.path}" == "https://www.last.fm/api/auth/"
    query = parse_qs(auth.query)
    assert query["api_key"] == [LASTFM_KEY]
    callback = urlsplit(query["cb"][0])
    assert callback.path == "/api/scrobblers/lastfm/callback"
    state = parse_qs(callback.query)["state"][0]
    # Someone else's sign-in (or a made-up one) isn't taken.
    friend, _ = await sign_in(app, plex, "friend-token")
    stolen = await friend.get(
        "/api/scrobblers/lastfm/callback", params={"state": state, "token": "approved"}
    )
    assert _location(stolen).endswith("?lastfm=expired")
    refused = await owner.get(
        "/api/scrobblers/lastfm/callback", params={"state": state, "token": "not-approved"}
    )
    assert _location(refused).endswith("?lastfm=refused")
    back = await owner.get(
        "/api/scrobblers/lastfm/callback", params={"state": state, "token": "approved"}
    )
    assert _location(back) == "/settings/scrobbling?lastfm=connected"
    listed = (await owner.get("/api/scrobblers")).json()
    assert [(t["service"], t["label"], t["username"]) for t in listed] == [
        ("lastfm", "Last.fm", "Joakim")
    ]
    async with get_sessionmaker()() as db:
        assert "lfm-sk" not in (await db.scalar(select(ScrobbleTarget.token_enc)) or "")

    # New plays are scrobbled, signed with the instance's secret and the person's session.
    await add_plays(user_id, 2)
    results = await send(user_id)
    assert [r.sent for r in results.values()] == [2]
    form = fake.scrobbles[0]
    assert (form["artist[0]"], form["track[1]"], form["sk"]) == (
        ["Radiohead"],
        ["Song 1"],
        ["lfm-sk"],
    )

    # Without the key, plays wait (not paused) until an admin adds it again.
    await owner.put("/api/admin/lastfm", json={"api_key": None}, headers=CSRF)
    await add_plays(user_id, 1)
    await send(user_id)
    waiting = (await owner.get("/api/scrobblers")).json()[0]
    assert not waiting["paused"] and waiting["pending"] == 1
    assert "isn't set up" in waiting["error"]
    await owner.put(
        "/api/admin/lastfm",
        json={"api_key": LASTFM_KEY, "shared_secret": LASTFM_SECRET},
        headers=CSRF,
    )
    await send(user_id)
    assert (await owner.get("/api/scrobblers")).json()[0]["sent"] == 3

    # Access withdrawn on last.fm: paused until connected again.
    fake.session_valid = False
    await add_plays(user_id, 1)
    await send(user_id)
    paused = (await owner.get("/api/scrobblers")).json()[0]
    assert paused["paused"] and "Connect Last.fm again" in paused["error"]


@pytest.mark.db
async def test_scrobble_servers_cant_be_this_machine(app: FastAPI, plex: FakePlex) -> None:
    owner, _ = await owner_with_library(app, plex)
    # Not this machine, cloud metadata or Plekstender's own services (home servers are fine).
    for url in (
        "http://localhost:42010/apis/listenbrainz",
        "http://127.0.0.1:42010/apis/listenbrainz",
        "http://169.254.169.254/latest",
        "http://db:5432",
        "http://[::1]:8080",
    ):
        refused = await owner.post(
            "/api/scrobblers", json={"service": "maloja", "url": url, "token": "t"}, headers=CSRF
        )
        assert (
            refused.status_code == 400 and "can't be a scrobble server" in refused.json()["detail"]
        ), url


@pytest.mark.db
async def test_a_scrobble_server_that_later_points_here_isnt_reached(
    app: FastAPI, plex: FakePlex
) -> None:
    """Checked when added, and again on every request (connecting to the address checked):
    a name that later resolves to this machine (DNS rebinding) gets nothing."""
    owner, _ = await owner_with_library(app, plex)
    user_id = (await owner.get("/api/auth/me")).json()["id"]
    servers = FakeServers(plex)
    added = await owner.post(
        "/api/scrobblers",
        json={"service": "maloja", "url": MALOJA, "token": "maloja-key"},
        headers=CSRF,
    )
    assert added.status_code == 201, added.text
    HOSTS["maloja.example"] = "127.0.0.1"
    await add_plays(user_id, 1)
    [result] = (await send(user_id)).values()
    assert result.sent == 0 and result.error and servers.maloja == []
    [listed] = (await owner.get("/api/scrobblers")).json()
    assert listed["pending"] == 1  # kept, for when the address is right again


@pytest.mark.db
async def test_a_key_without_last_fm_pro_still_scrobbles(
    app: FastAPI, plex: FakePlex, lastfm: FakeLastFm
) -> None:
    """Since 1 December 2026 Last.fm reads (imports) only for Last.fm Pro subscribers' keys,
    but scrobbles for any: such a key is kept when its shared secret works, saying so."""
    FakeLastFmWrite(plex)
    lastfm.valid_keys.clear()  # reads refused: the key's owner hasn't got Pro
    owner, _ = await owner_with_library(app, plex)

    alone = await owner.put("/api/admin/lastfm", json={"api_key": LASTFM_KEY}, headers=CSRF)
    assert alone.status_code == 400
    assert "Last.fm Pro" in alone.json()["detail"] and "scrobbling" in alone.json()["detail"]

    body = {"api_key": LASTFM_KEY, "shared_secret": LASTFM_SECRET}
    saved = (await owner.put("/api/admin/lastfm", json=body, headers=CSRF)).json()
    assert (saved["configured"], saved["scrobbling"]) == (True, True)
    assert "Last.fm Pro" in saved["note"]
    assert (await owner.get("/api/scrobblers/lastfm")).json() == {"available": True}

    # A key Last.fm doesn't know at all is still refused (its signed call fails too).
    fake = {"api_key": "f" * 32, "shared_secret": LASTFM_SECRET}
    assert (await owner.put("/api/admin/lastfm", json=fake, headers=CSRF)).status_code == 400
