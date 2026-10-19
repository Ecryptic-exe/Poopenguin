from typing import TYPE_CHECKING

from discord.ext import commands

from chuni_penguin.utils.score_messages import (  # noqa: F401 - legacy helper exports
    extract_images_from_component as _extract_images_from_component,
)
from chuni_penguin.utils.score_messages import (  # noqa: F401 - legacy helper exports
    extract_images_from_message as _extract_images_from_message,
)

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class RecordsCog(commands.Cog, name="Records"):
    def __init__(self, bot: "ChuniBot") -> None:
        self.bot = bot
        self.utils = self.bot.utils
        self.folder_statistics = bot.services.utils.folder_statistics


async def setup(bot: "ChuniBot"):
    await bot.add_cog(RecordsCog(bot))
