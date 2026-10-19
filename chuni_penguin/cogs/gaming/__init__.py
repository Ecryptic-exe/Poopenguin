from typing import TYPE_CHECKING

from .cog import GamingCog

if TYPE_CHECKING:
    from bot import ChuniBot


async def setup(bot: "ChuniBot") -> None:
    cog = GamingCog(bot)
    await bot.add_cog(cog)
