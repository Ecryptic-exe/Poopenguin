"""Compatibility extension; account sessions belong to shared application services."""

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class NetworksCog(commands.Cog, command_attrs={"hidden": True}):
    def __init__(self, bot: "ChuniBot"):
        self.service = bot.services.accounts


async def setup(bot: "ChuniBot"):
    await bot.add_cog(NetworksCog(bot))
