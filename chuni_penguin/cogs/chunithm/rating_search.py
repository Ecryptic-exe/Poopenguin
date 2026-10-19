"""Project C's metadata-only song search, independent of analysis workers."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class RatingSearchCog(commands.Cog, name="RatingSearch"):
    def __init__(self, bot: "ChuniBot"):
        self.search = bot.services.rating_search

    @app_commands.command(
        name="song_search", description="搜尋曲目與可用難度；不抓取統計或產圖"
    )
    @app_commands.describe(query="曲名、別名或片段")
    async def song_search(self, interaction: discord.Interaction, query: str):
        rows = self.search.search(query, limit=10)
        content = (
            "\n".join(
                f"{index}. **{discord.utils.escape_markdown(row['song'].title)}** · {', '.join(row['song'].difficulties)} · `id:{row['song'].song_id}`"
                for index, row in enumerate(rows, 1)
            )
            or "沒有找到符合的曲目。"
        )
        await interaction.response.send_message(
            content[:1950],
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(RatingSearchCog(bot))
