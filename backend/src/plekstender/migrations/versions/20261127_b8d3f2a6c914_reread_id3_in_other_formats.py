"""re-read ID3 tags in WAV, AIFF and DSD files

Their ID3 tags used to be read as raw frames, so they looked missing. Marked as read by the
reader before this one, so the next scan reads them again (only these files).

Revision ID: b8d3f2a6c914
Revises: a7c2e9f4b618
Create Date: 2026-11-27 19:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b8d3f2a6c914"
down_revision: str | None = "a7c2e9f4b618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORMATS = "('wav', 'wave', 'aif', 'aiff', 'aifc', 'dsf', 'dff')"


def upgrade() -> None:
    op.execute(
        f"UPDATE media_files SET reader = 5 WHERE reader > 5 AND lower(format) IN {FORMATS}"  # noqa: S608
    )


def downgrade() -> None:
    pass  # nothing to undo: those files are simply read again
