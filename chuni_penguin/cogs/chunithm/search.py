from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    AliasNameConverter,
    AliasNameTransformer,
    Level,
    LevelRange,
    LevelRangeConverter,
)
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.repositories.chart_search import ChartSearchFilter
from chuni_penguin.ui.autocomplete import song_title_choices
from chuni_penguin.utils import shlex_split

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot
    from chuni_penguin.services.catalog import UtilityServices


class SearchCog(commands.Cog, name="Search"):
    def __init__(self, bot: "ChuniBot") -> None:
        self.bot = bot
        self.utils: "UtilityServices" = bot.utils
        self.autocomplete = bot.services.autocomplete

    async def _song_title_autocomplete(
        self,
        interaction: "discord.Interaction[ChuniBot]",
        current: str,
    ):
        return song_title_choices(self.autocomplete, current, interaction.guild_id)

    @commands.hybrid_command("find")
    @logged_prefix_command
    async def find(
        self,
        ctx: Context,
        level: Annotated[Level | LevelRange, LevelRangeConverter],
        difficulty: str | None = None,
        version: str | None = None,
        tag: str | None = None,
    ):
        """Find charts by level or chart constant.

        Parameters
        ----------
        level: Level | LevelRange
            Level (13+), chart constant (13.5), or level range (13.2-13.7) to search for.
        """

        if isinstance(level, LevelRange):
            filters = ChartSearchFilter(
                min_const=(level.min_level.const or level.min_level.inferred_const)
                if level.min_level is not None
                else None,
                max_const=(level.max_level.const or level.max_level.inferred_max_const)
                if level.max_level is not None
                else None,
            )
        elif level.const is not None:
            filters = ChartSearchFilter(const=level.const)
        else:
            filters = ChartSearchFilter(level=level.level)

        async with ctx.typing():
            charts = await self.utils.chart_search.charts(filters)
            if difficulty is not None:
                from chuni_penguin.converters import DifficultyConverter

                selected = (
                    await DifficultyConverter().convert(ctx, difficulty)
                ).short()
                charts = [chart for chart in charts if chart.difficulty == selected]
            if version is not None:
                charts = [
                    chart
                    for chart in charts
                    if version.casefold()
                    in (chart.version or chart.song.version).casefold()
                ]
            if tag is not None:
                extras = getattr(ctx.bot, "phase1_charts", None)
                if extras is None:
                    await ctx.reply(
                        "社群標籤查詢目前無法使用。",
                        mention_author=False,
                    )
                    return
                chart_ids = await extras.tagged_chart_ids(tag)
                charts = [chart for chart in charts if chart.id in chart_ids]

            from chuni_penguin.ui.chart_results import ChartResultsView
            from chuni_penguin.ui.panel import session_for
            from chuni_penguin.utils.chunithm_i18n import ui_text

            values = dict(
                level=str(level), difficulty=difficulty, version=version, tag=tag
            )
            session_for(ctx).parameters["find"] = {
                key: str(value) for key, value in values.items() if value is not None
            }
            query_label = " · ".join(
                f"{ui_text(ctx, name)}: {value}"
                for name, value in (
                    ("等級／定數", str(level)),
                    ("難度", difficulty),
                    ("版本", version),
                    ("標籤", tag),
                )
                if value
            )
            await ChartResultsView(
                ctx, charts, title=ui_text(ctx, "篩選譜面"), query_label=query_label
            ).start()

    @app_commands.command(
        name="chart",
        description="Search for a song and browse its information in tabs.",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(
        query="Song title to search for. You don't have to be exact; try things out!",
        detailed="Open the chart details tab first (note counts and designer name)",
    )
    @app_commands.autocomplete(query=_song_title_autocomplete)
    @logged_app_command
    async def info_slash(
        self,
        interaction: "discord.Interaction[ChuniBot]",
        query: app_commands.Transform[str, AliasNameTransformer(lower=True)],
        *,
        detailed: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)
        return await self._info_inner(ctx, query=query, detailed=detailed)

    @commands.command("chart", aliases=["info"], usage="[-d] <query>")
    @logged_prefix_command
    async def info(
        self,
        ctx: PenguinContext,
        *,
        query: Annotated[str, AliasNameConverter(lower=True)] = "",
    ):
        """Search for a song and browse its information in tabs.

        **Parameters:**
        `query`: Song title to search for. You don't have to be exact; try things out!
        `-d`: Open the chart details tab first, with note counts and charter.
        """
        args = SimpleNamespace(detailed=False, query=[])

        argv = shlex_split(query)
        for arg in argv:
            if arg in ["-d", "--detailed"]:
                args.detailed = True
            else:
                args.query.append(arg)

        query = " ".join(args.query)
        return await self._info_inner(ctx, query=query, detailed=args.detailed)

    async def _info_inner(
        self, ctx: PenguinContext, *, query: str, detailed: bool = False
    ):
        from chuni_penguin.ui.entrypoints import show_song_entry

        return await show_song_entry(
            ctx, query, section="chart", tab="chart" if detailed else "view"
        )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(SearchCog(bot))
