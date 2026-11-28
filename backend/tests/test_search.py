"""The header's search."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from tests.fakes import FakePlex
from tests.test_sync import owner_with_library


@pytest.mark.db
async def test_library_search(app: FastAPI, plex: FakePlex) -> None:
    """The header's search: artists, albums and songs, each once."""
    owner, sid = await owner_with_library(app, plex)

    async def search(q: str) -> dict[str, list[tuple[int, str | None]]]:
        resp = await owner.get(f"/api/servers/{sid}/library/search", params={"q": q})
        assert resp.status_code == 200, resp.text
        return {k: [(h["rating_key"], h["subtitle"]) for h in v] for k, v in resp.json().items()}

    found = await search("head")
    assert [k for k, _ in found["artists"]] == [100, 101]  # the most popular first
    assert found["albums"] == [] and found["songs"] == []
    ok = await search("OK Computer")
    assert ok["albums"] == [(200, "Radiohead"), (201, "Radiohead")]  # the exact name first
    karma = await search("karma")
    [(key, subtitle)] = karma["songs"]  # the remaster is the same song
    assert key in (301, 302) and (subtitle or "").startswith("Radiohead · OK Computer")
    assert (
        await owner.get(f"/api/servers/{sid}/library/search", params={"q": ""})
    ).status_code == 422
