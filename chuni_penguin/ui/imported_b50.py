"""Source-snapshot Best 50 cards shared by the slash command and account panel."""

import itertools
from math import ceil

import discord

from chuni_penguin.calculation import calculate_rating
from chuni_penguin.utils.chunithm_i18n import ui_text


def imported_b50_pages(
    ctx, payload, source: str, synced_at: str | None
) -> list[discord.Embed]:
    """Rank only records whose own source supplied a verified constant."""
    ranked = sorted(
        (
            (calculate_rating(row["score"], row["source_const"]), row)
            for row in payload["records"]
            if row.get("source_const") is not None
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )[:50]
    source_rating = payload.get("rating")
    summary = ui_text(
        ctx,
        "Source rating: {0}\nBest50 is ranked locally using verified source constants. It does not represent the source's in-game rating composition.",
        source_rating if source_rating is not None else ui_text(ctx, "未提供"),
    )
    pages = []
    for i, batch in enumerate(itertools.batched(ranked, 5, strict=False)):
        lines = []
        for rank, (rating, row) in enumerate(batch, start=i * 5 + 1):
            title = " ".join(row["title"].split())
            title = title if len(title) <= 150 else title[:149] + "…"
            title = discord.utils.escape_markdown(
                discord.utils.escape_mentions(title), ignore_links=False
            )
            lines.append(
                f"**{rank}. {title}**\n{row['diff']} · {row['score']:,} · Rating {rating:.2f}"
            )
        page = discord.Embed(
            title="Best 50",
            description=summary + "\n\n" + "\n\n".join(lines),
            color=0x57B9CF,
        )
        page.set_footer(
            text=" · ".join(
                (
                    ui_text(
                        ctx,
                        "{0} · 同步 {1}",
                        source,
                        synced_at or ui_text(ctx, "未提供"),
                    ),
                    ui_text(ctx, "第 {0}/{1} 頁", i + 1, ceil(len(ranked) / 5)),
                )
            )
        )
        pages.append(page)
    return pages
