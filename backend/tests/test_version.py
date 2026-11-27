"""The version the app reports: the image's, or a development marker."""

from pathlib import Path

import pytest

import plekstender


def test_published_image_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    build = tmp_path / "build.json"
    build.write_text('{"version": " 0.1.212 ", "commit": "abc"}\n')
    monkeypatch.setattr(plekstender, "BUILD_FILE", build)
    # A deployment's own variable (the image tag it pulls) doesn't change it.
    monkeypatch.setenv("PLEKSTENDER_VERSION", "latest")
    assert plekstender._version() == "0.1.212"


def test_build_without_a_version_is_marked_dev(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    build = tmp_path / "build.json"
    build.write_text('{"version": "", "commit": ""}\n')  # what unset build args leave behind
    monkeypatch.setattr(plekstender, "BUILD_FILE", build)
    assert plekstender._version().endswith("-dev")
    monkeypatch.setattr(plekstender, "BUILD_FILE", tmp_path / "missing.json")
    assert plekstender._version().endswith("-dev")
