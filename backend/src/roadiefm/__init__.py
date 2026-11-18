"""RoadieFM: a helper app for Plex & Plexamp music libraries."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("roadiefm")
except PackageNotFoundError:  # pragma: no cover - running from a source checkout
    __version__ = "0.0.0"
