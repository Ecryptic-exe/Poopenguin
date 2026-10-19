from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

from chuni_penguin.context import PenguinContext
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.services.teams import parse_month
from chuni_penguin.ui.teams import TeamDetailsView

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class TeamsCog(commands.Cog, name="Teams"):
    @commands.hybrid_command(name="team", aliases=["teams"])
    @app_commands.describe(
        query="Team name to search for. Leave empty to browse the leaderboard.",
        month="Month to query (YYYY/MM); leave empty for the latest leaderboard.",
        mine="Look up your team using your linked CHUNITHM-NET account.",
    )
    @logged_prefix_command
    async def team(
        self,
        ctx: PenguinContext,
        query: str | None = None,
        month: str | None = None,
        *,
        mine: bool = False,
    ):
        """Browse team information, monthly rankings and history in one card.

        Quote multiword team names in text commands. `mine=True` uses your linked
        CHUNITHM-NET account. The leaderboard requires the bot's source account.
        """
        await self._team_inner(ctx, query=query, month=month, mine=mine)

    async def _team_inner(
        self,
        ctx: PenguinContext,
        *,
        query: str | None = None,
        month: str | None = None,
        mine: bool = False,
    ):
        if mine and query:
            msg = "Choose either a team name or your own team."
            raise commands.BadArgument(msg)
        if query and len(query) > 100:
            msg = "Team names must be at most 100 characters."
            raise commands.BadArgument(msg)
        try:
            selected_month = parse_month(month)
        except ValueError as error:
            raise commands.BadArgument(str(error)) from None
        async with ctx.typing():
            await TeamDetailsView(
                ctx, query=query or "", month=selected_month, mine=mine
            ).start()


async def setup(bot: "ChuniBot"):
    await bot.add_cog(TeamsCog())
