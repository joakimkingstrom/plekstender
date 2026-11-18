"""Phase 0 spike: what does this Plex server expose that RoadieFM's later phases rely on?

Read-only by default. Answers:
  1. Which popularity / play-count fields exist on artists, albums and tracks, and how
     many tracks actually have popularity data.
  2. Which metadata arrays (Genre, Mood, Style, Similar...) are present.
  3. Which "hubs" Plex offers on an artist page (popular tracks etc.).
  4. Whether the sonically-similar endpoints work (needs Plex Pass + sonic analysis).
  5. (--stream-test, opt-in) Whether downloading part of a track file counts as a play.

Usage (from backend/):
  PLEX_URL=http://192.168.1.10:32400 PLEX_TOKEN=xxxx uv run python scripts/spikes/plex_probe.py
  ... plex_probe.py --section 3 --json report.json --stream-test

The token is read from the environment and never printed.
"""

from __future__ import annotations

import argparse
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

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nReport written to {args.json}")
    return 0


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
