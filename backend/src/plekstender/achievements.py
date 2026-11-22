"""Achievements: small, silly rewards for milestones, each with a name from music lore.

Each one is a threshold on one of the person's own numbers (library ones count what their
Plex account can see). They're worked out when asked for (the app asks every few minutes),
unlocked once, and kept with the date; the app celebrates the ones not seen yet.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import extract, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from plekstender.db.instance import _get, _put
from plekstender.db.models import (
    GameRound,
    Gig,
    Listen,
    ListenBrainzLink,
    ScrobbleTarget,
    UserAchievement,
    UserItemState,
)
from plekstender.history import stats

SETTING_KEY = "achievements"


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    group: str  # library | rating | game | gigs | connections | listening
    metric: str
    goal: int
    message: str
    hint: str  # what it's for, shown with it


A = Achievement
CATALOGUE: tuple[Achievement, ...] = (
    # The library (what you can see).
    A("roadie", "Roadie", "library", "artists", 100,
      "You carry the gear now. Lift with your knees.", "100 artists in your library"),
    A("session-musician", "Session Musician", "library", "artists", 250,
      "Available for weddings, bar mitzvahs and your library.", "250 artists in your library"),
    A("headliner", "Headliner", "library", "artists", 500,
      "Your name goes at the top of the poster now.", "500 artists in your library"),
    A("golden-god", "Golden God", "library", "artists", 1000,
      "Shout it from a rooftop in Topeka.", "1,000 artists in your library"),
    A("hall-of-fame", "Hall of Fame", "library", "artists", 2500,
      "Induction speech: keep it under forty minutes.", "2,500 artists in your library"),
    A("deep-cut", "Deep Cut", "library", "tracks", 10_000,
      "Ten thousand songs. Some of them you've even heard.", "10,000 songs in your library"),
    A("b-side", "B-Side", "library", "tracks", 25_000,
      "Flip it over. There's more.", "25,000 songs in your library"),
    A("crate-digger", "Crate Digger", "library", "tracks", 50_000,
      "Your fingertips smell faintly of cardboard sleeves.", "50,000 songs in your library"),
    A("music-hoarder", "Music Hoarder", "library", "tracks", 100_000,
      "We hope you're not hoarding other things at home as much as you hoard music.",
      "100,000 songs in your library"),
    A("platinum-record", "Platinum Record", "library", "tracks", 250_000,
      "Frame it. Hang it in the downstairs loo, like the pros.", "250,000 songs in your library"),
    A("these-go-to-eleven", "These Go to Eleven", "library", "tracks", 1_000_000,
      "Your library has been referred to a support group. They're very understanding. "
      "Please call them.", "1,000,000 songs in your library"),
    # Rating.
    A("one-hit-wonder", "One-Hit Wonder", "rating", "ratings", 1,
      "Your first rating. A star is born (or half of one).", "Your first rating"),
    A("liner-notes-reader", "Liner Notes Reader", "rating", "rated_tracks", 1000,
      "You've read the small print. All of it.", "1,000 songs rated"),
    A("air-guitar", "Air Guitar", "rating", "rated_share", 10,
      "A tenth of your songs rated. Windmill optional.", "10 % of your songs rated"),
    A("stage-diver", "Stage Diver", "rating", "rated_share", 25,
      "A quarter rated. The crowd caught you. Mostly.", "25 % of your songs rated"),
    A("encore", "Encore", "rating", "rated_share", 50,
      "Half your songs rated. They're stamping their feet for more.", "Half your songs rated"),
    A("critics-choice", "Critic's Choice", "rating", "rated_share", 100,
      "Even the hidden track. Even the 12-minute one.", "Every song rated"),
    # Guess the intro.
    A("sound-check", "Sound Check", "game", "game_rounds", 1,
      "One, two. One, two. Is this thing on?", "Your first round of Guess the intro"),
    A("backstage-pass", "Backstage Pass", "game", "game_rounds", 100,
      "A hundred rounds. Security waves you through now.", "100 rounds of Guess the intro"),
    A("brown-mms", "Brown M&Ms", "game", "game_streak", 10,
      "Every detail of the rider, checked.", "Ten intros right in a row in Guess the intro"),
    # Gigs.
    A("front-row", "Front Row", "gigs", "gigs", 1,
      "Your ears are still ringing. Good.", "Your first gig"),
    A("groupie", "Groupie", "gigs", "gigs", 25,
      "Security knows you by name.", "25 gigs"),
    A("band-aid", "Band-Aid", "gigs", "gigs", 50,
      "Not a groupie. You're here for the music, obviously.", "50 gigs"),
    A("tour-bus", "Tour Bus", "gigs", "venues", 10,
      "Ten venues. You know where every one keeps its toilets.", "Gigs at 10 different venues"),
    A("world-tour", "World Tour", "gigs", "countries", 3,
      "Three countries. Your passport has a merch stand.", "Gigs in 3 countries"),
    A("residency", "Residency", "gigs", "gigs_in_a_year", 12,
      "A gig a month for a year. Vegas has been in touch.", "12 gigs in one year"),
    # Connections.
    A("patch-bay", "Patch Bay", "connections", "scrobble_services", 1,
      "Plugged in. Every play counted.", "A scrobble service connected"),
    A("daisy-chain", "Daisy Chain", "connections", "scrobble_services", 3,
      "Three services, one cable. What could go wrong?", "3 scrobble services connected"),
    # Listening.
    A("heavy-rotation", "Heavy Rotation", "listening", "scrobbles", 10_000,
      "Ten thousand plays. The needle's worn a groove.", "10,000 scrobbles"),
    A("gold-record", "Gold Record", "listening", "scrobbles", 100_000,
      "A hundred thousand plays. Your neighbours have opinions.", "100,000 scrobbles"),
    A("on-tour", "On Tour", "listening", "streak", 30,
      "Thirty days in a row. No days off on this tour.", "Listening 30 days in a row"),
    A("deep-diver", "Deep Diver", "listening", "artists_heard", 1000,
      "A thousand artists heard. Somewhere, a record shop clerk weeps with pride.",
      "1,000 different artists heard"),
)  # fmt: skip


async def enabled_for_everyone(db: AsyncSession) -> bool:
    return bool((await _get(db, SETTING_KEY) or {}).get("enabled", True))


async def set_enabled_for_everyone(db: AsyncSession, enabled: bool) -> None:
    await _put(db, SETTING_KEY, {"enabled": enabled})


async def metrics(db: AsyncSession, user_id: int, tz: ZoneInfo | None = None) -> dict[str, int]:
    """The numbers achievements are about, for one person."""
    out: dict[str, int] = {}
    rows = await db.execute(
        select(UserItemState.item_type, func.count(), func.count(UserItemState.user_rating))
        .where(UserItemState.user_id == user_id)
        .group_by(UserItemState.item_type)
    )
    ratings = 0
    for kind, count, rated in rows.all():
        out[f"{kind}s"] = count
        ratings += rated
        if kind == "track":
            out["rated_tracks"] = rated
    out["ratings"] = ratings
    tracks = out.get("tracks", 0)
    rated_tracks = out.get("rated_tracks", 0)
    # Whole per cent, rounded down; 100 only when every song is rated.
    out["rated_share"] = (
        100
        if tracks and rated_tracks >= tracks
        else (100 * rated_tracks // tracks if tracks else 0)
    )

    game = (
        await db.execute(
            select(func.count(), func.coalesce(func.max(GameRound.streak), 0)).where(
                GameRound.user_id == user_id
            )
        )
    ).one()
    out["game_rounds"], out["game_streak"] = int(game[0]), int(game[1])

    gigs = (
        await db.execute(
            select(
                func.count(),
                func.count(func.distinct(func.lower(Gig.venue))),
                func.count(func.distinct(Gig.country)),
            ).where(Gig.user_id == user_id)
        )
    ).one()
    out["gigs"], out["venues"], out["countries"] = int(gigs[0]), int(gigs[1]), int(gigs[2])
    year = extract("year", Gig.date)
    out["gigs_in_a_year"] = int(
        await db.scalar(
            select(func.count())
            .where(Gig.user_id == user_id)
            .group_by(year)
            .order_by(func.count().desc())
            .limit(1)
        )
        or 0
    )

    services = await db.scalar(select(func.count()).where(ScrobbleTarget.user_id == user_id)) or 0
    services += 1 if await db.get(ListenBrainzLink, user_id) else 0
    out["scrobble_services"] = services

    listens = (
        await db.execute(
            select(func.count(), func.count(func.distinct(Listen.artist_key))).where(
                Listen.user_id == user_id, Listen.duplicate_of.is_(None)
            )
        )
    ).one()
    out["scrobbles"], out["artists_heard"] = int(listens[0]), int(listens[1])
    zone = tz or ZoneInfo("UTC")
    days = await stats.listening_days(db, user_id, zone)
    out["streak"] = stats.streaks(days, dt.datetime.now(zone).date())[1] if days else 0
    return out


@dataclass
class Progress:
    achievement: Achievement
    value: int
    unlocked_at: dt.datetime | None
    seen: bool


async def evaluate(db: AsyncSession, user_id: int, tz: ZoneInfo | None = None) -> list[Progress]:
    """Every achievement with the person's progress; newly reached ones are unlocked (and
    stay unlocked even if a number later goes down)."""
    numbers = await metrics(db, user_id, tz)
    now = dt.datetime.now(dt.UTC)
    reached = [a.id for a in CATALOGUE if numbers.get(a.metric, 0) >= a.goal]
    if reached:
        await db.execute(
            insert(UserAchievement)
            .values([{"user_id": user_id, "achievement": i, "unlocked_at": now} for i in reached])
            .on_conflict_do_nothing(index_elements=["user_id", "achievement"])
        )
        await db.commit()
    unlocked = {
        a.achievement: a
        for a in await db.scalars(select(UserAchievement).where(UserAchievement.user_id == user_id))
    }
    return [
        Progress(
            a,
            numbers.get(a.metric, 0),
            unlocked[a.id].unlocked_at if a.id in unlocked else None,
            a.id in unlocked and unlocked[a.id].seen_at is not None,
        )
        for a in CATALOGUE
    ]


async def mark_seen(db: AsyncSession, user_id: int, ids: list[str]) -> None:
    rows = await db.scalars(
        select(UserAchievement).where(
            UserAchievement.user_id == user_id,
            UserAchievement.achievement.in_(ids),
            UserAchievement.seen_at.is_(None),
        )
    )
    now = dt.datetime.now(dt.UTC)
    for row in rows:
        row.seen_at = now
    await db.commit()
