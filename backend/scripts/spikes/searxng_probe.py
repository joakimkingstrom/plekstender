"""Phase 0 spike: how useful are SearXNG image results for "<artist> logo"?

Start SearXNG with a temporary port mapping (it has none in compose):
  docker compose --profile search run --rm -p 8888:8080 searxng

Then, from backend/:
  SEARXNG_URL=http://localhost:8888 uv run python scripts/spikes/searxng_probe.py \
      "Radiohead" "Earth, Wind & Fire" "St. Paul & The Broken Bones"
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from urllib.parse import urlsplit

import httpx


def probe(http: httpx.Client, base: str, artist: str) -> None:
    resp = http.get(
        f"{base}/search",
        params={"q": f"{artist} logo", "categories": "images", "format": "json"},
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    engines = Counter(e for r in results for e in r.get("engines", []))
    formats = Counter(
        (r.get("img_format") or urlsplit(r.get("img_src", "")).path.rsplit(".", 1)[-1])[:8].lower()
        for r in results
    )
    print(f"\n{artist!r}: {len(results)} results; engines {dict(engines)}")
    print(f"    formats: {dict(formats.most_common(6))}")
    if data.get("unresponsive_engines"):
        print(f"    unresponsive engines: {data['unresponsive_engines']}")
    for r in results[:5]:
        host = urlsplit(r.get("img_src", "")).hostname
        print(f"    - {r.get('title', '')[:60]!r} [{r.get('resolution') or '?'}] {host}")


def main() -> int:
    base = os.environ.get("SEARXNG_URL", "http://localhost:8888").rstrip("/")
    artists = sys.argv[1:] or ["Radiohead", "The Beatles", "Nathaniel Rateliff"]
    with httpx.Client(timeout=30) as http:
        for artist in artists:
            probe(http, base, artist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
