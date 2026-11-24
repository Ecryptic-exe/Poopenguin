from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, override

import discord
from discord.ext.commands import Context

from chuni_penguin.utils import floor_to_ndp
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, ui_text

from ._pagination import FormatPageReturn, ListPageSource, PaginationView
from .components.score_card_embed import ScoreCardEmbed

if TYPE_CHECKING:
    from chuni_penguin.types import Score


class B30PageSource(ListPageSource["Score"]):
    __slots__ = (
        "average",
        "has_estimated_play_rating",
        "heading",
        "reachable",
        "show_average",
        "show_lamps",
        "show_reachable",
        "synthesis_alt_jacket",
        "total_rating",
    )

    def __init__(
        self,
        *,
        records: Sequence["Score"],
        rating_slots: int = 30,
        per_page: int = 3,
        show_average: bool = True,
        show_reachable: bool = True,
        show_lamps: bool = False,
        synthesis_alt_jacket: str | None = None,
        heading: str = "",
    ) -> None:
        super().__init__(records, per_page=per_page)

        known_ratings = [row.rating for row in records if row.rating is not None]
        self.heading = heading
        self.total_rating = (
            sum(known_ratings, start=Decimal(0))
            if len(known_ratings) == len(records)
            else None
        )
        self.average = (
            floor_to_ndp(self.total_rating / rating_slots, 4)
            if self.total_rating is not None and rating_slots
            else None
        )
        self.reachable = (
            floor_to_ndp(
                self.total_rating / 40 + max(known_ratings, default=Decimal(0)) / 4, 4
            )
            if self.total_rating is not None
            else None
        )
        self.has_estimated_play_rating = any(
            row.rating is not None and row.chart.internal_level is None
            for row in records
        )
        self.show_average = show_average
        self.show_reachable = show_reachable
        self.show_lamps = show_lamps
        self.synthesis_alt_jacket = synthesis_alt_jacket

    @override
    async def format_page(
        self, menu: "PaginationView", page: Sequence["Score"]
    ) -> FormatPageReturn:
        start = menu.current_page * self.per_page
        embeds: list[discord.Embed] = [
            ScoreCardEmbed(
                record,
                show_lamps=self.show_lamps,
                index=start + i + 1,
                synthesis_alt_jacket=self.synthesis_alt_jacket,
                language=get_chunithm_language(menu.ctx),
            )
            for i, record in enumerate(page)
        ]

        kwargs: FormatPageReturn = {"embeds": embeds}

        lines = [self.heading] if self.heading else []
        unknown = ui_text(menu.ctx, "未提供")
        if self.show_average:
            lines.append(
                ui_text(
                    menu.ctx,
                    "Average: **{0}**",
                    self.average if self.average is not None else unknown,
                )
            )
        if self.show_reachable:
            lines.append(
                ui_text(
                    menu.ctx,
                    "\nReachable: **{0}**",
                    self.reachable if self.reachable is not None else unknown,
                ).lstrip()
            )
        if self.has_estimated_play_rating:
            lines.append(ui_text(menu.ctx, "缺少已確認定數的單曲 Rating 為估算值。"))
        if not page:
            lines.append(ui_text(menu.ctx, "此組別沒有來源紀錄。"))
        if lines:
            kwargs["content"] = "\n".join(lines)

        return kwargs


class B30View(PaginationView):
    def __init__(
        self,
        ctx: Context,
        items: Sequence["Score"],
        rating_slots: int = 30,
        per_page: int = 3,
        *,
        show_average: bool = True,
        show_reachable: bool = True,
        show_lamps: bool = False,
        synthesis_alt_jacket: str | None = None,
        heading: str = "",
    ):
        super().__init__(
            ctx,
            B30PageSource(
                records=items,
                rating_slots=rating_slots,
                per_page=per_page,
                show_average=show_average,
                show_reachable=show_reachable,
                show_lamps=show_lamps,
                synthesis_alt_jacket=synthesis_alt_jacket,
                heading=heading,
            ),
        )
