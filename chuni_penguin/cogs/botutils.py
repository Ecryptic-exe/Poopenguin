"""Compatibility extension; utilities are shared services, not Cog internals."""

from typing import TYPE_CHECKING

from discord.ext import commands

from chuni_penguin.services.catalog import CachedAlias, SongSearchResult

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot
__all__ = ("CachedAlias", "SongSearchResult", "UtilsCog")


class UtilsCog(commands.Cog, name="Utils"):
    def __init__(self, bot: "ChuniBot"):
        self.service = bot.services.utils


async def setup(bot: "ChuniBot"):
    await bot.add_cog(UtilsCog(bot))
