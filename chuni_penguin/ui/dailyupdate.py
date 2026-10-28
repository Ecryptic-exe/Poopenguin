"""Owner-only image pagination using the existing message/view lifecycle."""

import asyncio
from io import BytesIO
from math import ceil
from types import SimpleNamespace

import discord

from chuni_penguin.renderers.dailyupdate import PAGE_SIZE, render_dailyupdate
from chuni_penguin.services.dailyupdate import changed_charts
from chuni_penguin.services.jackets import JacketCache
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, t

from ._base import PenguinView


class DailyUpdateView(PenguinView):
    _panel_own_navigation = True

    def __init__(self, ctx, previous, current, *, live=False):
        super().__init__(ctx, timeout=600)
        self.message = None
        self.previous, self.current = previous, current
        self.rows = changed_charts(previous, current)
        self.page = 0
        self.page_count = max(1, ceil(len(self.rows) / PAGE_SIZE))
        self.language = get_chunithm_language(ctx)
        self.live = live
        self.jackets = JacketCache()
        self._pages = {}
        self._lock = asyncio.Lock()
        self._expired = False
        self.previous_page.label = t(self.language, "Prev", "上一頁")
        self.next_page.label = t(self.language, "Next", "下一頁")

    async def _image(self, page):
        if page not in self._pages:
            rows = self.rows[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
            # Reuse the public jacket cache, with a separate cookie-free client.
            await self.jackets.ensure(
                [
                    SimpleNamespace(
                        song=SimpleNamespace(id=new.song_id, jacket_url=new.jacket_url)
                    )
                    for _, new in rows
                ]
            )
            self._pages[page] = await asyncio.to_thread(
                render_dailyupdate,
                self.previous,
                self.current,
                rows,
                page,
                self.page_count,
                language=self.language,
                live=self.live,
            )
            # Keep only a few rendered pages in memory, regardless of song count.
            while len(self._pages) > 3:
                del self._pages[next(iter(self._pages))]
        return self._pages[page]

    def _buttons(self):
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page + 1 == self.page_count
        if self.page_count == 1:
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)

    async def _before_start(self, *, content=None):
        data = await self._image(0)
        self._buttons()
        return {
            "content": content,
            "files": [discord.File(BytesIO(data), filename="dailyupdate.png")],
        }

    async def interaction_check(self, interaction, /):
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message(
            t(
                self.language,
                "Only the requesting user can use these buttons.",
                "只有指令使用者可以操作這些按鈕。",
            ),
            ephemeral=True,
        )
        return False

    async def _move(self, interaction, offset):
        await interaction.response.defer()
        async with self._lock:
            if (
                self._expired
                or self.is_finished()
                or getattr(self, "_panel_inactive", False)
            ):
                return
            page = max(0, min(self.page + offset, self.page_count - 1))
            data = await self._image(page)
            if (
                self._expired
                or self.is_finished()
                or getattr(self, "_panel_inactive", False)
            ):
                return
            self.page = page
            self._buttons()
            file = discord.File(BytesIO(data), filename="dailyupdate.png")
            try:
                await self.edit_message(interaction, attachments=[file])
            finally:
                file.close()

    async def on_timeout(self):
        self._expired = True
        self._pages.clear()
        await super().on_timeout()

    async def deactivate(self):
        self._expired = True
        self._pages.clear()
        self.stop()

    @discord.ui.button(label="Prev", row=0)
    async def previous_page(self, interaction, button):
        await self._move(interaction, -1)

    @discord.ui.button(label="Next", row=0)
    async def next_page(self, interaction, button):
        await self._move(interaction, 1)
