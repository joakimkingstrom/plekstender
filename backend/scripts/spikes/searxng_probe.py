"""Phase 0 spike: how useful are SearXNG image results for "<artist> logo"?

SearXNG must be running and reachable first. On any machine with Docker (e.g. the
server that runs your other stacks), from the repo root:

  docker run --rm -p 8888:8080 -e SEARXNG_SECRET=$(openssl rand -hex 32) \
      -v "$PWD/searxng/settings.yml:/etc/searxng/settings.yml:ro" searxng/searxng

(or `docker compose --profile search run --rm -p 8888:8080 searxng` with SEARXNG_SECRET
set in .env). Then, from backend/:

  uv run python scripts/spikes/searxng_probe.py --url http://<docker-host>:8888 \
      "Radiohead" "Earth, Wind & Fire" --json searxng-report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_ARTISTS = ["Radiohead", "The Beatles", "Nathaniel Rateliff", "St. Paul & The Broken Bones"]


def probe(http: httpx.Client, base: str, artist: str) -> dict[str, Any]:
    resp = http.get(
        f"{base}/search",
        params={"q": f"{artist} logo", "categories": "images", "format": "json"},
    )
    if resp.status_code == 403:
        raise SystemExit(
            "SearXNG returned 403: JSON output is disabled. Use the settings.yml from this "
            "repo (search.formats must include json)."
        )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    engines = Counter(e for r in results for e in r.get("engines", []))
    formats = Counter(
        (r.get("img_format") or urlsplit(r.get("img_src", "")).path.rsplit(".", 1)[-1])[:8].lower()
        for r in results
    )
    top = [
        {
            "title": r.get("title", "")[:80],
            "resolution": r.get("resolution"),
            "host": urlsplit(r.get("img_src", "")).hostname,
            "img_src": r.get("img_src"),
        }
        for r in results[:10]
    ]

    print(f"\n{artist!r}: {len(results)} results; engines {dict(engines)}")
    print(f"    formats: {dict(formats.most_common(6))}")
    if data.get("unresponsive_engines"):
        print(f"    unresponsive engines: {data['unresponsive_engines']}")
    for r in top[:5]:
        print(f"    - {r['title'][:60]!r} [{r['resolution'] or '?'}] {r['host']}")
    return {
        "results": len(results),
        "engines": dict(engines),
        "formats": dict(formats),
        "unresponsive_engines": data.get("unresponsive_engines", []),
        "top": top,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("artists", nargs="*", default=DEFAULT_ARTISTS)
    parser.add_argument(
        "--url",
        default=os.environ.get("SEARXNG_URL", "http://localhost:8888"),
        help="SearXNG base URL (default: $SEARXNG_URL or http://localhost:8888)",
    )
    parser.add_argument("--json", help="also write the report to this file")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    report: dict[str, Any] = {}
    with httpx.Client(timeout=30) as http:
        try:
            http.get(f"{base}/healthz")
        except httpx.ConnectError:
            print(
                f"Can't connect to SearXNG at {base}. Is it running, and is the port mapped?\n"
                "See the instructions at the top of this script.",
                file=sys.stderr,
            )
            return 2
        for artist in args.artists:
            report[artist] = probe(http, base, artist)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nReport written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
