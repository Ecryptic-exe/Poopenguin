"""Private, confirmation-based learning for guild song aliases."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chuni_penguin.database import Alias, Song
from chuni_penguin.services.catalog import AliasService

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


AliasLearningStatus = Literal[
    "available", "added", "already_exists", "conflict", "title_conflict"
]

_DISCORD_MENTION = re.compile(r"@(?:everyone|here)\b|<@(?:!|&)?\d+>", re.IGNORECASE)


def normalize_alias_display(alias_text: str) -> str:
    """Return a safe display form with Unicode compatibility and spacing normalized."""
    if not isinstance(alias_text, str):
        msg = "Alias must be text."
        raise TypeError(msg)

    normalized = unicodedata.normalize("NFKC", alias_text)
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        msg = "Alias cannot contain control characters."
        raise ValueError(msg)

    display = " ".join(normalized.split())
    if not display:
        msg = "Alias cannot be empty."
        raise ValueError(msg)
    if len(display) > 100:
        msg = "Alias cannot exceed 100 characters."
        raise ValueError(msg)
    if _DISCORD_MENTION.search(display):
        msg = "Alias cannot contain a Discord mention."
        raise ValueError(msg)

    return display


def normalize_alias(alias_text: str) -> str:
    """Return the NFKC, whitespace-collapsed, case-insensitive alias key."""
    return normalize_alias_display(alias_text).casefold()


def _normalization_key(text: str) -> str:
    """Normalize persisted names without applying the user-input restrictions."""
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split()).casefold()


@dataclass(frozen=True, slots=True)
class AliasLearningConflict:
    rowid: int | None
    song_id: int
    title: str
    guild_id: int


@dataclass(frozen=True, slots=True)
class AliasLearningResult:
    status: AliasLearningStatus
    alias: str
    conflicts: list[AliasLearningConflict]
    song_id: int
    title: str
    guild_id: int


class AliasLearningService:
    """Inspect and confirm private aliases scoped to one Discord guild."""

    def __init__(self, database: DatabaseService, aliases: AliasService) -> None:
        self.database = database
        self.aliases = aliases

    async def inspect(
        self, alias_text: str, *, guild_id: int, song_id: int
    ) -> AliasLearningResult:
        display = normalize_alias_display(alias_text)
        _validate_positive_id(guild_id, "Guild ID")
        _validate_positive_id(song_id, "Song ID")

        async with self.database.read_sessionmaker() as session:
            return await _inspect_in_session(
                session,
                alias=display,
                alias_key=display.casefold(),
                guild_id=guild_id,
                song_id=song_id,
            )

    async def confirm(
        self,
        alias_text: str,
        *,
        guild_id: int,
        song_id: int,
        owner_id: int,
    ) -> AliasLearningResult:
        display = normalize_alias_display(alias_text)
        _validate_positive_id(guild_id, "Guild ID")
        _validate_positive_id(song_id, "Song ID")
        _validate_positive_id(owner_id, "Owner ID")
        alias_key = display.casefold()

        async def transaction(session: AsyncSession) -> AliasLearningResult:
            result = await _inspect_in_session(
                session,
                alias=display,
                alias_key=alias_key,
                guild_id=guild_id,
                song_id=song_id,
            )
            if result.status != "available":
                return result

            alias = Alias(
                alias=display,
                guild_id=guild_id,
                song_id=song_id,
                owner_id=owner_id,
            )
            session.add(alias)
            await session.flush()
            return AliasLearningResult(
                status="added",
                alias=display,
                conflicts=[],
                song_id=song_id,
                title=result.title,
                guild_id=guild_id,
            )

        result = await self.database.writer.execute_fn(transaction)
        if result.status == "added":
            await self.aliases.reload()
        return result


async def _inspect_in_session(
    session: AsyncSession,
    *,
    alias: str,
    alias_key: str,
    guild_id: int,
    song_id: int,
) -> AliasLearningResult:
    song = await session.get(Song, song_id)
    if song is None:
        msg = "Song does not exist."
        raise ValueError(msg)

    aliases_result = await session.execute(
        select(Alias.rowid, Alias.alias, Alias.guild_id, Alias.song_id, Song.title)
        .join(Song, Song.id == Alias.song_id)
        .where(Alias.guild_id.in_((0, guild_id)))
    )
    conflicts = [
        AliasLearningConflict(
            rowid=rowid,
            song_id=existing_song_id,
            title=title,
            guild_id=existing_guild_id,
        )
        for rowid, existing_alias, existing_guild_id, existing_song_id, title
        in aliases_result
        if _normalization_key(existing_alias) == alias_key
    ]

    titles_result = await session.execute(select(Song.id, Song.title))
    title_conflicts = [
        AliasLearningConflict(
            rowid=None,
            song_id=existing_song_id,
            title=title,
            guild_id=0,
        )
        for existing_song_id, title in titles_result
        if _normalization_key(title) == alias_key
    ]
    conflicts.extend(title_conflicts)
    conflicts.sort(
        key=lambda conflict: (
            conflict.guild_id,
            conflict.song_id,
            -1 if conflict.rowid is None else conflict.rowid,
        )
    )

    if title_conflicts:
        status: AliasLearningStatus = "title_conflict"
    elif any(conflict.song_id != song_id for conflict in conflicts):
        status = "conflict"
    elif conflicts:
        status = "already_exists"
    else:
        status = "available"

    return AliasLearningResult(
        status=status,
        alias=alias,
        conflicts=conflicts,
        song_id=song_id,
        title=song.title,
        guild_id=guild_id,
    )


def _validate_positive_id(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        msg = f"{label} must be a positive integer."
        raise ValueError(msg)
