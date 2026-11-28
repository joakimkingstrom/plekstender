"""What a smart playlist contains (stored as JSON on the playlist)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator


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


class NewAlbums(BaseModel):
    """The best songs of albums added to the library lately ("Fresh picks")."""

    days: int = Field(default=30, ge=1, le=365, description="Albums added in the last N days.")
    per_album: int = Field(default=3, ge=1, le=20, description="Each album's N most popular songs.")


TitleWord = Annotated[str, StringConstraints(min_length=2, max_length=40, pattern=r"^[^\s%_\\]+$")]


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
    new_albums: NewAlbums | None = Field(
        default=None,
        description="Also the best songs of albums added lately (picked again at each "
        "refresh; the rules still apply).",
    )
    title_words: list[TitleWord] = Field(
        default_factory=list,
        max_length=10,
        description="Also every song with one of these words in its title (whole words, any "
        "case, bracketed parts left out, as the word cloud counts them; found again at each "
        "refresh, and the rules still apply).",
    )
    rules: Rules = Field(default_factory=Rules)
    limit: int | None = Field(default=500, ge=1, le=5000)
    # random | most popular first | as listed in `tracks` (gig playlists: setlist order).
    order: Literal["random", "popular", "listed"] = "random"

    @model_validator(mode="after")
    def _something(self) -> Definition:
        if not (
            self.artists or self.tracks or self.forgotten or self.new_albums or self.title_words
        ):
            raise ValueError(
                "choose at least one artist or song, a word, your forgotten favourites or new "
                "albums"
            )
        return self
