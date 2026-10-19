from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from chuni_penguin.ui.autocomplete import song_title_choices

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class AutocompletersCog(commands.Cog, name="Autocompleters"):
    def __init__(self, bot: "ChuniBot") -> None:
        self.service = bot.services.autocomplete

    async def song_title_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return song_title_choices(self.service, current, interaction.guild_id)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(AutocompletersCog(bot))
