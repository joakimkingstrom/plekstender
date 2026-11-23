"""Plekstender: a helper app for Plex & Plexamp music libraries."""

import os
from importlib.metadata import PackageNotFoundError, version


def _version() -> str:
    """The published image's version (set when it's built), or the package's own for a build
    without one (a source checkout, or `docker compose build`), marked as a development build."""
    if built := os.environ.get("PLEKSTENDER_VERSION", "").strip():
        return built
    try:
        return f"{version('plekstender')}-dev"
    except PackageNotFoundError:  # pragma: no cover - running from a source tree without installing
        return "0.0.0-dev"


__version__ = _version()
# The commit the image was built from (empty outside published images).
__commit__ = os.environ.get("PLEKSTENDER_COMMIT", "").strip() or None
