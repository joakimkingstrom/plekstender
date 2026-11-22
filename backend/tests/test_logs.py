"""Secrets are kept out of the logs."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from plekstender.logs import RedactSecrets, redact


def test_secret_query_parameters_are_hidden() -> None:
    line = (
        "HTTP Request: GET https://app.ticketmaster.com/discovery/v2/events.json"
        '?size=1&apikey=abc123&classificationName=music "HTTP/1.1 200 OK"'
    )
    assert redact(line) == (
        "HTTP Request: GET https://app.ticketmaster.com/discovery/v2/events.json"
        '?size=1&apikey=***&classificationName=music "HTTP/1.1 200 OK"'
    )
    assert redact(
        "https://ws.audioscrobbler.com/2.0/?method=user.getinfo&api_key=k1&format=json"
    ) == ("https://ws.audioscrobbler.com/2.0/?method=user.getinfo&api_key=***&format=json")
    assert redact("https://webservice.fanart.tv/v3/music/x?api_key=SECRET") == (
        "https://webservice.fanart.tv/v3/music/x?api_key=***"
    )
    assert redact("x?access_token=t&client_secret=s&api_sig=m&password=p") == (
        "x?access_token=***&client_secret=***&api_sig=***&password=***"
    )
    # Other parameters are left alone.
    assert redact("https://api.deezer.com/search/album?q=OK%20Computer") == (
        "https://api.deezer.com/search/album?q=OK%20Computer"
    )


async def test_httpx_request_lines_are_logged_without_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("httpx")
    redactor = RedactSecrets()
    logger.addFilter(redactor)
    try:
        with respx.mock() as router, caplog.at_level(logging.INFO, logger="httpx"):
            router.get("https://app.ticketmaster.com/e.json").mock(
                return_value=httpx.Response(200, json={})
            )
            async with httpx.AsyncClient() as http:
                await http.get("https://app.ticketmaster.com/e.json", params={"apikey": "abc123"})
    finally:
        logger.removeFilter(redactor)
    [record] = [r for r in caplog.records if r.name == "httpx"]
    assert "apikey=***" in record.getMessage()
    assert "abc123" not in record.getMessage()
