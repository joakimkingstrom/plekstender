"""musicbrainz covers without a guessed original artist

MusicBrainz says whether a recording is a cover, but not which of the song's recordings is
the original: the artist named so far was the first plain recording MusicBrainz listed, often
the wrong one ("Blue Christmas", a cover of Kelly Clarkson). MusicBrainz covers keep the
song's title only; their link goes to the cover's own recording page instead of the guessed
original's.

Revision ID: 4c8e1a7d3b52
Revises: 9b4f2c7e1d38
Create Date: 2026-11-29 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4c8e1a7d3b52"
down_revision: str | None = "9b4f2c7e1d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE song_covers
        SET original_artist = NULL, original_url = performance_url
        WHERE source = 'musicbrainz' AND status = 'cover'
        """
    )


def downgrade() -> None:
    pass  # the guesses aren't worth bringing back
