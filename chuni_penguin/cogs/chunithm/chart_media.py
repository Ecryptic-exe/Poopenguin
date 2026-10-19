from typing import TYPE_CHECKING, Annotated

import discord
from discord import app_commands
from discord.ext import commands

from chuni_penguin import flags
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    AliasNameConverter,
    AliasNameTransformer,
    DifficultyConverter,
)
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.types import (
    Difficulty,
)
from chuni_penguin.ui.autocomplete import song_title_choices

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ChartMediaCog(commands.Cog, name="ChartMedia"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.autocomplete = bot.services.autocomplete
        self.http_client = bot.caching_http_client

    async def song_title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return song_title_choices(self.autocomplete, current, interaction.guild_id)

    @app_commands.command(
        name="chartview",
        description="Renders a chart view from sdvx.in for a given song and difficulty.",
    )
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name="BASIC", value="BASIC"),
            app_commands.Choice(name="ADVANCED", value="ADVANCED"),
            app_commands.Choice(name="EXPERT", value="EXPERT"),
            app_commands.Choice(name="MASTER", value="MASTER"),
            app_commands.Choice(name="ULTIMA", value="ULTIMA"),
            app_commands.Choice(name="WORLD'S END", value="WORLD'S END"),
        ]
    )
    @app_commands.autocomplete(query=song_title_autocomplete)
    @logged_app_command
    async def chart_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        difficulty: str,
        query: Annotated[str, AliasNameTransformer(lower=True)],
        *,
        mirror: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)
        converted_difficulty = await DifficultyConverter().convert(ctx, difficulty)

        await self._chart_impl(
            ctx, difficulty=converted_difficulty, query=query, mirror=mirror
        )

    @flags.command("chartview")
    @flags.argument("-m", "--mirror", action="store_true")
    @flags.argument("difficulty", type=DifficultyConverter)
    @flags.argument("query", nargs="+")
    @logged_prefix_command
    async def chart(
        self,
        ctx: PenguinContext,
        *,
        difficulty: Difficulty,
        query: list[str],
        mirror: bool = False,
    ):
        q = await AliasNameConverter(lower=True).convert(ctx, " ".join(query))

        await self._chart_impl(ctx, difficulty=difficulty, query=q, mirror=mirror)

    async def _chart_impl(
        self,
        ctx: PenguinContext,
        *,
        difficulty: Difficulty,
        query: str,
        mirror: bool = False,
    ):
        """Open the shared song panel on its chart-view page."""
        from chuni_penguin.ui.song_details import SongDetailsView

        async with ctx.typing():
            chart = await ctx.find_chart(difficulty, query, "選擇要查看的譜面：")
            if chart is None:
                return
            view = SongDetailsView(
                ctx,
                [chart.song],
                initial_tab="view",
                difficulty=chart.difficulty,
                mirror=mirror,
                synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
            )
            await view.start(ephemeral=ctx.interaction is not None)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(ChartMediaCog(bot))
