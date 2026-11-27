"""Plekstender: a helper app for Plex & Plexamp music libraries."""

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

# Written when the image is built (see the Dockerfile): its version and commit. Read from a
# file, not the environment, so a deployment's own variables can't change them.
BUILD_FILE = Path("/app/build.json")


def _build() -> dict[str, Any]:
    try:
        found = json.loads(BUILD_FILE.read_text())
    except OSError, ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _version() -> str:
    """The published image's version (set when it's built), or the package's own for a build
    without one (a source checkout, or `docker compose build`), marked as a development build."""
    if built := str(_build().get("version") or "").strip():
        return built
    try:
        return f"{version('plekstender')}-dev"
    except PackageNotFoundError:  # pragma: no cover - running from a source tree without installing
        return "0.0.0-dev"


__version__ = _version()
# The commit the image was built from (empty outside published images).
__commit__ = str(_build().get("commit") or "").strip() or None
