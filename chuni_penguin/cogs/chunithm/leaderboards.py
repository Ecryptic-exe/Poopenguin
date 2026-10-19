"""Chart leaderboards with original account selection and presentation."""

from typing import TYPE_CHECKING, Annotated, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from chuni_penguin import flags
from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.errors import ChartNotFound, SongNotFound
from chuni_penguin.adapters.kamaitachi import KamaitachiAdapter
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    AliasNameConverter,
    AliasNameTransformer,
    DifficultyConverter,
    RankingDifficultyConverter,
)
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.services.teams import parse_month
from chuni_penguin.types import Difficulty
from chuni_penguin.types.ranking import RankingType
from chuni_penguin.ui import LeaderboardView
from chuni_penguin.ui.autocomplete import song_title_choices
from chuni_penguin.ui.ranking import (
    CurrencyRankingView,
    RatingRankingView,
    ScoreRankingView,
)
from chuni_penguin.ui.teams import TeamDetailsView

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class LeaderboardsCog(commands.Cog, name="Leaderboards"):
    def __init__(self, bot: "ChuniBot"):
        self.accounts = bot.services.accounts
        self.autocomplete = bot.services.autocomplete

    async def song_title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return song_title_choices(self.autocomplete, current, interaction.guild_id)

    @flags.command("leaderboard", aliases=["lb"])
    @flags.argument("-c", "--chunithm-net", action="store_true")
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument("difficulty", type=DifficultyConverter)
    @flags.argument("query", nargs="+")
    @logged_prefix_command
    async def leaderboard(
        self,
        ctx: PenguinContext,
        *,
        difficulty: Difficulty,
        query: list[str],
        chunithm_net: bool = False,
        kamaitachi: bool = False,
    ):
        """View the leaderboard for a specific song and difficulty.

        **Parameters**:
        `difificulty`: Chart difficulty to view the leaderboard for (BAS/ADV/EXP/MAS/ULT).
        `query`: Song title to search for. You don't have to be exact; try things out!
        `-c`, `--chunithm-net`: View the CHUNITHM-NET International leaderboard for the song.
        `-k`, `--kamaitachi`: View the Kamaitachi leaderboard for the song.
        """

        query_str = await AliasNameConverter(lower=True).convert(ctx, " ".join(query))

        if chunithm_net and kamaitachi:
            msg = "You can only select either CHUNITHM-NET International or Kamaitachi."
            raise commands.BadArgument(msg)

        if (
            not chunithm_net and not kamaitachi
        ):  # determine the network based on the user
            try:
                async with self.accounts.network(ctx) as client:
                    kamaitachi = isinstance(client, KamaitachiAdapter)
            except commands.CommandError:
                kamaitachi = False

        async with (
            ctx.typing(),
            self.accounts.bot_network(kamaitachi=kamaitachi) as client,
        ):
            chart = await ctx.find_chart(
                difficulty, query_str, "Select a chart to see leaderboard for:"
            )

            if chart is None:
                return

            if isinstance(client, ChunithmNetAdapter):
                chart.song.raise_if_not_available()

            try:
                leaderboard = await client.get_chart_leaderboard(
                    chart.song.id, difficulty
                )
            except (SongNotFound, ChartNotFound):
                msg = f"The song **{escape_markdown(chart.song.title)}** is not available on {client.NAME}."
                raise commands.CommandError(msg) from None

        view = LeaderboardView(
            ctx,
            leaderboard,
            chart.song,
            difficulty,
            chart,
            synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
            network=client.NAME,
        )

        if ctx.response is not None:
            await view.start_from(ctx.response, content="")
        else:
            await view.start()

    @app_commands.command(
        name="leaderboard", description="View the leaderboard for the given chart."
    )
    @app_commands.rename(chunithm_net="chunithm-net")
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name=str(x), value=x.value)
            for x in Difficulty.__members__.values()
        ],  # type: ignore[reportGeneralTypeIssues]
    )
    @app_commands.describe(
        difficulty="Chart difficulty to view the leaderboard for.",
        query="Song title to search for. You don't have to be exact; try things out!",
        kamaitachi="View the Kamaitachi leaderboard for the song.",
        chunithm_net="View the CHUNITHM-NET International leaderboard for the song.",
    )
    @app_commands.autocomplete(query=song_title_autocomplete)
    @logged_app_command
    async def leaderboard_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        difficulty: Difficulty,
        query: app_commands.Transform[str, AliasNameTransformer(lower=True)],
        *,
        kamaitachi: bool = False,
        chunithm_net: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)

        await self.leaderboard(
            ctx,
            difficulty=difficulty,
            query=query.split(" "),
            kamaitachi=kamaitachi,
            chunithm_net=chunithm_net,
        )

    @commands.group("ranking", invoke_without_command=True)
    @logged_prefix_command
    async def ranking_prefix(self, ctx: PenguinContext):
        """
        View rankings for teams, rating, scores and currency.
        """

        await ctx.send_help(ctx.command)

    @ranking_prefix.command("team")
    @logged_prefix_command
    async def ranking_team_prefix(self, ctx: PenguinContext, month: str | None = None):
        """
        View team rankings.

        Only available for CHUNITHM International.

        **Parameters**
        `month`: The month to view team rankings for (e.g. 2026/04). If omitted, default to most recent team ranking.
        """

        await self._ranking_team_impl(ctx, month)

    @ranking_prefix.command("rating", cls=flags.FlagCommand)
    @flags.argument("-c", "--chunithm-net", action="store_true")
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument("type", nargs="?", default="global", choices=["friend", "global"])
    @logged_prefix_command
    async def ranking_rating_prefix(
        self,
        ctx: PenguinContext,
        *,
        chunithm_net: bool = False,
        kamaitachi: bool = False,
        type: Literal["friend", "global"] = "global",
    ):
        """
        View player rankings on rating.

        **Paramters**
        `-c`: View CHUNITHM International rankings.
        `-k`: View Kamaitachi rankings.
        `type`: The ranking type to view. If not specified, show global rankings. Friend rankings are only supported for CHUNITHM International.
        """

        await self._ranking_rating_impl(
            ctx, type, chunithm_net=chunithm_net, kamaitachi=kamaitachi
        )

    @ranking_prefix.command("score")
    @logged_prefix_command
    async def ranking_score_prefix(
        self,
        ctx: PenguinContext,
        difficulty: Annotated[Difficulty | None, RankingDifficultyConverter] = None,
        type: Literal["friend", "global"] = "global",
    ):
        """
        View player rankings on total highscore.

        Only available for CHUNITHM International.

        **Paramters**:
        `difficulty`: The difficulty to view. If not specified or `all`, show total highscore across all difficulties.
        `type`: The ranking type to view. If not specified, show global rankings. Friend rankings are only supported for CHUNITHM International.
        """

        await self._ranking_score_impl(ctx, type, difficulty)

    @ranking_prefix.command("currency", aliases=["memory", "point"])
    @logged_prefix_command
    async def ranking_currency_prefix(
        self, ctx: PenguinContext, type: Literal["friend", "global"] = "global"
    ):
        """
        View player rankings on total currency obtained.

        Only available for CHUNITHM International.

        **Paramters**:
        `type`: The ranking type to view. If not specified, show global rankings. Friend rankings are only supported for CHUNITHM International.
        """

        await self._ranking_currency_impl(ctx, type)

    ranking_slash = app_commands.Group(
        name="ranking",
        description="View rankings for teams, rating, scores and currency.",
    )

    @ranking_slash.command(name="team", description="View team rankings.")
    @app_commands.describe(month="The month to view team rankings for (e.g. 2026/04).")
    @logged_app_command
    async def ranking_team_slash(
        self, interaction: discord.Interaction["ChuniBot"], month: str | None = None
    ):
        await self._ranking_team_impl(
            await PenguinContext.from_interaction(interaction), month
        )

    @ranking_slash.command(name="rating", description="View player rankings on rating.")
    @app_commands.rename(chunithm_net="chunithm-net")
    @app_commands.describe(
        type="The ranking type to view. If not specified, show global rankings.",
        chunithm_net="View CHUNITHM International rankings.",
        kamaitachi="View Kamaitachi rankings.",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Global", value="global"),
            app_commands.Choice(name="Friend", value="friend"),
        ]
    )
    @logged_app_command
    async def ranking_rating_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        type: Literal["friend", "global"] = "global",
        *,
        chunithm_net: bool = False,
        kamaitachi: bool = False,
    ):
        await self._ranking_rating_impl(
            await PenguinContext.from_interaction(interaction),
            type,
            chunithm_net=chunithm_net,
            kamaitachi=kamaitachi,
        )

    @ranking_slash.command(
        name="score", description="View player rankings on total highscore."
    )
    @app_commands.describe(
        difficulty="The difficulty to view. If not specified, show total highscore across all difficulties.",
        type="The ranking type to view. If not specified, show global rankings.",
    )
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name=str(x), value=x.value)
            for x in Difficulty.__members__.values()
        ],  # type: ignore[reportGeneralTypeIssues]
        type=[
            app_commands.Choice(name="Global", value="global"),
            app_commands.Choice(name="Friend", value="friend"),
        ],
    )
    @logged_app_command
    async def ranking_score_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        difficulty: Difficulty | None = None,
        type: Literal["friend", "global"] = "global",
    ):
        await self._ranking_score_impl(
            await PenguinContext.from_interaction(interaction), type, difficulty
        )

    @ranking_slash.command(
        name="currency", description="View player rankings on total currency obtained."
    )
    @app_commands.describe(
        type="The ranking type to view. If not specified, show global rankings.",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Global", value="global"),
            app_commands.Choice(name="Friend", value="friend"),
        ]
    )
    @logged_app_command
    async def ranking_currency_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        type: Literal["friend", "global"] = "global",
    ):
        await self._ranking_currency_impl(
            await PenguinContext.from_interaction(interaction), type
        )

    async def _ranking_team_impl(self, ctx: PenguinContext, month: str | None = None):
        try:
            selected_month = parse_month(month)
        except ValueError as error:
            raise commands.BadArgument(str(error)) from None
        async with ctx.typing():
            await TeamDetailsView(ctx, month=selected_month).start()

    async def _ranking_rating_impl(
        self,
        ctx: PenguinContext,
        type: Literal["friend", "global"],
        *,
        chunithm_net: bool,
        kamaitachi: bool,
    ):
        if chunithm_net and kamaitachi:
            msg = "You can only select either CHUNITHM-NET International or Kamaitachi."
            raise commands.BadArgument(msg)

        ranking_type = RankingType(type)

        # if asking for friend ranking, directly grab user's network
        if ranking_type == RankingType.friend:
            async with (
                ctx.typing(),
                self.accounts.network(
                    ctx, kamaitachi=kamaitachi, chunithm_net=chunithm_net
                ) as client,
            ):
                try:
                    ranking = await client.get_rating_ranking(RankingType(type))
                except NotImplementedError:
                    msg = f"{client.NAME} does not support {type} rankings."
                    raise commands.CommandError(msg) from None
        else:
            if (
                not chunithm_net and not kamaitachi
            ):  # determine the network based on the user
                try:
                    async with self.accounts.network(ctx) as client:
                        kamaitachi = isinstance(client, KamaitachiAdapter)
                except commands.CommandError:
                    kamaitachi = False

            async with (
                ctx.typing(),
                self.accounts.bot_network(kamaitachi=kamaitachi) as client,
            ):
                try:
                    ranking = await client.get_rating_ranking(RankingType(type))
                except NotImplementedError:
                    msg = f"{client.NAME} does not support {type} rankings."
                    raise commands.CommandError(msg) from None

        view = RatingRankingView(
            ctx,
            client.NAME,
            discord.Color(client.ACCENT_COLOR),
            RankingType(type),
            ranking,
        )
        await view.start()

    async def _ranking_score_impl(
        self,
        ctx: PenguinContext,
        type: Literal["friend", "global"],
        difficulty: Difficulty | None,
    ):
        ranking_type = RankingType(type)

        async with (
            ctx.typing(),
            (
                self.accounts.network(ctx, ctx.author.id, chunithm_net=True)
                if ranking_type == RankingType.friend
                else self.accounts.bot_network(kamaitachi=False)
            ) as client,
        ):
            try:
                ranking = await client.get_score_ranking(RankingType(type), difficulty)
            except NotImplementedError:
                msg = f"{client.NAME} does not support {type} score rankings on {difficulty}."
                raise commands.CommandError(msg) from None

        view = ScoreRankingView(
            ctx,
            client.NAME,
            discord.Color(client.ACCENT_COLOR),
            RankingType(type),
            difficulty,
            ranking,
        )
        await view.start()

    async def _ranking_currency_impl(
        self, ctx: PenguinContext, type: Literal["friend", "global"]
    ):
        ranking_type = RankingType(type)

        async with (
            ctx.typing(),
            (
                self.accounts.network(ctx, ctx.author.id, chunithm_net=True)
                if ranking_type == RankingType.friend
                else self.accounts.bot_network(kamaitachi=False)
            ) as client,
        ):
            try:
                ranking = await client.get_currency_ranking(RankingType(type))
            except NotImplementedError:
                msg = f"{client.NAME} does not support {type} currency rankings."
                raise commands.CommandError(msg) from None

        view = CurrencyRankingView(
            ctx,
            client.NAME,
            discord.Color(client.ACCENT_COLOR),
            RankingType(type),
            ranking,
        )
        await view.start()


async def setup(bot: "ChuniBot"):
    await bot.add_cog(LeaderboardsCog(bot))
