"""Global Discord event hooks; shared presentation survives this Cog's unload."""

from typing import TYPE_CHECKING

import discord
from discord.app_commands import AppCommandError
from discord.ext import commands

from chuni_penguin.context import PenguinContext

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.errors = bot.services.errors

    async def cog_load(self):
        self._old_tree_error = self.bot.tree.on_error
        self.bot.tree.on_error = self.tree_on_error

    async def cog_unload(self):
        if self.bot.tree.on_error == self.tree_on_error:
            self.bot.tree.on_error = self._old_tree_error

    async def tree_on_error(
        self, interaction: discord.Interaction["ChuniBot"], error: AppCommandError
    ):
        await self.errors.tree_on_error(interaction, error)

    @commands.Cog.listener()
    async def on_command_error(
        self, ctx: PenguinContext, error: commands.errors.CommandInvokeError
    ):
        await self.errors.on_command_error(ctx, error)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(EventsCog(bot))
