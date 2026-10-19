# ruff: noqa: RUF001
"""Load only trustworthy, owner-local background fields for chart reviews."""

import asyncio
import json
from collections.abc import Mapping
from decimal import Decimal

from sqlalchemy import select

from chuni_penguin.database import PlayerSource
from chuni_penguin.services.chart_reviews import (
    EXPERIENCE_BUCKETS,
    PRIMARY_LEVELS,
    RATING_BUCKETS,
    SKILLS,
)
from chuni_penguin.types import RatingType

_UNAVAILABLE = "未提供"
_NATIVE_SOURCES = frozenset(("default", "net", "chunithm_net", "kamaitachi"))
_SNAPSHOT_SOURCES = frozenset(("chunirec:jp", "chunirec:jp2", "chunilib:export"))


def _saved_profile(saved):
    saved = saved if isinstance(saved, Mapping) else {}
    profile = {}
    for key, allowlist in (
        ("rating_bucket", RATING_BUCKETS),
        ("primary_level", PRIMARY_LEVELS),
        ("experience_bucket", EXPERIENCE_BUCKETS),
    ):
        value = saved.get(key)
        profile[key] = (
            value if isinstance(value, str) and value in allowlist else _UNAVAILABLE
        )

    for key in ("strengths", "weaknesses"):
        values = saved.get(key, ())
        if not isinstance(values, (list, tuple)):
            values = ()
        profile[key] = list(
            dict.fromkeys(
                value for value in values if isinstance(value, str) and value in SKILLS
            )
        )
    return profile


def _rating_bucket(value):
    """Return an allowlisted bucket for an actual numeric Rating, if valid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        rating = value
    elif type(value) in (int, float):
        rating = Decimal(str(value))
    else:
        return None

    if not rating.is_finite() or rating < 0 or rating >= 30:
        return None
    if rating == 0:
        return "未定級"
    if rating < 12:
        return "未滿 12"
    if rating < 14:
        return "12–13.99"
    if rating < 15:
        return "14–14.99"
    if rating < Decimal("15.5"):
        return "15–15.49"
    if rating < 16:
        return "15.5–15.99"
    if rating < Decimal("16.5"):
        return "16–16.49"
    if rating < 17:
        return "16.5–16.99"
    return "17 以上"


def _in_game_rating(profile):
    for system in getattr(profile, "rating_systems", ()) or ():
        if getattr(system, "type", None) == RatingType.in_game:
            bucket = _rating_bucket(getattr(system, "value", None))
            if bucket is not None:
                return bucket
    return None


async def _native_rating(ctx, source):
    accounts = ctx.bot.services.accounts
    async with asyncio.timeout(8):
        async with accounts.network(
            ctx,
            ctx.author.id,
            kamaitachi=source == "kamaitachi",
            chunithm_net=source in ("net", "chunithm_net"),
        ) as client:
            profile = await client.get_minimal_profile()
            rating = _in_game_rating(profile)
            if rating is not None:
                return rating
            profile = await client.get_profile()
            return _in_game_rating(profile)


async def _snapshot_rating(ctx, source):
    database = ctx.bot.services.database
    async with database.mapping_read_sessionmaker() as session:
        payload = await session.scalar(
            select(PlayerSource.payload).where(
                PlayerSource.discord_id == ctx.author.id,
                PlayerSource.source == source,
            )
        )
    if not isinstance(payload, str):
        return None
    try:
        snapshot = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, Mapping):
        return None
    return _rating_bucket(snapshot.get("rating"))


async def load_review_background(ctx, *, saved=None, source="default"):
    """Return an allowlisted owner profile, filling only actual source Rating.

    Native account reads are bounded to eight seconds and are always closed by the
    account context manager. Explicit source snapshots are read-only and query only
    the current owner's payload column. Other fields come solely from validated
    saved review background data.
    """
    profile = _saved_profile(saved)
    if not isinstance(source, str):
        return profile

    try:
        if source in _NATIVE_SOURCES:
            rating = await _native_rating(ctx, source)
        elif source in _SNAPSHOT_SOURCES:
            rating = await _snapshot_rating(ctx, source)
        else:
            rating = None
    except Exception:  # noqa: BLE001
        # Account and snapshot failures must not leak exception details into the UI.
        rating = None

    if rating is not None:
        profile["rating_bucket"] = rating
    return profile
