"""Tools border results with the shared custom JUSTICE modal."""

import asyncio
from copy import deepcopy

import discord

from chuni_penguin.utils.chunithm_i18n import get_chunithm_language

from .custom_justice import CustomJusticeButton, add_custom_justice_fields
from .panel import PanelView


class BorderView(PanelView):
    def __init__(self, ctx, embed: discord.Embed, notecount: int):
        super().__init__(ctx)
        self.message = None
        self.embed = deepcopy(embed)
        self.notecount = notecount
        self.custom_justice = None
        self._revision = 0
        self._expired = False
        self._edit_lock = asyncio.Lock()
        self.add_item(
            CustomJusticeButton(self, notecount, row=3, reopen_command="/tools")
        )

    async def _before_start(self, *, content=None):
        return {"content": content, "embed": deepcopy(self.embed)}

    async def transition(
        self, interaction: discord.Interaction, *, custom_justice: int, page: int = 0
    ):
        self._revision += 1
        revision = self._revision
        await interaction.response.defer()
        async with self._edit_lock:
            if (
                self._expired
                or self.is_finished()
                or getattr(self, "_panel_inactive", False)
                or revision != self._revision
            ):
                return
            embed = deepcopy(self.embed)
            add_custom_justice_fields(
                embed,
                self.notecount,
                custom_justice,
                get_chunithm_language(self.ctx),
            )
            self.custom_justice = custom_justice
            await self.edit_message(interaction, embed=embed)

    async def on_timeout(self):
        self._expired = True
        self._revision += 1
        await super().on_timeout()

    async def deactivate(self):
        self._expired = True
        self._revision += 1
        self.stop()
