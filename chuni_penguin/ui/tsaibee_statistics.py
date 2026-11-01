"""CHUNILIB population counts and BEST30 cohorts on the existing song card."""

import discord

from chuni_penguin.services.tsaibee_statistics import (
    RANKS,
    StatisticsUnavailable,
    statistics_for,
)
from chuni_penguin.types import Difficulty
from chuni_penguin.utils.chunithm_i18n import ui_text


def _counts(row):
    return "\n".join(
        f"**{rank}** ▸ {value:,} ({value / row.players:.1%})"
        for rank, value in zip(RANKS, row.counts, strict=True)
    )


async def statistics_embeds(ctx, song, chart):
    embed = discord.Embed(
        title=song.title[:256], color=Difficulty(chart.difficulty).color()
    )
    embed.set_author(name=f"{Difficulty(chart.difficulty)} · {chart.level}")
    embed.description = ui_text(
        ctx, "CHUNILIB 公開玩家統計。各門檻為累積人數；來源沒有獨立的 S+ 欄位。"
    )
    try:
        result = await statistics_for(ctx.bot).chart(song.id, chart.difficulty)
    except StatisticsUnavailable:
        embed.add_field(
            name=ui_text(ctx, "統計資料"),
            value=ui_text(
                ctx, "此譜面目前沒有可用的 CHUNILIB 統計，請切換難度或稍後重試。"
            ),
            inline=False,
        )
        return [[embed]]
    row = result.overall
    embed.add_field(
        name=ui_text(ctx, "玩家人數／平均分"),
        value=f"**{row.players:,}** / **{row.average:,}**",
        inline=False,
    )
    embed.add_field(
        name=ui_text(ctx, "達成人數／比例"), value=_counts(row), inline=False
    )
    embed.add_field(
        name="AJ / FC",
        value=f"{row.aj:,} ({row.aj / row.players:.1%}) / {row.fc:,} ({row.fc / row.players:.1%})",
        inline=False,
    )
    embed.add_field(
        name=ui_text(ctx, "統計來源"),
        value=f"[CHUNILIB](https://chunilib.tsaibee.org/) · {result.source.observed_at}",
        inline=False,
    )
    if result.source.stale:
        embed.add_field(
            name=ui_text(ctx, "備用資料"),
            value=ui_text(ctx, "即時來源暫時不可用，顯示上述日期的已保存統計。"),
            inline=False,
        )
    pages = [[embed]]
    for offset in range(0, len(result.bands), 6):
        cohort = discord.Embed(title=song.title[:256], color=embed.color)
        cohort.description = ui_text(
            ctx, "依 BEST30 平均值分組（16.0–17.7）；各組只計入有遊玩此譜面的玩家。"
        )
        for band in result.bands[offset : offset + 6]:
            if not band.players:
                continue
            cohort.add_field(
                name=ui_text(
                    ctx,
                    "BEST30 {0:.1f} · {1:,} 人 · 平均 {2:,}",
                    band.best30,
                    band.players,
                    band.average,
                ),
                value=_counts(band),
                inline=False,
            )
        if result.band_source is not None:
            cohort.set_footer(
                text=ui_text(ctx, "統計擷取：{0}", result.band_source.observed_at)
            )
            if result.band_source.stale:
                cohort.add_field(
                    name=ui_text(ctx, "備用資料"),
                    value=ui_text(
                        ctx, "即時來源暫時不可用，顯示上述日期的已保存統計。"
                    ),
                    inline=False,
                )
        if cohort.fields:
            pages.append([cohort])
    return pages
