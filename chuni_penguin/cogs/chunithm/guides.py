"""Original A guide links, independently managed without a catalog Provider."""

from typing import TYPE_CHECKING

from discord.ext import commands
from discord.ext.commands import Context

from chuni_penguin.logging import logged_prefix_command

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class GuidesCog(commands.Cog, name="Guides"):
    @commands.hybrid_command("codex", aliases=["odex"])
    @logged_prefix_command
    async def odex(self, ctx: Context):
        """Read the Codex."""

        await ctx.reply(
            content=(
                "Read the Codex? Codexes? Codices? It doesn't really matter, just read them.\n"
                "- [Chunithm English Guide](<https://chunithm.org>)\n"
                "- [The (Extended) Chunithm Tutorial](<https://chunithm.org/how-to-improve>)\n"
                "- [Chunithm Chart Compendium/Codex](<https://chunithm.org/codex>)\n"
                "- [Kamaitachi Chunithm Questline](<https://chunithm.org/quests>)\n"
                "- [Linked VERSE Unlock Guide](<https://chunithm.org/linked-verse>)"
            ),
            mention_author=False,
        )

    @commands.hybrid_command("how-to-improve", aliases=["how2improve", "howtoimprove"])
    @logged_prefix_command
    async def how_to_improve(self, ctx: Context):
        await ctx.reply(
            content="[The (Extended) Chunithm Tutorial](https://chunithm.org/how-to-improve)",
            mention_author=False,
        )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(GuidesCog())
