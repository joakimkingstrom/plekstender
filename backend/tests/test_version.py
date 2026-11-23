"""The version the app reports: the image's, or a development marker."""

import pytest

import plekstender


def test_published_image_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLEKSTENDER_VERSION", " 0.1.212 ")
    assert plekstender._version() == "0.1.212"


def test_build_without_a_version_is_marked_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLEKSTENDER_VERSION", "")  # what an unset build arg leaves behind
    assert plekstender._version().endswith("-dev")
