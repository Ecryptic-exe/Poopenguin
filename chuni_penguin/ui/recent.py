import asyncio
import itertools
from collections.abc import Sequence
from typing import TYPE_CHECKING, AsyncContextManager, override

import discord.ui
from discord.ext.commands import Context

from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, ui_text

from ._base import MessageKwargs
from ._pagination import ListPageSource, PaginationView
from .components.score_card_embed import ScoreCardEmbed

if TYPE_CHECKING:
    from chuni_penguin.adapters.base import NetworkAdapter
    from chuni_penguin.types import Profile, RecentScore


def split_scores_into_credits(scores: list["RecentScore"]) -> list[list["RecentScore"]]:
    if not scores or any(s.track_no is None for s in scores):
        return [list(c) for c in itertools.batched(scores, 3, strict=False)]

    credits = []
    current_credit = []
    last_track = None
    for score in scores:
        assert score.track_no is not None
        if last_track is not None and score.track_no >= last_track:
            credits.append(current_credit)
            current_credit = []
        current_credit.append(score)
        last_track = score.track_no
    credits.append(current_credit)
    return credits


class RecentRecordsPageSource(ListPageSource[list["RecentScore"]]):
    def __init__(self, pages, *, player, source, synthesis_alt_jacket):
        super().__init__(pages, per_page=1)
        self.player = discord.utils.escape_markdown(
            discord.utils.escape_mentions(player), ignore_links=False
        )
        self.source = source
        self.synthesis_alt_jacket = synthesis_alt_jacket

    def heading(self, ctx):
        return f"**{ui_text(ctx, '最近遊玩')}** · {self.player} · {self.source}"

    @override
    async def format_page(
        self, menu: "PaginationView", page: Sequence[list["RecentScore"]]
    ) -> MessageKwargs:
        if not page:
            return {
                "embed": discord.Embed(
                    title=ui_text(menu.ctx, "最近遊玩"),
                    description=ui_text(
                        menu.ctx,
                        "{0} 沒有回傳 {1} 的最近遊玩紀錄。",
                        self.source,
                        self.player,
                    ),
                )
            }
        language = get_chunithm_language(menu.ctx)
        return {
            "content": self.heading(language),
            "embeds": [
                ScoreCardEmbed(
                    score,
                    synthesis_alt_jacket=self.synthesis_alt_jacket,
                    language=language,
                )
                for score in page[0]
            ],
        }


class RecentRecordsView(PaginationView):
    def __init__(
        self,
        ctx: Context,
        target_id: int,
        scores: list["RecentScore"],
        network_client: "NetworkAdapter",
        network_client_manager: AsyncContextManager["NetworkAdapter"],
        userinfo: "Profile",
        synthesis_alt_jacket: str | None = None,
    ):
        self.target_id = target_id
        self.scores = scores
        self.network_client = network_client
        self.network_client_manager: AsyncContextManager["NetworkAdapter"] | None = (
            network_client_manager
        )
        self._network_lock = asyncio.Lock()
        self._edit_lock = asyncio.Lock()
        self._revision = 0
        self._selected: int | None = None
        self._details: dict[int, RecentScore] = {}

        # Unusually long credits still fit the multi-embed message limits.
        pages = [
            list(group)
            for credit in split_scores_into_credits(scores)
            for group in itertools.batched(credit, 3, strict=False)
        ]
        source = RecentRecordsPageSource(
            pages,
            player=userinfo.username,
            source=network_client.NAME,
            synthesis_alt_jacket=synthesis_alt_jacket,
        )
        super().__init__(ctx, source)

        self._options: list[list[discord.SelectOption]] = []
        position = 0
        for page in pages:
            options = []
            for play in page:
                label = " ".join(play.song.title.split()) or str(play.chart.difficulty)
                options.append(
                    discord.SelectOption(
                        label=label if len(label) <= 100 else label[:99] + "…",
                        description=f"{play.chart.difficulty} · {play.score:,}",
                        value=str(position),
                    )
                )
                position += 1
            self._options.append(options)
        if pages:
            self.dropdown.placeholder = ui_text(ctx, "選擇一筆遊玩查看詳情")
            self.dropdown.options = self._options[0]
            self.back_to_plays.label = ui_text(ctx, "返回本頁紀錄")
            self.add_item(self.dropdown)
            self.add_item(self.back_to_plays)

    async def _before_start(self, *, content: str | None = None):
        if not self.scores or not self.network_client.SUPPORTS_DETAILED_RECENT_SCORE:
            await self.close_network()
        return await super()._before_start(content=content)

    async def show_page(self, interaction: discord.Interaction, page_index: int):
        self._revision += 1
        revision = self._revision
        self._selected = None
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with self._edit_lock:
            if revision != self._revision:
                return
            self.dropdown.options = self._options[page_index]
            for option in self.dropdown.options:
                option.default = False
            self.back_to_plays.disabled = True
            await super().show_page(interaction, page_index)

    async def on_timeout(self):
        self._revision += 1
        await self.close_network()
        async with self._edit_lock:
            await super().on_timeout()

    async def close_network(self) -> None:
        async with self._network_lock:
            manager, self.network_client_manager = self.network_client_manager, None
            if manager is not None:
                await manager.__aexit__(None, None, None)

    @discord.ui.button(label="Back to plays", row=4, disabled=True)
    async def back_to_plays(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await self.show_page(interaction, self.current_page)

    @discord.ui.select(placeholder="Select a play", row=2)
    async def dropdown(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        idx = int(select.values[0])
        if idx == self._selected or not any(
            option.value == str(idx) for option in self.dropdown.options
        ):
            await interaction.response.defer()
            return
        self._selected = idx
        self._revision += 1
        revision = self._revision
        await interaction.response.defer()
        try:
            async with self._network_lock:
                if revision != self._revision:
                    return
                if (
                    self.network_client.SUPPORTS_DETAILED_RECENT_SCORE
                    and idx not in self._details
                ):
                    if self.network_client_manager is None:
                        return
                    self._details[
                        idx
                    ] = await self.network_client.get_detailed_recent_score(
                        self.scores[idx]
                    )
                score = self._details.get(idx, self.scores[idx])

            async with self._edit_lock:
                if revision != self._revision or getattr(
                    self, "_panel_inactive", False
                ):
                    return
                for option in self.dropdown.options:
                    option.default = option.value == str(idx)
                self.back_to_plays.disabled = False
                assert isinstance(self.source, RecentRecordsPageSource)
                await self.edit_message(
                    interaction,
                    content=self.source.heading(self.ctx),
                    embed=ScoreCardEmbed(
                        score,
                        synthesis_alt_jacket=self.source.synthesis_alt_jacket,
                        detailed=True,
                        language=get_chunithm_language(self.ctx),
                    ),
                )
        except Exception:
            if revision == self._revision:
                self._selected = None
                raise
