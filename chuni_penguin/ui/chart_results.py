# ruff: noqa: RUF001 - Traditional Chinese UI punctuation
"""A reusable, same-message browser for canonical chart search results."""

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast, override

import discord
from discord.utils import escape_markdown

from chuni_penguin.database import Chart
from chuni_penguin.types import Difficulty

from ._base import MessageKwargs, PenguinView
from .song_labels import availability_label

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext

PAGE_SIZE = 10


def _difficulty_label(value: str) -> str:
    try:
        return str(Difficulty(value))
    except ValueError:
        return value


class ChartResultSelect(discord.ui.Select["ChartResultsView"]):
    def __init__(self, view: "ChartResultsView") -> None:
        charts = view.page_charts
        super().__init__(
            placeholder="選擇譜面以開啟歌曲資訊",
            custom_id="chart_results:chart",
            row=0,
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(
                        f"{' '.join(chart.song.title.splitlines())} · "
                        f"{_difficulty_label(chart.difficulty)} {chart.level}"
                    )[:100],
                    value=str(chart.id),
                    description=(
                        f"收錄版本 {chart.version or chart.song.version or '未知'} · "
                        f"{availability_label(chart.song)}"
                    )[:100],
                )
                for chart in charts
            ],
            disabled=not charts,
        )

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.select_chart(interaction, self.values[0])


class ChartResultsButton(discord.ui.Button["ChartResultsView"]):
    def __init__(
        self,
        label: str,
        action: str,
        *,
        row: int,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            label=label,
            custom_id=f"chart_results:{action}",
            row=row,
            disabled=disabled,
        )
        self.action = action

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        if self.action == "previous":
            await self.view.change_page(interaction, -1)
        elif self.action == "next":
            await self.view.change_page(interaction, 1)
        elif self.action == "modify":
            from .panel import navigate, session_for

            section = session_for(self.view.ctx).route.section
            if section not in ("find", "tags"):
                section = "find"
            await navigate(self.view.ctx, interaction, section)
        elif self.action == "song_home":
            from .panel import navigate

            await navigate(self.view.ctx, interaction, "song_home")


class ChartResultsView(PenguinView["PenguinContext"]):
    """Paginated canonical charts that can return to their originating search."""

    _panel_own_navigation = True
    page_size = PAGE_SIZE

    def __init__(
        self,
        ctx: "PenguinContext",
        charts: Sequence[Chart],
        *,
        title: str,
        query_label: str = "",
        page: int = 0,
    ) -> None:
        super().__init__(ctx, timeout=600)
        self.charts = tuple(charts)
        self.title = title
        self.query_label = query_label
        self.page = max(0, min(page, self.page_count - 1))
        self._expired = False
        self._edit_lock = asyncio.Lock()
        self._build_controls()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.charts) + self.page_size - 1) // self.page_size)

    @property
    def page_charts(self) -> tuple[Chart, ...]:
        start = self.page * self.page_size
        return self.charts[start : start + self.page_size]

    def _build_controls(self) -> None:
        self.clear_items()
        if self.page_charts:
            self.add_item(ChartResultSelect(self))
        if self.page_count > 1:
            self.add_item(
                ChartResultsButton("上一頁", "previous", row=1, disabled=self.page <= 0)
            )
            self.add_item(
                ChartResultsButton(
                    "下一頁",
                    "next",
                    row=1,
                    disabled=self.page >= self.page_count - 1,
                )
            )
        self.add_item(ChartResultsButton("修改條件", "modify", row=2))
        self.add_item(ChartResultsButton("返回歌曲搜尋", "song_home", row=2))
        from .dismiss import ensure_close

        ensure_close(self, self.ctx.author.id, ctx=self.ctx)

    def _description(self) -> str:
        if not self.charts:
            return "沒有符合條件的譜面。"

        lines = []
        first_number = self.page * self.page_size + 1
        for number, chart in enumerate(self.page_charts, start=first_number):
            difficulty = _difficulty_label(chart.difficulty)
            title = escape_markdown(chart.song.title.replace("\n", " "))[:140]
            version = escape_markdown(chart.version or chart.song.version or "未知")
            status = availability_label(chart.song)
            lines.append(
                f"{number}. **{chart.level} · {difficulty}** {title}\n"
                f"   收錄版本：{version} · 狀態：{status}"
            )
        return "\n".join(lines)[:4096]

    def _kwargs(self) -> MessageKwargs:
        embed = discord.Embed(
            title=self.title[:256],
            description=self._description(),
            color=0x57B9CF,
        )
        footer = f"第 {self.page + 1}/{self.page_count} 頁"
        if self.query_label:
            footer = f"{escape_markdown(self.query_label)[:180]} · {footer}"
        embed.set_footer(text=footer[:2048])
        return {"content": None, "embed": embed}

    @override
    async def _before_start(self, *, content: str | None = None) -> MessageKwargs:
        from .panel import session_for

        cast(Any, session_for(self.ctx)).active_view = self
        kwargs = self._kwargs()
        kwargs["content"] = content
        return kwargs

    @override
    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if (
            getattr(self, "_panel_inactive", False)
            or self._expired
            or self.is_finished()
        ):
            message = "此譜面結果頁已失效，請重新搜尋。"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return False
        return True

    @override
    async def on_timeout(self) -> None:
        self._expired = True
        await super().on_timeout()
        if self.message is not None and not getattr(self, "_panel_inactive", False):
            try:
                await self.message.edit(
                    content="篩選結果已逾時，請重新開啟 /chart 篩選。",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass

    async def select_chart(
        self, interaction: discord.Interaction, chart_id: str
    ) -> None:
        from .panel import open_chart_result

        try:
            selected_id = int(chart_id)
        except (TypeError, ValueError):
            selected_id = -1
        chart = next(
            (item for item in self.page_charts if item.id == selected_id), None
        )
        if chart is None:
            message = "這個譜面結果已失效，請重新搜尋。"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        await open_chart_result(self.ctx, interaction, chart, origin_view=self)

    async def change_page(
        self, interaction: discord.Interaction, direction: int
    ) -> None:
        from .panel import component_context, defer_panel

        if not await self.interaction_check(interaction):
            return
        updated = component_context(self.ctx, interaction)
        await defer_panel(updated, interaction)
        async with self._edit_lock:
            if (
                getattr(self, "_panel_inactive", False)
                or self._expired
                or self.is_finished()
            ):
                return
            self.page = max(0, min(self.page + direction, self.page_count - 1))
            self._build_controls()
            await self.edit_message(interaction, **self._kwargs())
