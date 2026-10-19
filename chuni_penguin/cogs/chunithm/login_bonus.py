"""Original login bonus presentation with an independent account boundary."""

from typing import TYPE_CHECKING

from discord.ext import commands

from chuni_penguin.context import PenguinContext
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.ui import LoginBonusView

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class LoginBonusCog(commands.Cog, name="LoginBonus"):
    def __init__(self, bot: "ChuniBot"):
        self.accounts = bot.services.accounts

    @commands.hybrid_command("loginbonus")
    @logged_prefix_command
    async def loginbonus(self, ctx: PenguinContext):
        """View your current login bonus progress."""

        async with (
            ctx.typing(),
            self.accounts.network(ctx, chunithm_net=True) as client,
        ):
            login_bonus = await client.get_login_bonus_progress()

        view = LoginBonusView(ctx, login_bonus)
        await view.start()


async def setup(bot: "ChuniBot"):
    await bot.add_cog(LoginBonusCog(bot))
