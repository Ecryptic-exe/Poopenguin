import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, override

import discord
from discord.ext.commands import Context

from chuni_penguin.utils import floor_to_ndp
from chuni_penguin.utils.chunithm_i18n import ui_text

from ._pagination import PaginationView
from .b30 import B30PageSource

if TYPE_CHECKING:
    from chuni_penguin.types import Score


class B30N20View(PaginationView):
    def __init__(
        self,
        ctx: Context,
        b30: Sequence["Score"],
        n20: Sequence["Score"],
        *,
        synthesis_alt_jacket: str | None = None,
        heading: str = "",
    ):
        self.best30 = B30PageSource(
            records=b30,
            rating_slots=30,
            per_page=3,
            show_reachable=False,
            synthesis_alt_jacket=synthesis_alt_jacket,
        )
        self.new20 = B30PageSource(
            records=n20,
            rating_slots=20,
            per_page=3,
            show_reachable=False,
            synthesis_alt_jacket=synthesis_alt_jacket,
        )

        self.heading = heading
        self._edit_lock = asyncio.Lock()
        self._revision = 0
        super().__init__(ctx, self.new20 if n20 or not b30 else self.best30)
        self.add_item(self.show_best30)
        self.add_item(self.show_new20)
        self._mark_frame()
        self.rating = (
            floor_to_ndp((self.best30.total_rating + self.new20.total_rating) / 50, 2)
            if self.best30.total_rating is not None
            and self.new20.total_rating is not None
            else None
        )

    def _mark_frame(self):
        for button, frame in (
            (self.show_best30, self.best30),
            (self.show_new20, self.new20),
        ):
            button.disabled = frame is self.source
            button.style = (
                discord.ButtonStyle.primary
                if button.disabled
                else discord.ButtonStyle.secondary
            )

    @override
    async def show_page(
        self,
        interaction: discord.Interaction,
        page_index: int,
        *,
        frame: B30PageSource | None = None,
    ):
        self._revision += 1
        revision = self._revision
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with self._edit_lock:
            if revision != self._revision:
                return
            if frame is not None and frame is not self.source:
                self.source = frame
                for nav in (
                    self.to_first_page,
                    self.to_previous_page,
                    self.jump_to_page,
                    self.to_next_page,
                    self.to_last_page,
                ):
                    self.remove_item(nav)
                self.fill_items()
                self._mark_frame()
            await super().show_page(interaction, page_index)

    @override
    async def get_kwargs_from_page(self, page: list["Score"]):
        kwargs = await super().get_kwargs_from_page(page)
        missing = ui_text(self.ctx, "未提供")
        frame = "Best 30" if self.source is self.best30 else "New 20"
        lines = [self.heading] if self.heading else []
        lines.append(
            f"**{frame}** · Rating {self.rating if self.rating is not None else missing}"
        )
        lines.append(
            ui_text(
                self.ctx,
                "平均單曲 Rating · Best 30 {0} / New 20 {1}",
                self.best30.average if self.best30.average is not None else missing,
                self.new20.average if self.new20.average is not None else missing,
            )
        )
        if (
            self.best30.has_estimated_play_rating
            or self.new20.has_estimated_play_rating
        ):
            lines.append(ui_text(self.ctx, "缺少已確認定數的單曲 Rating 為估算值。"))
        if not page:
            lines.append(ui_text(self.ctx, "此組別沒有來源紀錄。"))
        kwargs["content"] = "\n".join(lines)
        return kwargs

    @discord.ui.button(label="Best 30", style=discord.ButtonStyle.grey, row=1)
    async def show_best30(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.show_page(interaction, 0, frame=self.best30)

    @discord.ui.button(label="New 20", style=discord.ButtonStyle.green, row=1)
    async def show_new20(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.show_page(interaction, 0, frame=self.new20)
