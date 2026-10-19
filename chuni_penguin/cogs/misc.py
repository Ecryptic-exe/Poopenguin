from typing import TYPE_CHECKING

from discord.ext import commands

from chuni_penguin.utils.runtime_metadata import (  # noqa: F401 - legacy helper export
    get_version_from_pyproject as _get_version_from_pyproject,
)

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class MiscCog(commands.Cog, name="Miscellaneous"):
    def __init__(self, bot: "ChuniBot") -> None:
        self.bot = bot
        self.runtime_info = bot.services.runtime_info


async def setup(bot: "ChuniBot") -> None:
    await bot.add_cog(MiscCog(bot))
