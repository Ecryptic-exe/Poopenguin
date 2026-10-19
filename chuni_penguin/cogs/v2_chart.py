# ruff: noqa: RUF001
"""Shared chart additions and source-tag discovery; canonical search stays in A."""

import discord
from discord import app_commands
from discord.ext import commands

from chuni_penguin.context import PenguinContext
from chuni_penguin.repositories.chart_annotations import ChartAnnotationRepository
from chuni_penguin.services.phase1_charts import Phase1Charts
from chuni_penguin.ui import EmbedPaginationView


class ChartCog(commands.Cog, name="V2Chart"):
    def __init__(self, bot):
        self.bot = bot
        self.service = Phase1Charts(
            allow_network=not bot.offline,
            annotations=ChartAnnotationRepository(
                bot.database.mapping_read_sessionmaker
            ),
        )
        bot.phase1_charts = self.service

    async def cog_unload(self):
        await self.service.close()
        if getattr(self.bot, "phase1_charts", None) is self.service:
            self.bot.phase1_charts = None

    @app_commands.command(
        name="tags", description="搜尋 B 人工配置標籤；未審核對應的資料保留來源名稱"
    )
    async def tags(self, interaction: discord.Interaction, tag: str):
        await interaction.response.defer(ephemeral=True)
        rows = await self.service.tag_matches(tag)
        if not rows:
            await interaction.followup.send("沒有符合的人工標籤。", ephemeral=True)
            return
        embeds = []
        for start in range(0, len(rows), 10):
            text = "\n".join(
                f"來源列 {idx} · {discord.utils.escape_markdown(row['title'])} [{row['difficulty']}] · {', '.join(row.get('tags', []))}"
                for idx, row in rows[start : start + 10]
            )
            embeds.append(
                discord.Embed(
                    title=f"人工標籤：{tag[:80]}", description=text[:4000]
                ).set_footer(text="Poopenguin 社群資料 · 依資料庫譜面編號查詢")
            )
        await EmbedPaginationView(
            await PenguinContext.from_interaction(interaction), embeds
        ).start(ephemeral=True)


async def setup(bot):
    await bot.add_cog(ChartCog(bot))
