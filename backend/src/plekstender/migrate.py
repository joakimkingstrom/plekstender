"""Database migrations: Alembic for Plekstender's own tables, plus procrastinate's schema.

Procrastinate ships its schema as SQL plus incremental migration files. On a fresh
database the full schema is applied and all bundled migrations are marked as done; on
later runs (after upgrading procrastinate) any new migration files are applied in order.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from procrastinate.schema import SchemaManager

from plekstender.config import get_settings

log = logging.getLogger(__name__)

TRACKING_TABLE = "plekstender_procrastinate_migrations"  # constant; safe to interpolate
# Serialises concurrent `migrate` runs (e.g. two db-init containers).
MIGRATE_LOCK_ID = 0x524F4D49  # "ROMI"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "plekstender:migrations")
    return cfg


def run_alembic() -> None:
    command.upgrade(alembic_config(), "head")


def _migration_files() -> list[Path]:
    path = Path(SchemaManager.get_migrations_path())
    return sorted(p for p in path.glob("*.sql"))


def apply_procrastinate_schema(conninfo: str) -> list[str]:
    """Returns the names of the procrastinate migrations applied in this run."""
    applied: list[str] = []
    with psycopg.connect(conninfo, autocommit=False) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATE_LOCK_ID,))
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {TRACKING_TABLE} ("
            "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        row = conn.execute("SELECT to_regclass('procrastinate_jobs') IS NOT NULL").fetchone()
        installed = bool(row and row[0])
        files = _migration_files()

        if not installed:
            log.info("installing procrastinate schema")
            conn.execute(SchemaManager.get_schema())
            for f in files:
                conn.execute(
                    f"INSERT INTO {TRACKING_TABLE} (name) VALUES (%s) ON CONFLICT DO NOTHING",  # noqa: S608
                    (f.name,),
                )
        else:
            done = {r[0] for r in conn.execute(f"SELECT name FROM {TRACKING_TABLE}")}  # noqa: S608
            if not done:
                raise RuntimeError(
                    "procrastinate schema exists but no migrations are recorded in "
                    f"{TRACKING_TABLE}; refusing to guess which migrations to apply"
                )
            for f in files:
                if f.name in done:
                    continue
                log.info("applying procrastinate migration %s", f.name)
                conn.execute(f.read_text())
                conn.execute(f"INSERT INTO {TRACKING_TABLE} (name) VALUES (%s)", (f.name,))  # noqa: S608
                applied.append(f.name)
        conn.commit()
    return applied


def migrate() -> None:
    settings = get_settings()
    log.info("running database migrations")
    run_alembic()
    apply_procrastinate_schema(settings.libpq_url)
    log.info("database is up to date")
