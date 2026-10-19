"""User-invoked score reads into the existing local cache."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from chuni_penguin import flags
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import MemberOrUserConverter
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.ui import FriendCodeOfferView

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ScoreSyncCog(commands.Cog, name="ScoreSync"):
    def __init__(self, bot: "ChuniBot"):
        self.accounts = bot.services.accounts
        self.database = bot.services.database

    @flags.command("sync", aliases=["refresh", "update"])
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument("user", nargs=flags.OPTIONAL_INVISIBLE, default=None)
    @commands.cooldown(2, 60, commands.BucketType.user)
    @logged_prefix_command
    async def sync(
        self,
        ctx: PenguinContext,
        *,
        user: str | None = None,
        kamaitachi: bool = False,
    ):
        """Sync scores with the bot.

        It is usually not necessary to use this command, since the bot will automatically track scores as they are fetched from other commands like `c>best50` or `c>recent`.

        Currently scores stored in the bot are only used for `$PREFIXstatistics`. This may change in the future.

        **Parameters**:
        `-k`: Sync the user's Kamaitachi scores.
        `user`: The user to sync scores for. Yourself, if not specified. Alternatively, a CHUNITHM International friend code is also accepted.
        """

        user_or_friend_code: discord.User | discord.Member | str | None = None

        if user is not None:
            try:
                user_or_friend_code = await MemberOrUserConverter().convert(ctx, user)
            except commands.UserNotFound:
                if kamaitachi or not user.isdigit():
                    raise

                user_or_friend_code = user

        await self._sync_impl(ctx, user=user_or_friend_code, kamaitachi=kamaitachi)

    @app_commands.command(name="sync", description="Sync scores with the bot.")
    @app_commands.describe(
        user="The user to sync scores for.",
        friend_code="The friend code to sync scores for.",
        kamaitachi="Sync the user's Kamaitachi scores.",
    )
    @app_commands.rename(friend_code="friend-code")
    @app_commands.checks.cooldown(2, 60)
    @logged_app_command
    async def sync_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        *,
        user: discord.User | discord.Member | None = None,
        friend_code: str | None = None,
        kamaitachi: bool = False,
    ):
        if friend_code is not None and user is not None:
            msg = "Cannot specify both a user and a friend code."
            raise commands.BadArgument(msg)

        if friend_code is not None and not friend_code.isdigit():
            msg = "Invalid friend code."
            raise commands.BadArgument(msg)

        await self._sync_impl(
            await PenguinContext.from_interaction(interaction),
            user=friend_code or user,
            kamaitachi=kamaitachi,
        )

    async def _sync_impl(
        self,
        ctx: PenguinContext,
        *,
        user: discord.User | discord.Member | str | None = None,
        kamaitachi: bool = False,
    ):
        if isinstance(user, str):
            async with ctx.typing():
                (
                    profile,
                    _,
                ) = await self.accounts.fetch_chunithm_net_from_friend_code(ctx, user)

            await ctx.respond_or_edit(
                f"Successfully synced CHUNITHM International scores for {escape_markdown(profile.username)}."
            )
            return

        target_id = user.id if user is not None else ctx.author.id
        cookie = await self.database.cookies.get_by_discord_id(target_id)

        if (
            user is None
            and not kamaitachi
            and (
                cookie is None
                or (not cookie.cookie and cookie.kamaitachi_token is None)
            )
        ):
            view = FriendCodeOfferView(
                ctx, cookie.friend_code if cookie is not None else None
            )
            await view.start()
            return

        async with (
            ctx.typing(),
            self.accounts.network(ctx, target_id, kamaitachi=kamaitachi) as client,
        ):
            profile = await client.get_minimal_profile()

            await ctx.respond_or_edit(f"Fetching recent scores from {client.NAME}...")

            recents = await client.get_recent_scores()

            if client.SUPPORTS_DETAILED_RECENT_SCORE:
                for i, recent in enumerate(recents):
                    if (i + 1) % 10 == 0 or (i + 1) == len(recents):
                        await ctx.respond_or_edit(
                            f"Fetching recent scores from {client.NAME}... {i + 1}/{len(recents)}"
                        )

                    # Side effect: also processes scores
                    _ = await client.get_detailed_recent_score(recent)

            await ctx.respond_or_edit(f"Fetching personal bests from {client.NAME}...")

            # Side effect: fetches PBs and puts them into the database
            _ = await client.get_all_personal_bests()

        await ctx.respond_or_edit(
            f"Successfully synced {client.NAME} scores for {escape_markdown(profile.username)}."
        )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(ScoreSyncCog(bot))
