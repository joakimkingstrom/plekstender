"""Upgrading procrastinate's schema: new migrations, including ones that can't run inside
a transaction (CREATE INDEX CONCURRENTLY), are applied once and recorded."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from plekstender import migrate
from plekstender.config import get_settings


@pytest.mark.db
def test_new_procrastinate_migrations_are_applied_once(
    migrated_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conninfo = get_settings().libpq_url
    alone = tmp_path / "99.00.00_01_pre_add_test_index.sql"
    alone.write_text(
        "-- built concurrently: has to run outside a transaction\n"
        "CREATE INDEX CONCURRENTLY plekstender_test_idx ON procrastinate_jobs (queue_name);\n"
    )
    plain = tmp_path / "99.00.00_50_post_add_test_function.sql"
    plain.write_text(
        "CREATE FUNCTION plekstender_test_fn() RETURNS int LANGUAGE sql AS 'SELECT 1';\n"
        "COMMENT ON FUNCTION plekstender_test_fn() IS 'two statements in one transaction';\n"
    )
    bundled = migrate._migration_files()
    monkeypatch.setattr(migrate, "_migration_files", lambda: [*bundled, alone, plain])
    try:
        assert migrate.apply_procrastinate_schema(conninfo) == [alone.name, plain.name]
        assert migrate.apply_procrastinate_schema(conninfo) == []  # recorded: not again
        with psycopg.connect(conninfo) as conn:
            index = conn.execute("SELECT to_regclass('plekstender_test_idx')").fetchone()
            assert index and index[0] is not None
    finally:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute("DROP INDEX IF EXISTS plekstender_test_idx")
            conn.execute("DROP FUNCTION IF EXISTS plekstender_test_fn()")
            conn.execute(
                f"DELETE FROM {migrate.TRACKING_TABLE} WHERE name LIKE '99.00.00%'"  # noqa: S608
            )
