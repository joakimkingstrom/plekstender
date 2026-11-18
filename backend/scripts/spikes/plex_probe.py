"""Phase 0 spike: what does this Plex server expose that RoadieFM's later phases rely on?

Read-only by default. Answers:
  1. Which popularity / play-count fields exist on artists, albums and tracks, and how
     many tracks actually have popularity data.
  2. Which metadata arrays (Genre, Mood, Style, Similar...) are present.
  3. Which "hubs" Plex offers on an artist page (popular tracks etc.).
  4. Whether the sonically-similar endpoints work (needs Plex Pass + sonic analysis).
  5. (--stream-test, opt-in) Whether downloading part of a track file counts as a play.
  6. (--history) What Plex's play history exposes for music: fields, filters, how far
     back it goes and which accounts appear. Run it with the server owner's token, and,
     if you have one, again with a shared user's token to see what they can read.
     Only counts, field names and account IDs are printed, never titles or names.

Usage (from backend/):
  PLEX_URL=http://192.168.1.10:32400 PLEX_TOKEN=xxxx uv run python scripts/spikes/plex_probe.py
  ... plex_probe.py --section 3 --json report.json --stream-test
  ... plex_probe.py --history --json history-report.json

The token is read from the environment and never printed.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from typing import Any

import httpx

POPULARITY_FIELDS = [
    "ratingCount",
    "viewCount",
    "skipCount",
    "userRating",
    "audienceRating",
    "rating",
    "lastViewedAt",
    "lastRatedAt",
]
ARRAY_FIELDS = ["Genre", "Mood", "Style", "Similar", "Country", "Guid", "Collection", "Label"]
TYPES = {"artist": 8, "album": 9, "track": 10}


class Plex:
    def __init__(self, url: str, token: str) -> None:
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={
                "X-Plex-Token": token,
                "Accept": "application/json",
                "X-Plex-Product": "RoadieFM spike",
                "X-Plex-Client-Identifier": "roadiefm-spike",
            },
            timeout=30,
        )

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        resp = self.http.get(path, params=params)
        resp.raise_for_status()
        return resp.json().get("MediaContainer", {})

    def status(self, path: str, **params: Any) -> int:
        return self.http.get(path, params=params).status_code


def section_items(plex: Plex, section: str, kind: str, size: int, **params: Any) -> dict[str, Any]:
    return plex.get(
        f"/library/sections/{section}/all",
        type=TYPES[kind],
        includeGuids=1,
        **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": size},
        **params,
    )


def field_coverage(items: list[dict[str, Any]]) -> dict[str, str]:
    out = {}
    for f in POPULARITY_FIELDS:
        present = [i[f] for i in items if f in i]
        sample = present[:3]
        out[f] = f"{len(present)}/{len(items)} present, e.g. {sample}" if present else "absent"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", help="music library section key (default: first)")
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--json", help="also write the report to this file")
    parser.add_argument(
        "--stream-test",
        action="store_true",
        help="download the first 512 KB of one track and check whether a play is recorded",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="only probe play history, accounts and sessions (for listening history)",
    )
    args = parser.parse_args()

    url, token = os.environ.get("PLEX_URL"), os.environ.get("PLEX_TOKEN")
    if not url or not token:
        print("Set PLEX_URL and PLEX_TOKEN", file=sys.stderr)
        return 2
    plex = Plex(url, token)
    report: dict[str, Any] = {}

    root = plex.get("/")
    report["server"] = {k: root.get(k) for k in ("friendlyName", "version", "myPlexSubscription")}
    print(f"Server: {root.get('friendlyName')} {root.get('version')}")
    print(f"  Plex Pass (myPlexSubscription): {root.get('myPlexSubscription')}")

    sections = [
        d for d in plex.get("/library/sections").get("Directory", []) if d["type"] == "artist"
    ]
    if not sections:
        print("No music libraries found.")
        return 1
    section = args.section or sections[0]["key"]
    print(f"Music libraries: {[(s['key'], s['title']) for s in sections]}; using {section}\n")

    if args.history:
        report["history"] = history_probe(plex, [s["key"] for s in sections])
        return write_report(report, args.json)

    # 1. popularity fields per type, sorted by popularity where possible
    report["fields"] = {}
    samples: dict[str, list[dict[str, Any]]] = {}
    for kind in TYPES:
        try:
            mc = section_items(plex, section, kind, args.sample, sort="ratingCount:desc")
        except httpx.HTTPStatusError:
            mc = section_items(plex, section, kind, args.sample)
        items = mc.get("Metadata", [])
        samples[kind] = items
        coverage = field_coverage(items)
        report["fields"][kind] = {"total": mc.get("totalSize"), "coverage": coverage}
        print(f"[{kind}s] total={mc.get('totalSize')}  (sample of {len(items)})")
        for f, c in coverage.items():
            print(f"    {f:15} {c}")

    # How many tracks have popularity data at all?
    try:
        with_pop = section_items(plex, section, "track", 0, **{"ratingCount>>": 0})
        total = section_items(plex, section, "track", 0)
        report["tracks_with_ratingCount"] = [with_pop.get("totalSize"), total.get("totalSize")]
        print(
            f"\nTracks with ratingCount > 0: {with_pop.get('totalSize')} "
            f"of {total.get('totalSize')}"
        )
    except httpx.HTTPStatusError as exc:
        print(f"\nCould not filter on ratingCount: HTTP {exc.response.status_code}")

    # 2. full metadata for the top item of each type
    report["metadata_keys"] = {}
    print()
    for kind, items in samples.items():
        if not items:
            continue
        key = items[0]["ratingKey"]
        meta = plex.get(f"/library/metadata/{key}", includeGuids=1).get("Metadata", [{}])[0]
        arrays = {f: len(meta[f]) for f in ARRAY_FIELDS if isinstance(meta.get(f), list)}
        report["metadata_keys"][kind] = {"keys": sorted(meta), "arrays": arrays}
        print(f"[{kind} detail: {meta.get('title')!r}] arrays: {arrays}")
        print(f"    keys: {', '.join(sorted(meta))}")

    # 3. hubs on an artist page
    if samples["artist"]:
        artist = samples["artist"][0]
        hubs = plex.get(f"/hubs/metadata/{artist['ratingKey']}").get("Hub", [])
        report["artist_hubs"] = [
            {"id": h.get("hubIdentifier"), "title": h.get("title"), "size": h.get("size")}
            for h in hubs
        ]
        print(f"\nHubs for artist {artist['title']!r}:")
        for h in report["artist_hubs"]:
            print(f"    {h['id']:40} {h['title']!r} ({h['size']})")

    # 4. sonic similarity
    report["sonic"] = {}
    print("\nSonically similar (/nearest):")
    for kind, items in samples.items():
        if not items:
            continue
        key = items[0]["ratingKey"]
        resp = plex.http.get(f"/library/metadata/{key}/nearest", params={"limit": 10})
        count = None
        if resp.status_code == 200:
            count = len(resp.json().get("MediaContainer", {}).get("Metadata", []))
        report["sonic"][kind] = {"status": resp.status_code, "results": count}
        print(f"    {kind:7} HTTP {resp.status_code}, {count} results")

    # 5. does fetching the file count as a play?
    if args.stream_test and samples["track"]:
        report["stream_test"] = stream_test(plex, samples["track"][0]["ratingKey"])

    return write_report(report, args.json)


def write_report(report: dict[str, Any], path: str | None) -> int:
    if path:
        with open(path, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nReport written to {path}")
    return 0


HISTORY = "/status/sessions/history/all"
HISTORY_FIELDS = [
    "historyKey",
    "ratingKey",
    "key",
    "parentKey",
    "grandparentKey",
    "librarySectionID",
    "type",
    "viewedAt",
    "accountID",
    "deviceID",
    "title",
    "parentTitle",
    "grandparentTitle",
    "originalTitle",
]


def history_page(plex: Plex, size: int, **params: Any) -> tuple[int | None, dict[str, Any]]:
    """(HTTP status, MediaContainer) without raising, so rejected filters are reported."""
    resp = plex.http.get(
        HISTORY,
        params={"X-Plex-Container-Start": 0, "X-Plex-Container-Size": size, **params},
    )
    if resp.status_code != 200:
        return resp.status_code, {}
    return 200, resp.json().get("MediaContainer", {})


def history_probe(plex: Plex, music_sections: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    print("Play history (/status/sessions/history/all):")

    status, everything = history_page(plex, 0)
    out["read_status"] = status
    if status != 200:
        print(f"    HTTP {status}: this token can't read play history.")
        return out
    out["total_all_types"] = everything.get("totalSize")

    # The newest entries of any type: which types appear, and does `type=` filter at all?
    status, newest_any = history_page(plex, 300, sort="viewedAt:desc")
    type_counts: dict[str, int] = {}
    for e in newest_any.get("Metadata", []):
        type_counts[str(e.get("type"))] = type_counts.get(str(e.get("type")), 0) + 1
    out["types_in_newest_300"] = type_counts
    status, typed = history_page(plex, 0, type=TYPES["track"])
    out["type_filter"] = {"status": status, "total": typed.get("totalSize")}
    status, typed = history_page(plex, 0, metadataType=TYPES["track"])
    out["metadataType_filter"] = {"status": status, "total": typed.get("totalSize")}
    print(f"    all entries: {out['total_all_types']}; types in newest 300: {type_counts}")
    print(
        f"    type=10 filter: {out['type_filter']}; metadataType=10: {out['metadataType_filter']}"
    )

    # Music plays, per music library section.
    tracks: list[dict[str, Any]] = []
    out["per_section"] = {}
    for key in music_sections:
        status, page = history_page(plex, 200, sort="viewedAt:desc", librarySectionID=key)
        entries = page.get("Metadata", [])
        section_types: dict[str, int] = {}
        for e in entries:
            section_types[str(e.get("type"))] = section_types.get(str(e.get("type")), 0) + 1
        status_old, oldest = history_page(plex, 1, sort="viewedAt:asc", librarySectionID=key)
        first = (oldest.get("Metadata") or [{}])[0].get("viewedAt")
        info = {
            "status": status,
            "total": page.get("totalSize"),
            "types_in_newest_200": section_types,
            "oldest_play": time.strftime("%Y-%m-%d", time.gmtime(int(first))) if first else None,
            "sorted_asc_status": status_old,
        }
        out["per_section"][key] = info
        print(f"    librarySectionID={key}: {info}")
        tracks += [e for e in entries if e.get("type") == "track"]

    tracks.sort(key=lambda e: int(e.get("viewedAt", 0)), reverse=True)
    out["sample_size"] = len(tracks)
    out["fields"] = {f: f"{sum(1 for e in tracks if f in e)}/{len(tracks)}" for f in HISTORY_FIELDS}
    out["extra_fields"] = sorted({k for e in tracks for k in e} - set(HISTORY_FIELDS))
    print(f"    fields on {len(tracks)} newest music plays:")
    for f, c in out["fields"].items():
        print(f"        {f:18} {c}")
    print(f"        other keys: {', '.join(out['extra_fields'])}")

    # Do the plays' items still resolve? (history outlives deleted / re-added tracks)
    keys = list(dict.fromkeys(e["ratingKey"] for e in tracks if e.get("ratingKey")))[:50]
    if keys:
        found = plex.get(f"/library/metadata/{','.join(keys)}").get("Metadata", [])
        out["rating_keys_resolving"] = f"{len(found)}/{len(keys)}"
        print(f"    ratingKeys that still exist: {out['rating_keys_resolving']}")

    # viewedAt vs. the track's duration: is viewedAt the start or the end of a play?
    # Gaps between consecutive plays on the same account and device hint at it.
    gaps = []
    for a, b in itertools.pairwise(tracks):
        if (a.get("accountID"), a.get("deviceID")) == (b.get("accountID"), b.get("deviceID")):
            gaps.append(int(a["viewedAt"]) - int(b["viewedAt"]))
    if gaps:
        gaps.sort()
        out["consecutive_play_gap_seconds"] = {
            "median": gaps[len(gaps) // 2],
            "p10": gaps[len(gaps) // 10],
            "p90": gaps[len(gaps) * 9 // 10],
        }
        print(f"    seconds between consecutive plays: {out['consecutive_play_gap_seconds']}")

    if tracks:
        middle = len(tracks) // 2
        cutoff = int(tracks[middle]["viewedAt"])
        status, since = history_page(
            plex, 0, librarySectionID=music_sections[0], **{"viewedAt>": cutoff}
        )
        out["viewedAt_filter"] = {
            "status": status,
            "total": since.get("totalSize"),
            "unfiltered_section_total": out["per_section"][music_sections[0]]["total"],
        }
        print(f"    viewedAt> filter on section {music_sections[0]}: {out['viewedAt_filter']}")
        # Does this server need the music library's plays to be re-requested per account?
        status, mine = history_page(plex, 0, librarySectionID=music_sections[0], accountID=1)
        out["accountID_filter"] = {"status": status, "total": mine.get("totalSize")}
        print(f"    accountID=1 filter: {out['accountID_filter']}")

    # Which accounts play music, and how their IDs look (IDs only, no names).
    per_account: dict[str, int] = {}
    for e in tracks:
        per_account[str(e.get("accountID"))] = per_account.get(str(e.get("accountID")), 0) + 1
    out["plays_per_account_in_sample"] = per_account
    resp = plex.http.get("/accounts")
    if resp.status_code == 200:
        accounts = resp.json().get("MediaContainer", {}).get("Account", [])
        out["server_account_ids"] = sorted(int(a["id"]) for a in accounts if "id" in a)
    else:
        out["server_account_ids"] = f"HTTP {resp.status_code}"
    print(f"    plays per accountID in sample: {per_account}")
    print(f"    accounts on server (/accounts): {out['server_account_ids']}")

    me = httpx.get(
        "https://plex.tv/api/v2/user",
        headers={**plex.http.headers, "Accept": "application/json"},
        timeout=30,
    )
    if me.status_code == 200:
        plex_tv_id = me.json().get("id")
        out["token_owner_plex_tv_id_in_accounts"] = plex_tv_id in (
            out["server_account_ids"] if isinstance(out["server_account_ids"], list) else []
        )
        print(
            f"    token owner's plex.tv ID listed in /accounts: "
            f"{out['token_owner_plex_tv_id_in_accounts']} (the owner is usually account 1)"
        )

    # Now playing: what a "playing now" scrobble could use.
    resp = plex.http.get("/status/sessions")
    if resp.status_code == 200:
        sessions = resp.json().get("MediaContainer", {}).get("Metadata", [])
        music = [s for s in sessions if s.get("type") == "track"]
        out["now_playing"] = {
            "status": 200,
            "music_sessions": len(music),
            "keys": sorted({k for s in music for k in s}),
            "user_keys": sorted({k for s in music for k in s.get("User", {})}),
            "player_keys": sorted({k for s in music for k in s.get("Player", {})}),
        }
    else:
        out["now_playing"] = {"status": resp.status_code}
    print(f"    now playing (/status/sessions): {out['now_playing']}")
    return out


def stream_test(plex: Plex, rating_key: str) -> dict[str, Any]:
    def snapshot() -> tuple[Any, Any, int]:
        meta = plex.get(f"/library/metadata/{rating_key}").get("Metadata", [{}])[0]
        history = plex.get(
            "/status/sessions/history/all",
            metadataItemID=rating_key,
            **{"X-Plex-Container-Size": 0},
        )
        return meta.get("viewCount"), meta.get("lastViewedAt"), int(history.get("totalSize", 0))

    meta = plex.get(f"/library/metadata/{rating_key}").get("Metadata", [{}])[0]
    part = meta["Media"][0]["Part"][0]["key"]
    before = snapshot()
    print(
        f"\nStream test on {meta.get('title')!r}: "
        f"before (viewCount, lastViewedAt, history) = {before}"
    )
    with plex.http.stream("GET", part, headers={"Range": "bytes=0-524287"}) as resp:
        size = sum(len(chunk) for chunk in resp.iter_bytes())
    print(f"    downloaded {size} bytes (HTTP {resp.status_code}); waiting 20 s...")
    time.sleep(20)
    after = snapshot()
    recorded = after != before
    print(f"    after = {after} -> {'PLAY RECORDED' if recorded else 'no play recorded'}")
    return {"before": before, "after": after, "play_recorded": recorded}


if __name__ == "__main__":
    sys.exit(main())
