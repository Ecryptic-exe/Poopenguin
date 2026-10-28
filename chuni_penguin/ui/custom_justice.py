# ruff: noqa: RUF001 - Bilingual modal labels
"""Shared fixed-JUSTICE input and output for song cards and calculator results."""

from typing import TYPE_CHECKING

import discord

from chuni_penguin.calculation import calculate_border
from chuni_penguin.config import config
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, t

if TYPE_CHECKING:
    from .border import BorderView
    from .song_details import SongDetailsView


def add_custom_justice_fields(
    embed: discord.Embed, notecount: int, justice: int, language: str
) -> None:
    borders = calculate_border(notecount, justice=justice)
    lines = []
    for rank, judgements in borders.items():
        value = (
            "N/A"
            if judgements is None
            else f"{judgements.justice}-{judgements.attack}-{judgements.miss}"
        )
        lines.append(f"▸ {config.icons.rank_icon(rank)} ▸ {value}")
    embed.add_field(
        name=t(
            language,
            f"Borders at JUSTICE = {justice}",
            f"JUSTICE = {justice} 時的容錯",
        ),
        value="\n".join(lines),
        inline=False,
    )
    embed.add_field(
        name=t(language, "Convention", "計算方式"),
        value=t(
            language,
            "Keep the original MISS allowance where possible, then maximize ATTACK. 99AJ requires no ATTACK or MISS.",
            "盡量保留原本的 MISS 配額，再取最大 ATTACK。99AJ 不允許 ATTACK 或 MISS。",
        ),
        inline=False,
    )


class CustomJusticeModal(discord.ui.Modal):
    def __init__(self, view: "SongDetailsView | BorderView", notecount: int):
        self.card = view
        self.revision = view._revision
        self.language = get_chunithm_language(view.ctx)
        self.notecount = notecount
        super().__init__(
            title=t(self.language, "Custom JUSTICE", "自訂 JUSTICE"),
            timeout=view.timeout,
        )
        self.count = discord.ui.TextInput(
            placeholder=f"0–{notecount}",
            max_length=max(10, len(str(notecount))),
        )
        self.add_item(
            discord.ui.Label(
                text=t(self.language, "Target JUSTICE count", "目標 JUSTICE 數量"),
                component=self.count,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.card.interaction_check(interaction):
            return
        if (
            self.card._expired
            or self.card.is_finished()
            or getattr(self.card, "_panel_inactive", False)
            or self.revision != self.card._revision
        ):
            await interaction.response.send_message(
                t(
                    self.language,
                    "This card changed or expired. Open Custom JUSTICE again on the current chart.",
                    "資訊卡已變更或逾時。請在目前的譜面重新開啟自訂 JUSTICE。",
                ),
                ephemeral=True,
            )
            return
        value = self.count.value.strip()
        if not value or not value.isascii() or not value.isdecimal():
            await interaction.response.send_message(
                t(
                    self.language,
                    "Enter a non-negative whole number (for example, 50).",
                    "請輸入非負整數（例如 50）。",
                ),
                ephemeral=True,
            )
            return
        justice = int(value)
        if justice > self.notecount:
            await interaction.response.send_message(
                t(
                    self.language,
                    f"JUSTICE cannot exceed this chart's {self.notecount} notes.",
                    f"JUSTICE 不可超過此譜面的總物量 {self.notecount}。",
                ),
                ephemeral=True,
            )
            return
        await self.card.transition(interaction, custom_justice=justice, page=0)

    async def on_error(self, interaction, error):
        await self.card.on_error(interaction, error, self)


class CustomJusticeButton(discord.ui.Button):
    def __init__(
        self,
        view: "SongDetailsView | BorderView",
        notecount: int,
        *,
        row: int = 4,
        reopen_command: str = "/chart",
    ):
        super().__init__(
            label=t(get_chunithm_language(view.ctx), "Custom JUSTICE", "自訂 JUSTICE"),
            custom_id="song_details:custom_justice",
            row=row,
        )
        self.notecount = notecount
        self.reopen_command = reopen_command

    async def callback(self, interaction: discord.Interaction):
        if self.view._expired or self.view.is_finished():
            await interaction.response.send_message(
                t(
                    get_chunithm_language(self.view.ctx),
                    f"This card expired. Please run {self.reopen_command} again.",
                    f"資訊卡已逾時，請重新使用 {self.reopen_command}。",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            CustomJusticeModal(self.view, self.notecount)
        )
