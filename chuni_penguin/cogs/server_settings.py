"""Original guild-prefix commands through the shared user settings service."""

from typing import TYPE_CHECKING, Optional

from discord.ext import commands

from chuni_penguin.context import PenguinGuildContext
from chuni_penguin.logging import logged_prefix_command

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ServerSettingsCog(commands.Cog, name="ServerSettings"):
    def __init__(self, bot: "ChuniBot"):
        self.users = bot.services.users

    @commands.hybrid_command("prefix")
    @commands.guild_only()
    @logged_prefix_command
    async def prefix(self, ctx: PenguinGuildContext, new_prefix: Optional[str] = None):
        """Get or set the prefix for this server.

        Permissions
        -----------
        Only users with the Manage Guild permission can set the prefix.

        Parameters
        ----------
        new_prefix: Optional[str]
            New prefix to set. If not provided, the current prefix will be shown.
        """

        async with ctx.typing():
            if new_prefix is None:
                answer = await self.users.guild_prefix(ctx)
                await ctx.reply(f"Current prefix: `{answer}`", mention_author=False)
            else:
                permissions = ctx.author.guild_permissions  # type: ignore[reportGeneralTypeIssues]
                missing_permission = permissions.manage_guild is not True
                if missing_permission:
                    raise commands.MissingPermissions(["manage_guild"])

                await self.users.set_guild_prefix(ctx.guild.id, new_prefix)

                await ctx.reply(f"Prefix set to `{new_prefix}`", mention_author=False)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(ServerSettingsCog(bot))
