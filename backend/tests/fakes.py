"""A fake plex.tv and Plex Media Server (via respx) shared by the API and sync tests."""

from __future__ import annotations

import copy
import re
import time
from collections import Counter
from typing import Any

import httpx
import respx
from fastapi import FastAPI

PLEX_TV = "https://plex.tv"
CLIENTS = "https://clients.plex.tv"
SERVER_URI = "https://10-0-0-2.abc.plex.direct:32400"
CSRF = {"X-Plekstender": "1"}
NOW = 1_790_000_000
SECTION = "3"

ACCOUNTS = {
    "owner-token": {"id": 1, "uuid": "u1", "username": "owner", "email": "owner@example.com"},
    "friend-token": {"id": 2, "uuid": "u2", "username": "friend", "email": "friend@example.com"},
    "stranger-token": {"id": 3, "uuid": "u3", "username": "stranger", "email": None},
}
SERVER_TOKENS = {"srv-owner", "srv-friend"}


def home_server(owned: bool, token: str) -> dict[str, Any]:
    return {
        "name": "Home",
        "clientIdentifier": "home-machine",
        "provides": "server",
        "owned": owned,
        "accessToken": token,
        "presence": True,
        "productVersion": "1.42.0",
        "connections": [
            {
                "protocol": "https",
                "address": "10.0.0.2",
                "port": 32400,
                "uri": SERVER_URI,
                "local": True,
            }
        ],
    }


RESOURCES = {
    "owner-token": [home_server(True, "srv-owner")],
    "friend-token": [home_server(False, "srv-friend")],
    "stranger-token": [],
}


def _tags(*names: str) -> list[dict[str, str]]:
    return [{"tag": n} for n in names]


def _artist(key: int, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "ratingKey": str(key),
        "type": "artist",
        "title": title,
        "thumb": f"/library/metadata/{key}/thumb/1700000000",
        "addedAt": NOW - 10_000 + key,
        "updatedAt": NOW - 10_000,
        **extra,
    }


def _album(key: int, title: str, artist: int, year: int, **extra: Any) -> dict[str, Any]:
    return {
        "ratingKey": str(key),
        "type": "album",
        "title": title,
        "parentRatingKey": str(artist),
        "year": year,
        "thumb": f"/library/metadata/{key}/thumb/1700000000",
        "addedAt": NOW - 10_000 + key,
        "updatedAt": NOW - 10_000,
        **extra,
    }


def _track(
    key: int, title: str, album: int, artist: int, number: int, popularity: int
) -> dict[str, Any]:
    return {
        "ratingKey": str(key),
        "type": "track",
        "title": title,
        "parentRatingKey": str(album),
        "grandparentRatingKey": str(artist),
        "index": number,
        "parentIndex": 1,
        "duration": 240_000,
        "ratingCount": popularity,
        "addedAt": NOW - 10_000 + key,
        "updatedAt": NOW - 10_000,
        "Guid": [{"id": f"mbid://track-{key}"}],
        "Media": [
            {"Part": [{"key": f"/library/parts/{key}/1/file.flac", "file": f"/m/{key}.flac"}]}
        ],
    }


class FakeLibrary:
    """One music library ("3") with per-token visibility and personal state.

    Artist 102 and its album/track are hidden from the shared user (srv-friend), like a
    Plex share restricted to part of a library. "Karma Police" exists twice (original
    and remaster) with the same popularity.
    """

    def __init__(self) -> None:
        self.items: dict[int, dict[str, Any]] = {}
        self.details: dict[int, dict[str, Any]] = {}
        self.hidden_from: dict[int, set[str]] = {}
        self.state: dict[str, dict[int, dict[str, Any]]] = {t: {} for t in SERVER_TOKENS}
        self.activity_filters_supported = True
        self.multi_key_metadata = True
        self.requests: Counter[str] = Counter()

        for item in (
            _artist(100, "Radiohead", Guid=[{"id": "mbid://a74b1b7f"}]),
            _artist(101, "Portishead"),
            _artist(102, "Secret Band"),
            _album(200, "OK Computer", 100, 1997, rating=9.0, originallyAvailableAt="1997-05-21"),
            _album(201, "OK Computer OKNOTOK", 100, 2017),
            _album(202, "Dummy", 101, 1994),
            _album(203, "Secret Album", 102, 2020),
            _track(300, "Paranoid Android", 200, 100, 2, 1000),
            _track(301, "Karma Police", 200, 100, 6, 2000),
            _track(302, "Karma Police (Remastered)", 201, 100, 6, 2000),
            _track(303, "Lucky", 201, 100, 11, 500),
            _track(304, "Glory Box", 202, 101, 11, 800),
            _track(305, "Secret Song", 203, 102, 1, 10),
        ):
            self.items[int(item["ratingKey"])] = item

        self.details = {
            100: {
                "Genre": _tags("Rock", "Alternative"),
                "Mood": _tags("Melancholy"),
                "Style": _tags("Art Rock"),
                "Country": _tags("United Kingdom"),
                "Similar": _tags("Portishead", "Muse"),
            },
            101: {"Genre": _tags("Trip Hop"), "Similar": _tags("Massive Attack")},
            102: {"Genre": _tags("Rock")},
            200: {"Genre": _tags("Rock"), "Mood": _tags("Paranoid")},
        }
        for key in (102, 203, 305):
            self.hidden_from[key] = {"srv-friend"}

        self.state["srv-owner"] = {
            100: {"userRating": 8.0, "lastRatedAt": NOW - 500},
            300: {
                "userRating": 10.0,
                "viewCount": 5,
                "lastViewedAt": NOW - 500,
                "lastRatedAt": NOW - 500,
            },
        }
        self.state["srv-friend"] = {304: {"userRating": 6.0, "viewCount": 1}}

    # --- helpers for tests ---------------------------------------------------------------

    # Changes made by tests happen "now" (after the last sync), unlike the seed data.

    def touch(self, key: int, **changes: Any) -> None:
        """Change an item's metadata, as Plex would (bumps updatedAt)."""
        self.items[key].update(changes, updatedAt=int(time.time()) + 1)

    def add(self, item: dict[str, Any]) -> None:
        stamp = int(time.time()) + 1
        self.items[int(item["ratingKey"])] = item | {"addedAt": stamp, "updatedAt": stamp}

    def rate(self, token: str, key: int, rating: float) -> None:
        self.state[token].setdefault(key, {}).update(
            userRating=rating, lastRatedAt=int(time.time()) + 1
        )

    # --- request handlers ------------------------------------------------------------------

    def _visible(self, token: str) -> list[dict[str, Any]]:
        out = []
        for key, item in sorted(self.items.items(), key=lambda kv: kv[1]["addedAt"]):
            if token in self.hidden_from.get(key, set()):
                continue
            out.append({**copy.deepcopy(item), **self.state[token].get(key, {})})
        return out

    def list_items(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("X-Plex-Token", "")
        if token not in SERVER_TOKENS:
            return httpx.Response(401)
        self.requests["list"] += 1
        params = request.url.params
        kind = {"8": "artist", "9": "album", "10": "track"}[params["type"]]
        items = [i for i in self._visible(token) if i["type"] == kind]
        for name, value in params.items():
            if not name.endswith(">>"):
                continue
            field = name[:-2]
            if field in {"lastViewedAt", "lastRatedAt"} and not self.activity_filters_supported:
                return httpx.Response(400)
            items = [i for i in items if int(i.get(field) or 0) > int(value)]
        start = int(params["X-Plex-Container-Start"])
        size = int(params["X-Plex-Container-Size"])
        page = items[start : start + size]
        return httpx.Response(
            200,
            json={"MediaContainer": {"size": len(page), "totalSize": len(items), "Metadata": page}},
        )

    def metadata(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("X-Plex-Token", "")
        if token not in SERVER_TOKENS:
            return httpx.Response(401)
        self.requests["metadata"] += 1
        match = re.search(r"/library/metadata/([\d,]+)$", request.url.path)
        assert match
        keys = [int(k) for k in match.group(1).split(",")]
        if not self.multi_key_metadata:
            keys = keys[:1]
        visible = {int(i["ratingKey"]): i for i in self._visible(token)}
        found = [visible[k] | self.details.get(k, {}) for k in keys if k in visible]
        if not found:
            return httpx.Response(404)
        return httpx.Response(200, json={"MediaContainer": {"Metadata": found}})

    def photo(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Plex-Token") not in SERVER_TOKENS:
            return httpx.Response(401)
        self.requests["photo"] += 1
        return httpx.Response(
            200, content=b"\xff\xd8fake-jpeg", headers={"content-type": "image/jpeg"}
        )


class FakePlex:
    """Mocks plex.tv and one Plex server with a small music library."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.next_pin = 100
        self.approved: dict[int, str] = {}
        self.library = FakeLibrary()
        router.post(f"{PLEX_TV}/api/v2/pins").mock(side_effect=self._create_pin)
        router.get(url__regex=rf"{PLEX_TV}/api/v2/pins/\d+").mock(side_effect=self._check_pin)
        router.get(f"{PLEX_TV}/api/v2/user").mock(side_effect=self._user)
        router.get(f"{CLIENTS}/api/v2/resources").mock(side_effect=self._resources)
        router.get(f"{SERVER_URI}/identity").mock(
            return_value=httpx.Response(
                200, json={"MediaContainer": {"machineIdentifier": "home-machine"}}
            )
        )
        router.get(f"{SERVER_URI}/library/sections").mock(side_effect=self._sections)
        router.get(f"{SERVER_URI}/library/sections/{SECTION}/all").mock(
            side_effect=self.library.list_items
        )
        router.get(url__regex=rf"{re.escape(SERVER_URI)}/library/metadata/[\d,]+(\?|$)").mock(
            side_effect=self.library.metadata
        )
        router.get(f"{SERVER_URI}/photo/:/transcode").mock(side_effect=self.library.photo)

    def _create_pin(self, request: httpx.Request) -> httpx.Response:
        self.next_pin += 1
        return httpx.Response(201, json={"id": self.next_pin, "code": f"code{self.next_pin}"})

    def _check_pin(self, request: httpx.Request) -> httpx.Response:
        pin_id = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            200,
            json={"id": pin_id, "code": f"code{pin_id}", "authToken": self.approved.get(pin_id)},
        )

    def _user(self, request: httpx.Request) -> httpx.Response:
        account = ACCOUNTS.get(request.headers.get("X-Plex-Token", ""))
        return httpx.Response(200, json=account) if account else httpx.Response(401)

    def _resources(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESOURCES[request.headers["X-Plex-Token"]])

    def _sections(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Plex-Token") not in SERVER_TOKENS:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Directory": [
                        {"key": SECTION, "title": "Music", "type": "artist"},
                        {"key": "1", "title": "Movies", "type": "movie"},
                    ]
                }
            },
        )


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def sign_in(app: FastAPI, plex: FakePlex, token: str) -> tuple[httpx.AsyncClient, Any]:
    client = client_for(app)
    resp = await client.post("/api/auth/plex/pin", headers=CSRF)
    assert resp.status_code == 200
    assert "app.plex.tv/auth" in resp.json()["auth_url"]

    pending = await client.post("/api/auth/plex/check", headers=CSRF)
    assert pending.json()["status"] == "pending"

    plex.approved[plex.next_pin] = token
    result = await client.post("/api/auth/plex/check", headers=CSRF)
    assert result.status_code == 200
    return client, result.json()


LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_KEY = "0123456789abcdef0123456789abcdef"


class FakeLastFm:
    """Mocks the Last.fm API: one public user with a scrobble history, one private user."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.valid_keys = {LASTFM_KEY}
        self.private = {"hidden"}
        self.users: dict[str, list[dict[str, Any]]] = {"Joakim": []}
        self.fail_pages: dict[int, int] = {}  # page -> remaining temporary failures
        self.requests: list[dict[str, str]] = []
        router.get(LASTFM_URL).mock(side_effect=self._handle)

    def add_scrobbles(self, user: str, count: int, *, start: int, step: int = 3600) -> None:
        """`count` scrobbles from `start` (epoch seconds), every `step` seconds."""
        history = self.users.setdefault(user, [])
        for i in range(count):
            n = len(history)
            history.append(
                {
                    "uts": start + i * step,
                    "artist": "The Beatles" if n % 2 else "Radiohead",
                    "album": "Abbey Road" if n % 2 else "OK Computer",
                    "track": f"Song {n}",
                    "mbid": "c3e6f8a2-1111-4222-8333-944455556666" if n == 0 else "",
                }
            )

    def _error(self, code: int, message: str) -> httpx.Response:
        return httpx.Response(200 if code != 29 else 429, json={"error": code, "message": message})

    def _handle(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        self.requests.append(q)
        if q.get("api_key") not in self.valid_keys:
            return self._error(10, "Invalid API key - You must be granted a valid key by last.fm")
        method = q["method"].lower()
        if method == "chart.gettopartists":
            return httpx.Response(200, json={"artists": {"artist": []}})
        user = next(
            (u for u in [*self.users, *self.private] if u.lower() == q["user"].lower()), None
        )
        if user is None:
            return self._error(6, "User not found")
        if method == "user.getinfo":
            return httpx.Response(
                200,
                json={
                    "user": {
                        "name": user,
                        "playcount": str(len(self.users.get(user, []))),
                        "registered": {"unixtime": "1100000000"},
                    }
                },
            )
        assert method == "user.getrecenttracks"
        if user in self.private:
            return self._error(17, "Login: User required to be logged in")
        page, limit = int(q.get("page", 1)), int(q.get("limit", 50))
        if self.fail_pages.get(page, 0) > 0:
            self.fail_pages[page] -= 1
            return self._error(16, "There was a temporary error processing your request.")
        lo, hi = int(q.get("from", 0)), int(q.get("to", 2**62))
        matching = sorted(
            (s for s in self.users[user] if lo <= s["uts"] <= hi), key=lambda s: -s["uts"]
        )
        chunk = matching[(page - 1) * limit : page * limit]
        tracks: list[dict[str, Any]] = [
            {
                "artist": {"mbid": "", "#text": s["artist"]},
                "album": {"mbid": "", "#text": s["album"]},
                "name": s["track"],
                "mbid": s["mbid"],
                "date": {"uts": str(s["uts"]), "#text": "whenever"},
            }
            for s in chunk
        ]
        if page == 1:  # "now playing" comes first and must be skipped
            tracks.insert(
                0, {"artist": {"#text": "X"}, "name": "Y", "@attr": {"nowplaying": "true"}}
            )
        pages = (len(matching) + limit - 1) // limit
        return httpx.Response(
            200,
            json={
                "recenttracks": {
                    "track": tracks[0] if len(tracks) == 1 else tracks,
                    "@attr": {
                        "user": user,
                        "page": str(page),
                        "perPage": str(limit),
                        "totalPages": str(pages),
                        "total": str(len(matching)),
                    },
                }
            },
        )
