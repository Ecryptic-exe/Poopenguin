"""Explicit player actions through the existing shared account service."""

from typing import TYPE_CHECKING

from discord.ext import commands
from discord.ext.commands import Context

from chuni_penguin.adapters.chunithm_net import ChuniNetError
from chuni_penguin.logging import logged_prefix_command

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class PlayerActionsCog(commands.Cog, name="PlayerActions"):
    def __init__(self, bot: "ChuniBot"):
        self.accounts = bot.services.accounts

    @commands.hybrid_command(name="rename")
    @logged_prefix_command
    async def rename(self, ctx: Context, *, new_name: str):
        """Use magical powers to change your IGN.

        Please note that this will change the actual display name of your CHUNITHM account.

        Parameters
        ----------
        new_name: str
            The username you want to change to.
            Your username can include up to 8 characters, excluding specific characters. You can also use the following symbols.
            ． ・ ： ； ？ ！ ～ ／ ＋ － × ÷ ＝ ♂ ♀ ∀ ＃ ＆ ＊ ＠ ☆ ○ ◎ ◇ □ △ ▽ ♪ † ‡ Σ α β γ θ φ ψ ω Д ё
        """  # noqa: RUF002

        async with (
            ctx.typing(),
            self.accounts.network(ctx, chunithm_net=True) as client,
        ):
            try:
                await client.update_username(new_name)
                await ctx.reply("Your username has been changed.", mention_author=False)
            except ValueError as e:
                msg = str(e)

                if msg == "文字数が多すぎます。":  # Too many characters
                    msg = "The new username is too long (only 8 characters allowed)."

                raise commands.BadArgument(msg) from None
            except ChuniNetError as e:
                if e.code == 110106:
                    msg = "The new username contains a banned word."
                    raise commands.BadArgument(msg) from None

                raise


async def setup(bot: "ChuniBot"):
    await bot.add_cog(PlayerActionsCog(bot))
