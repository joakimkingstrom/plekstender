"""Logging setup: one format for every process, and secrets kept out of the logs.

httpx logs each request's full address at INFO ("HTTP Request: GET https://…"), which is
handy for seeing what Plekstender calls out to; but some services take their key in the
address (Ticketmaster's `apikey`, Last.fm's and fanart.tv's `api_key`), so the values of
key, token, secret and password parameters are replaced with `***` before anything is
written.
"""

from __future__ import annotations

import logging
import re
import sys

# A query parameter whose name says it's a secret, and its value.
_SECRET_PARAM = re.compile(
    r"(?P<name>[?&](?:[A-Za-z_]*(?:key|token|secret|password|sig)[A-Za-z_]*))=(?P<value>[^&#\s\"']+)",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """The text with secret-looking query parameter values replaced by ***."""
    return _SECRET_PARAM.sub(lambda m: f"{m['name']}=***", text)


class RedactSecrets(logging.Filter):
    """Rewrites a record's message with secret query parameters hidden."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").addFilter(RedactSecrets())
    # httpcore's connection-level messages add nothing but noise below WARNING.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
