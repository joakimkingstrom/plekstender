"""What a smart playlist contains (stored as JSON on the playlist)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Rules(BaseModel):
    """Which of the chosen artists' songs make it in."""

    op: Literal["AND", "OR"] = Field(default="AND", description="How the two rules combine.")
    min_rating: int | None = Field(
        default=None, ge=1, le=10, description="Your rating, 0-10 (half stars = 1)."
    )
    include_unrated: bool = Field(default=True, description="Unrated songs pass the rating rule.")
    top_percent: int | None = Field(
        default=None, ge=1, le=100, description="Only each artist's most popular N% of songs."
    )
    tags: list[int] = Field(
        default_factory=list,
        max_length=50,
        description="Only songs whose album or artist has one of these genres, styles or "
        "moods (always applies, whatever `op` says).",
    )


class Definition(BaseModel):
    artists: list[int] = Field(default_factory=list, max_length=500)
    tracks: list[int] = Field(
        default_factory=list, max_length=2000, description="Always included, rules or not."
    )
    forgotten: bool = Field(
        default=False,
        description="Also your forgotten favourites: songs rated 4½ stars or more that you "
        "haven't played for a year (picked again at each refresh; the rules still apply).",
    )
    rules: Rules = Field(default_factory=Rules)
    limit: int | None = Field(default=500, ge=1, le=5000)
    order: Literal["random", "popular"] = "random"

    @model_validator(mode="after")
    def _something(self) -> Definition:
        if not self.artists and not self.tracks and not self.forgotten:
            raise ValueError("choose at least one artist or song, or your forgotten favourites")
        return self
