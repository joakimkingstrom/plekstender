"""A fake plex.tv and Plex Media Server (via respx) shared by the API and sync tests."""

from __future__ import annotations

import copy
import json
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
        # Play history, as /status/sessions/history/all lists it.
        self.history: list[dict[str, Any]] = []
        self.history_denied: set[str] = set()  # tokens Plex refuses history to
        self.rates: list[tuple[str, int, float]] = []  # (token, key, rating) as Plex got them
        self.posters: dict[int, list[dict[str, Any]]] = {
            100: [
                {"key": "https://images.example-agent.com/radiohead.jpg", "provider": "agent"},
                {
                    "key": "/library/metadata/100/file?url=upload%3A%2F%2Fposters%2Fabc",
                    "provider": "upload",
                    "selected": True,
                },
            ]
        }
        self.uploaded: list[tuple[int, bytes]] = []
        # /status/sessions: what's playing (each entry needs "User": {"id": ...}).
        self.now_playing: list[dict[str, Any]] = []
        self.rate_fails = False
        # Playlists by key: {"token", "title", "summary", "items": [rating keys]}.
        self.playlists: dict[int, dict[str, Any]] = {}
        self.next_playlist = 9000
        self.bulk_clear_supported = True
        # /library/metadata/{key}/nearest (sonic analysis); missing keys give 404.
        self.nearest: dict[int, list[int]] = {}

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
            200: {"Genre": _tags("Rock"), "Mood": _tags("Paranoid"), "Format": _tags("Album")},
            201: {"Format": _tags("Album"), "Subformat": _tags("Compilation")},
            202: {"Format": _tags("EP")},
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

    def play(self, account: int, key: int, viewed_at: int, *, section: str = SECTION) -> None:
        """Record a play in the history, as Plex does when a track (nearly) finishes."""
        item = self.items.get(key, {"type": "movie", "title": f"Movie {key}"})
        album = self.items.get(int(item.get("parentRatingKey", 0)), {})
        artist = self.items.get(int(item.get("grandparentRatingKey", 0)), {})
        self.history.append(
            {
                "historyKey": f"/status/sessions/history/{len(self.history) + 1}",
                "ratingKey": str(key),
                "key": f"/library/metadata/{key}",
                "librarySectionID": section,
                "type": item["type"],
                "title": item["title"],
                "parentTitle": album.get("title"),
                "grandparentTitle": artist.get("title"),
                "viewedAt": viewed_at,
                "accountID": account,
                "deviceID": 7,
            }
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

    def rate_request(self, request: httpx.Request) -> httpx.Response:
        """PUT /:/rate: -1 clears the token owner's rating."""
        token = request.headers.get("X-Plex-Token", "")
        if token not in SERVER_TOKENS:
            return httpx.Response(401)
        if self.rate_fails:
            return httpx.Response(500)
        params = request.url.params
        assert params["identifier"] == "com.plexapp.plugins.library"
        key, rating = int(params["key"]), float(params["rating"])
        self.rates.append((token, key, rating))
        state = self.state[token].setdefault(key, {})
        if rating < 0:
            state.pop("userRating", None)
        else:
            state["userRating"] = rating
        return httpx.Response(200)

    def poster_request(self, request: httpx.Request) -> httpx.Response:
        """GET lists an item's posters; POST uploads one (Plex then selects it)."""
        if request.headers.get("X-Plex-Token") != "srv-owner":
            return httpx.Response(401)
        key = int(request.url.path.split("/")[3])
        if request.method == "POST":
            self.uploaded.append((key, request.content))
            self.items[key]["thumb"] = f"/library/metadata/{key}/thumb/{len(self.uploaded)}"
            return httpx.Response(200)
        posters = self.posters.get(key, [])
        return httpx.Response(200, json={"MediaContainer": {"Metadata": posters}})

    def playing(
        self, account: int, key: int, offset_ms: int, session: str, *, lan: bool, address: str
    ) -> dict[str, Any]:
        """A track playing now, as /status/sessions lists it."""
        item = self.items[key]
        return {
            "type": "track",
            "ratingKey": str(key),
            "sessionKey": session,
            "viewOffset": offset_ms,
            "duration": item.get("duration"),
            "title": item["title"],
            "User": {"id": str(account), "title": f"account {account}"},
            "Player": {
                "title": "Joakim's iPhone" if account == 1 else "Living room",
                "product": "Plexamp",
                "local": lan,
                "address": address if lan else "10.0.0.1",
                **({} if lan else {"remotePublicAddress": address}),
            },
            "Session": {"id": session, "location": "lan" if lan else "wan"},
        }

    def sessions_list(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("X-Plex-Token", "")
        if token not in SERVER_TOKENS:
            return httpx.Response(401)
        mine = [p for p in self.now_playing if token == "srv-owner" or p["User"]["id"] == "2"]
        return httpx.Response(200, json={"MediaContainer": {"size": len(mine), "Metadata": mine}})

    def history_list(self, request: httpx.Request) -> httpx.Response:
        """The owner sees everyone's plays; a shared user (account 2) only their own."""
        token = request.headers.get("X-Plex-Token", "")
        if token not in SERVER_TOKENS:
            return httpx.Response(401)
        if token in self.history_denied:
            return httpx.Response(403)
        self.requests["history"] += 1
        params = request.url.params
        entries = [e for e in self.history if token == "srv-owner" or e["accountID"] == 2]
        if "librarySectionID" in params:
            entries = [e for e in entries if e["librarySectionID"] == params["librarySectionID"]]
        if "viewedAt>" in params:
            entries = [e for e in entries if e["viewedAt"] >= int(params["viewedAt>"])]
        entries.sort(key=lambda e: e["viewedAt"])
        start = int(params.get("X-Plex-Container-Start", 0))
        size = int(params.get("X-Plex-Container-Size", 50))
        page = entries[start : start + size]
        return httpx.Response(
            200,
            json={
                "MediaContainer": {"size": len(page), "totalSize": len(entries), "Metadata": page}
            },
        )

    # --- playlists ----------------------------------------------------------------------

    def _uri_keys(self, uri: str) -> list[int]:
        match = re.fullmatch(
            r"server://home-machine/com\.plexapp\.plugins\.library/library/metadata/([\d,]+)",
            uri,
        )
        assert match, uri
        return [int(k) for k in match.group(1).split(",")]

    def playlists_create(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("X-Plex-Token", "")
        if token not in SERVER_TOKENS:
            return httpx.Response(401)
        params = request.url.params
        assert (params["type"], params["smart"]) == ("audio", "0")
        self.next_playlist += 1
        key = self.next_playlist
        self.playlists[key] = {
            "token": token,
            "title": params["title"],
            "summary": "",
            "items": self._uri_keys(params["uri"]),
        }
        return httpx.Response(200, json={"MediaContainer": {"Metadata": [{"ratingKey": str(key)}]}})

    def playlist_request(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("X-Plex-Token", "")
        if token not in SERVER_TOKENS:
            return httpx.Response(401)
        parts = request.url.path.split("/")[2:]  # [key] | [key, "items"] | [key, "items", id]
        playlist = self.playlists.get(int(parts[0]))
        if playlist is None or playlist["token"] != token:
            return httpx.Response(404)
        key = int(parts[0])
        if len(parts) == 1:
            if request.method == "DELETE":
                del self.playlists[key]
            else:
                playlist["title"] = request.url.params["title"]
                playlist["summary"] = request.url.params.get("summary", "")
            return httpx.Response(200)
        if len(parts) == 3:
            assert request.method == "DELETE"
            index = int(parts[2]) - 1
            playlist["items"][index] = None
            return httpx.Response(200)
        if request.method == "GET":
            items = [
                {"ratingKey": str(k), "playlistItemID": i + 1}
                for i, k in enumerate(playlist["items"])
                if k is not None
            ]
            return httpx.Response(200, json={"MediaContainer": {"Metadata": items}})
        if request.method == "DELETE":
            if not self.bulk_clear_supported:
                return httpx.Response(400)
            playlist["items"] = []
            return httpx.Response(200)
        playlist["items"] = [k for k in playlist["items"] if k is not None]
        playlist["items"] += self._uri_keys(request.url.params["uri"])
        return httpx.Response(200)

    def nearest_request(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Plex-Token") not in SERVER_TOKENS:
            return httpx.Response(401)
        key = int(request.url.path.split("/")[3])
        if key not in self.nearest:
            return httpx.Response(404)
        found = [self.items[k] for k in self.nearest[key]]
        return httpx.Response(200, json={"MediaContainer": {"Metadata": found}})

    def photo(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Plex-Token") not in SERVER_TOKENS:
            return httpx.Response(401)
        self.requests["photo"] += 1
        return httpx.Response(
            200, content=sample_image("JPEG"), headers={"content-type": "image/jpeg"}
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
        router.put(f"{SERVER_URI}/:/rate").mock(side_effect=self.library.rate_request)
        router.route(url__regex=rf"{re.escape(SERVER_URI)}/library/metadata/\d+/posters").mock(
            side_effect=self.library.poster_request
        )
        router.get(f"{SERVER_URI}/status/sessions").mock(side_effect=self.library.sessions_list)
        router.post(f"{SERVER_URI}/playlists").mock(side_effect=self.library.playlists_create)
        router.route(url__regex=rf"{re.escape(SERVER_URI)}/playlists/\d+").mock(
            side_effect=self.library.playlist_request
        )
        router.get(url__regex=rf"{re.escape(SERVER_URI)}/library/metadata/\d+/nearest").mock(
            side_effect=self.library.nearest_request
        )
        router.get(f"{SERVER_URI}/status/sessions/history/all").mock(
            side_effect=self.library.history_list
        )

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


def sample_image(fmt: str = "PNG", size: tuple[int, int] = (300, 120)) -> bytes:
    """A small real image: a white box on a transparent (PNG) or black background."""
    import io

    from PIL import Image, ImageDraw

    mode = "RGBA" if fmt == "PNG" else "RGB"
    img = Image.new(mode, size, (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0))
    ImageDraw.Draw(img).rectangle((50, 20, size[0] - 50, size[1] - 20), fill="white")
    out = io.BytesIO()
    img.save(out, fmt)
    return out.getvalue()


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


LISTENBRAINZ_URL = "https://api.listenbrainz.org"


class FakeListenBrainz:
    """Mocks ListenBrainz: token validation and listen submission."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.tokens = {"lb-token-1111": "joakim_lb", "lb-token-2222": "joakim_lb"}
        self.submissions: list[dict[str, Any]] = []
        self.refused_tracks: set[str] = set()  # 400 for listens of these tracks
        self.next_status: list[int] = []  # forced responses for the next submissions
        # Public listen history per username, newest first when listed.
        self.histories: dict[str, list[dict[str, Any]]] = {}
        self.fail_listens_from: int | None = None  # 503 from this listen-page request on
        self.listen_requests: list[dict[str, str]] = []
        router.get(f"{LISTENBRAINZ_URL}/1/validate-token").mock(side_effect=self._validate)
        router.post(f"{LISTENBRAINZ_URL}/1/submit-listens").mock(side_effect=self._submit)
        router.get(url__regex=rf"{LISTENBRAINZ_URL}/1/user/[^/]+/listen-count").mock(
            side_effect=self._count
        )
        router.get(url__regex=rf"{LISTENBRAINZ_URL}/1/user/[^/]+/listens").mock(
            side_effect=self._listens
        )

    def add_history(self, user: str, ts: int, artist: str, track: str, **info: Any) -> None:
        listen: dict[str, Any] = {
            "listened_at": ts,
            "track_metadata": {
                "artist_name": artist,
                "track_name": track,
                "release_name": info.pop("album", None),
                "additional_info": info,
            },
        }
        self.histories.setdefault(user, []).append(listen)

    def _user(self, request: httpx.Request) -> str:
        return request.url.path.split("/")[3]

    def _count(self, request: httpx.Request) -> httpx.Response:
        user = self._user(request)
        if user not in self.histories:
            return httpx.Response(404, json={"code": 404, "error": "Cannot find user"})
        return httpx.Response(200, json={"payload": {"count": len(self.histories[user])}})

    def _listens(self, request: httpx.Request) -> httpx.Response:
        user = self._user(request)
        if user not in self.histories:
            return httpx.Response(404, json={"code": 404, "error": "Cannot find user"})
        params = dict(request.url.params)
        self.listen_requests.append(params)
        if (
            self.fail_listens_from is not None
            and len(self.listen_requests) >= self.fail_listens_from
        ):
            return httpx.Response(503)
        max_ts = int(params.get("max_ts", 2**62))
        count = min(int(params.get("count", 25)), 1000)
        page = sorted(
            (x for x in self.histories[user] if x["listened_at"] < max_ts),
            key=lambda x: -x["listened_at"],
        )[:count]
        return httpx.Response(200, json={"payload": {"count": len(page), "listens": page}})

    def _token(self, request: httpx.Request) -> str:
        return request.headers.get("Authorization", "").removeprefix("Token ")

    def _validate(self, request: httpx.Request) -> httpx.Response:
        user = self.tokens.get(self._token(request))
        if user is None:
            return httpx.Response(
                200, json={"code": 200, "message": "Token invalid.", "valid": False}
            )
        return httpx.Response(
            200, json={"code": 200, "message": "Token valid.", "valid": True, "user_name": user}
        )

    def _submit(self, request: httpx.Request) -> httpx.Response:
        if self._token(request) not in self.tokens:
            return httpx.Response(401, json={"code": 401, "error": "Invalid authorization token."})
        if self.next_status:
            status = self.next_status.pop(0)
            return httpx.Response(
                status,
                json={"code": status, "error": "forced"},
                headers={"X-RateLimit-Reset-In": "7"},
            )
        body = json.loads(request.content)
        listens = body["payload"]
        if body["listen_type"] == "single":
            assert len(listens) == 1
        if any(listen["track_metadata"]["track_name"] in self.refused_tracks for listen in listens):
            return httpx.Response(400, json={"code": 400, "error": "Invalid listen."})
        self.submissions.append(body)
        return httpx.Response(200, json={"status": "ok"})

    @property
    def listens(self) -> list[dict[str, Any]]:
        return [listen for body in self.submissions for listen in body["payload"]]
