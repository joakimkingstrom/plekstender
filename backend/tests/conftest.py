"""Test configuration.

Unit tests run anywhere. Tests marked `db` need a throwaway Postgres database passed via
TEST_DATABASE_URL (CI provides one); its public schema is wiped at the start of the run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fakes import FakeLastFm, FakePlex

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

os.environ["APP_SECRET_KEY"] = "test-secret-key-test-secret-key-0123456789"
os.environ["APP_URL"] = "http://testserver"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL or "postgresql://unused@localhost:1/unused"
os.environ.pop("INITIAL_ADMIN", None)
os.environ.pop("ACCESS_MODE", None)
os.environ.pop("STATIC_DIR", None)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_db() -> None:
    import psycopg

    from roadiefm.config import get_settings
    from roadiefm.migrate import migrate

    with psycopg.connect(get_settings().libpq_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    migrate()


@pytest.fixture
async def clean_db(migrated_db: None) -> AsyncIterator[None]:
    from sqlalchemy import text

    from roadiefm.db import get_sessionmaker

    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "TRUNCATE users, sessions, plex_servers, user_servers, instance_settings,"
                " procrastinate_jobs CASCADE"
            )
        )
        await db.commit()
    yield


@pytest.fixture
async def app(clean_db: None) -> AsyncIterator[FastAPI]:
    from roadiefm.api.app import create_app
    from roadiefm.config import get_settings

    application = create_app(get_settings())
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def plex() -> Iterator[FakePlex]:
    import respx

    from tests.fakes import FakePlex

    with respx.mock(assert_all_called=False) as router:
        yield FakePlex(router)


@pytest.fixture
def lastfm(plex: FakePlex) -> FakeLastFm:
    from tests.fakes import FakeLastFm

    return FakeLastFm(plex.router)
