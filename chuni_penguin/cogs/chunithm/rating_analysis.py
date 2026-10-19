"""C rating chart command; A's existing /rating calculator keeps its name."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from chuni_penguin.services.rating_search import DIFFICULTIES
from chuni_penguin.ui.rating_chart import RatingChoiceView, deliver_rating_chart

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class RatingAnalysisCog(commands.Cog, name="RatingAnalysis"):
    def __init__(self, bot: "ChuniBot"):
        self.search = bot.services.rating_search
        self.service = bot.services.rating_analysis

    @app_commands.command(
        name="rating_chart", description="搜尋曲目並產生玩家分布圖；只處理所選譜面"
    )
    @app_commands.describe(
        song="曲名或片段；可從自動完成選擇",
        difficulty="譜面難度",
        detailed="顯示完整分數與玩家範圍；預設為 SS 以上、定數 +1.00～+2.15",
    )
    @app_commands.rename(detailed="詳細")
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name=difficulty, value=difficulty)
            for difficulty in DIFFICULTIES
        ]
    )
    @app_commands.checks.cooldown(1, 5, key=lambda interaction: interaction.user.id)
    async def rating_chart(
        self,
        interaction: discord.Interaction,
        song: str,
        difficulty: str = "MASTER",
        *,
        detailed: bool = False,
    ):
        exact = self.search.exact_matches(song, difficulty)
        if len(exact) == 1:
            await deliver_rating_chart(
                interaction, exact[0], difficulty, self.service, detailed=detailed
            )
            return
        other_difficulties = self.search.exact_matches(song)
        if other_difficulties and not exact:
            available = sorted(
                {
                    difficulty
                    for song in other_difficulties
                    for difficulty in song.difficulties
                },
                key=DIFFICULTIES.index,
            )
            await interaction.response.send_message(
                "提供的曲目資料未列此難度。可選：" + ", ".join(available),
                ephemeral=True,
            )
            return
        candidates = self.search.search(song, difficulty, 25)
        if not candidates:
            await interaction.response.send_message(
                "沒有找到符合的曲目。", ephemeral=True
            )
            return
        view = RatingChoiceView(
            [row["song"] for row in candidates],
            difficulty,
            interaction.user.id,
            self.service,
            detailed=detailed,
        )
        await interaction.response.send_message(
            "請選擇要查看的曲目：",
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @rating_chart.autocomplete("song")
    async def complete_song(self, interaction: discord.Interaction, current: str):
        difficulty = getattr(interaction.namespace, "difficulty", "MASTER") or "MASTER"
        return [
            app_commands.Choice(
                name=row["song"].title[:100], value="id:" + row["song"].song_id
            )
            for row in self.search.search(current, difficulty, 25)
        ]


async def setup(bot: "ChuniBot"):
    await bot.add_cog(RatingAnalysisCog(bot))
