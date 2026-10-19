"""Recent scores with the original detailed-score View and explicit account handoff."""

from typing import TYPE_CHECKING

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from chuni_penguin import flags
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import MemberOrUserConverter
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.ui import RecentRecordsView

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class RecentScoresCog(commands.Cog, name="RecentScores"):
    def __init__(self, bot: "ChuniBot"):
        self.accounts = bot.services.accounts

    async def _recent_inner(
        self,
        ctx: PenguinContext,
        user: discord.User | discord.Member | None = None,
        *,
        kamaitachi: bool = False,
    ):
        target_id = ctx.author.id if user is None else user.id
        client_manager = self.accounts.network(ctx, target_id, kamaitachi=kamaitachi)
        client = await client_manager.__aenter__()

        view: RecentRecordsView | None = None
        try:
            async with ctx.typing():
                profile = await client.get_minimal_profile()

                recents = await client.get_recent_scores()

            view = RecentRecordsView(
                ctx,
                target_id,
                recents,
                client,
                client_manager,
                profile,
                ctx.user_config.synthesis_alt_jacket,
            )
            await view.start(
                content=f"Most recent scores for {profile.username} on {client.NAME}:"
            )
        except BaseException:
            try:
                if view is None:
                    await client_manager.__aexit__(None, None, None)
                else:
                    await view.close_network()
            finally:
                if view is not None:
                    view.stop()
            raise

    @flags.command("recent", aliases=["rs"])
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument("user", nargs="?", default=None, type=MemberOrUserConverter)
    @logged_prefix_command
    async def recent(
        self,
        ctx: PenguinContext,
        *,
        kamaitachi: bool = False,
        user: discord.Member | discord.User | None = None,
    ):
        """View your recent scores.

        **Parameters**:
        `user`: The user to get scores for.
        `-k, --kamaitachi`: Get recent scores from Kamaitachi, if the user has that linked.
        """

        await self._recent_inner(ctx, user, kamaitachi=kamaitachi)

    @app_commands.command(name="recent", description="View recent scores")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(
        user="The user to get recent scores for",
        kamaitachi="Get recent scores from Kamaitachi, if linked",
    )
    @logged_app_command
    async def recent_slash(
        self,
        interaction: Interaction["ChuniBot"],
        user: discord.User | discord.Member | None = None,
        *,
        kamaitachi: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)

        return await self._recent_inner(ctx, user, kamaitachi=kamaitachi)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(RecentScoresCog(bot))
