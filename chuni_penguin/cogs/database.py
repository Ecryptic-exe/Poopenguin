"""Compatibility extension; database ownership lives in application services."""

from typing import TYPE_CHECKING

from discord.ext import commands

from chuni_penguin.services.database import (
    ChartQueries,
    CookieQueries,
    PendingKamaitachiImportQueries,
    PersonalBestQueries,
    SongQueries,
    WriterQueue,
)

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot

__all__ = (
    "ChartQueries",
    "CookieQueries",
    "DatabaseCog",
    "PendingKamaitachiImportQueries",
    "PersonalBestQueries",
    "SongQueries",
    "WriterQueue",
)


class DatabaseCog(commands.Cog, name="Database"):
    def __init__(self, bot: "ChuniBot"):
        self.service = bot.services.database


async def setup(bot: "ChuniBot"):
    await bot.add_cog(DatabaseCog(bot))
