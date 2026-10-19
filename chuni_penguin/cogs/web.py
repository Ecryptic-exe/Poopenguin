"""Independently managed lease for the Bot-owned HTTP application."""

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class WebCog(commands.Cog, name="Web"):
    def __init__(self, bot: "ChuniBot") -> None:
        self.bot = bot
        self.web = bot.services.web

    async def cog_load(self):
        await self.web.enable(self)

    async def cog_unload(self):
        await self.web.disable(self)

    @property
    def web_app(self):
        return self.web.app


async def setup(bot: "ChuniBot"):
    await bot.add_cog(WebCog(bot))
