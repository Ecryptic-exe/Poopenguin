# ruff: noqa: RUF001 - Traditional Chinese UI
import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING, Literal, cast, override

import discord
import httpx
from discord.ext import commands
from discord.utils import escape_markdown, format_dt

from chuni_penguin.adapters.errors import AuthenticationError, NetworkError
from chuni_penguin.logging import logger
from chuni_penguin.services.teams import (
    FIRST_MONTH,
    TeamBoard,
    TeamDetailsData,
    find_teams,
    parse_month,
    shift_month,
)
from chuni_penguin.types.ranking import TeamRankingEntry

from ._base import MessageKwargs, PenguinView
from ._embed_pages import embed_pages as _embed_pages

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext

TeamTab = Literal["overview", "ranking", "history"]
TEAM_TABS: tuple[tuple[TeamTab, str], ...] = (
    ("overview", "團隊概況"),
    ("ranking", "團隊排行"),
    ("history", "歷史月榜"),
)


@dataclass(frozen=True)
class TeamState:
    tab: TeamTab = "ranking"
    month: date | None = None
    query: str = ""
    selected_name: str | None = None
    selected_position: int | None = None
    page: int = 0
    choices_page: int = 0


@dataclass
class TeamFrame:
    state: TeamState
    board: TeamBoard | None
    matches: list[TeamRankingEntry]
    selected: TeamRankingEntry | None
    pages: list[list[discord.Embed]]


class TeamTabButton(discord.ui.Button["TeamDetailsView"]):
    def __init__(self, tab: TeamTab, label: str, *, selected: bool):
        super().__init__(
            label=label,
            row=0,
            custom_id=f"team:tab:{tab}",
            style=discord.ButtonStyle.primary
            if selected
            else discord.ButtonStyle.secondary,
        )
        self.tab = tab

    @override
    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        await self.view.transition(interaction, tab=self.tab, page=0)


class TeamSelect(discord.ui.Select["TeamDetailsView"]):
    def __init__(self, frame: TeamFrame):
        start = frame.state.choices_page * 25
        self.entries = frame.matches[start : start + 25]
        fingerprint = hashlib.sha256(
            repr(
                (frame.state.month, [(e.team_name, e.position) for e in self.entries])
            ).encode()
        ).hexdigest()[:16]
        super().__init__(
            row=2,
            custom_id=f"team:select:{fingerprint}",
            placeholder="選擇團隊以查看概況或歷史",
            options=[
                discord.SelectOption(
                    label=entry.team_name[:100],
                    value=str(index),
                    description=f"第 {entry.position} 名 · {entry.points:,} 點"[:100],
                    default=entry is frame.selected,
                )
                for index, entry in enumerate(self.entries)
            ],
        )

    @override
    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        entry = self.entries[int(self.values[0])]
        await self.view.transition(
            interaction,
            selected_name=entry.team_name,
            selected_position=entry.position,
            tab="overview",
            page=0,
        )


class TeamSearchModal(discord.ui.Modal, title="搜尋團隊"):
    query = discord.ui.TextInput(
        label="團隊名稱",
        required=False,
        max_length=100,
        placeholder="留空可顯示全部排行",
    )

    def __init__(self, view: "TeamDetailsView"):
        super().__init__()
        self.menu = view
        self.query.default = view.state.query

    @override
    async def on_submit(self, interaction: discord.Interaction):
        query = self.query.value.strip()
        await self.menu.transition(
            interaction,
            query=query,
            selected_name=None,
            selected_position=None,
            tab="overview" if query else "ranking",
            page=0,
            choices_page=0,
        )


class TeamMonthModal(discord.ui.Modal, title="選擇查詢月份"):
    month = discord.ui.TextInput(
        label="月份（YYYY/MM）", max_length=7, placeholder="例如 2026/09"
    )

    def __init__(self, view: "TeamDetailsView"):
        super().__init__()
        self.menu = view
        self.month.default = (view.state.month or view.data.now()).strftime("%Y/%m")

    @override
    async def on_submit(self, interaction: discord.Interaction):
        try:
            month = parse_month(self.month.value, now=self.menu.data.now())
        except ValueError:
            await interaction.response.send_message(
                "請輸入 2015/01 至本月間的有效月份，格式為 YYYY/MM。", ephemeral=True
            )
            return
        await self.menu.transition(
            interaction, month=month, selected_position=None, page=0, choices_page=0
        )


class TeamDetailsView(PenguinView["PenguinContext"]):
    def __init__(
        self,
        ctx: "PenguinContext",
        *,
        query: str = "",
        month: date | None = None,
        mine: bool = False,
        data: TeamDetailsData | None = None,
    ):
        super().__init__(ctx, timeout=600)
        self.message = None
        self.data = data or TeamDetailsData(ctx)
        self.state = TeamState(
            query=query.strip(),
            month=month,
            tab="overview" if query or mine else "ranking",
        )
        self._initial_mine = mine
        self._revision = 0
        self._edit_lock = asyncio.Lock()
        self._expired = False

    @staticmethod
    def _notice(state: TeamState, text: str) -> TeamFrame:
        embed = discord.Embed(
            title="團隊查詢", description=text, color=discord.Color.yellow()
        )
        return TeamFrame(state, None, [], None, _embed_pages(embed))

    async def render(self, state: TeamState) -> TeamFrame:
        board = await self.data.monthly(state.month)
        matches = find_teams(board.entries, state.query)
        selected = None
        candidates = [
            entry for entry in matches if entry.team_name == state.selected_name
        ]
        if len(candidates) == 1:
            selected = candidates[0]
        elif candidates and state.selected_position is not None:
            positions = [
                entry
                for entry in candidates
                if entry.position == state.selected_position
            ]
            if len(positions) == 1:
                selected = positions[0]
        if selected is None and state.selected_name is None and len(matches) == 1:
            selected = matches[0]
        if selected is not None:
            state = replace(
                state,
                selected_name=selected.team_name,
                selected_position=selected.position,
            )
        history_name = selected.team_name if selected else state.selected_name
        if history_name is None and not matches and state.query:
            # A team may be absent from the current board but present in earlier months.
            # The supplied name is used literally; no fuzzy historical identity is inferred.
            history_name = state.query

        month_label = state.month.strftime("%Y/%m") if state.month else "最新榜"
        embed = discord.Embed(
            title=f"團隊查詢 · {month_label}",
            color=board.color,
            timestamp=board.updated_at,
        )
        embed.set_footer(text=board.network)
        if state.tab == "ranking":
            pages = []
            for start in range(0, len(matches), 10):
                page = embed.copy()
                page.description = "\n".join(
                    f"**{entry.position}.** {escape_markdown(entry.team_name)} · **{entry.points:,}** 點 · {entry.delta:+,} · {entry.ranking_delta.emoji}"
                    for entry in matches[start : start + 10]
                )
                pages.extend(_embed_pages(page))
            if not pages:
                embed.description = "此榜單未找到符合的團隊；可改用其他名稱或月份查詢。"
                pages = [[embed]]
        elif selected is None and (state.tab != "history" or history_name is None):
            if not matches:
                embed.description = "此榜單沒有符合的團隊。未上榜不代表團隊不存在，可切換月份或重新搜尋。"
            elif state.selected_name:
                embed.description = "原先選擇的團隊在此月份無法唯一辨識。可切到歷史月榜，或從下方選單重新選擇團隊。"
            elif state.query:
                embed.description = (
                    f"找到 {len(matches)} 筆符合的團隊，請用下方選單選擇。"
                )
            else:
                embed.description = f"已取得 {len(matches)} 隊的排行資料。請搜尋或選擇團隊，也可按「我的團隊」查詢自己的帳號。"
            pages = [[embed]]
        elif state.tab == "overview":
            assert selected is not None
            embed.title = selected.team_name[:256]
            embed.description = (
                f"**{escape_markdown(selected.team_name)}**\n查詢月份：{month_label}"
            )
            embed.add_field(name="名次", value=f"第 {selected.position} 名")
            embed.add_field(name="團隊點數", value=f"{selected.points:,}")
            embed.add_field(name="來源回報的點數增減", value=f"{selected.delta:+,}")
            embed.add_field(name="名次變化", value=selected.ranking_delta.emoji)
            pages = _embed_pages(embed)
        else:
            name = history_name
            assert name is not None
            history = await self.data.history(name, end=state.month)
            rows = []
            labels = {
                "not_found": "未上榜或未找到同名團隊",
                "ambiguous": "同名團隊，無法唯一配對",
                "unavailable": "來源暫時不可用，可重試",
            }
            for point in history:
                label = point.month.strftime("%Y/%m")
                if point.entry is not None:
                    row = f"**{label}** · 第 {point.entry.position} 名 · **{point.entry.points:,}** 點 · {point.entry.delta:+,}"
                else:
                    row = f"**{label}** · {labels[point.status]}"
                if point.updated_at is not None:
                    row += f"\n更新：{format_dt(point.updated_at, 'd')}"
                rows.append(row)
            embed.title = f"歷史月榜 · {name}"[:256]
            embed.timestamp = None
            embed.description = (
                "\n\n".join(rows)
                + "\n\n依完整隊名比對。改名或重名可能無法配對；未找到的月份不會視為零分。"
            )
            pages = _embed_pages(embed)
        return TeamFrame(state, board, matches, selected, pages)

    async def _safe_render(self, state: TeamState, *, mine: bool = False) -> TeamFrame:
        try:
            if mine:
                team = await self.data.mine()
                if team is None:
                    return self._notice(
                        state, "帳號目前沒有回報所屬團隊。可直接搜尋團隊或查看排行榜。"
                    )
                state = replace(
                    state,
                    query=team.name,
                    selected_name=team.name,
                    selected_position=None,
                    tab="overview",
                    page=0,
                    choices_page=0,
                )
            return await self.render(state)
        except (AuthenticationError, commands.CommandError):
            text = "目前無法取得團隊資料。「我的團隊」需先用 /login 綁定 CHUNITHM-NET；排行榜需由管理員設定 Bot 的來源帳號。"
        except (NetworkError, httpx.HTTPError, TimeoutError):
            text = "團隊資料來源暫時無法回應，請再按一次分頁重試。"
        except NotImplementedError:
            text = "此來源目前不支援團隊資料。"
        except Exception as error:  # noqa: BLE001 - do not expose upstream responses or credentials
            await logger.awarning(
                "Team card failed",
                tag="team_card_error",
                error_type=type(error).__name__,
            )
            text = "團隊資料暫時無法顯示，請稍後重試。"
        return self._notice(state, text)

    def _controls(self, frame: TeamFrame):
        self.clear_items()
        for tab, label in TEAM_TABS:
            self.add_item(TeamTabButton(tab, label, selected=tab == self.state.tab))
        self.add_item(self.search)
        self.add_item(self.my_team)
        anchor = self.state.month or self.data.now()
        self.previous_month.disabled = anchor <= FIRST_MONTH
        self.next_month.disabled = self.state.month is None or anchor >= self.data.now()
        self.choose_month.label = (
            self.state.month.strftime("%Y/%m") if self.state.month else "選擇月份"
        )
        for item in (
            self.previous_month,
            self.choose_month,
            self.next_month,
            self.latest,
        ):
            self.add_item(item)
        if frame.matches:
            self.add_item(TeamSelect(frame))
        if len(frame.matches) > 25:
            self.previous_choices.disabled = self.state.choices_page == 0
            self.next_choices.disabled = (self.state.choices_page + 1) * 25 >= len(
                frame.matches
            )
            self.previous_choices.label = f"上一組 ({self.state.choices_page + 1}/{(len(frame.matches) + 24) // 25})"
            self.add_item(self.previous_choices)
            self.add_item(self.next_choices)
        if len(frame.pages) > 1:
            self.previous_page.disabled = self.state.page == 0
            self.next_page.disabled = self.state.page == len(frame.pages) - 1
            self.position.label = f"{self.state.page + 1}/{len(frame.pages)}"
            for item in (self.previous_page, self.position, self.next_page):
                self.add_item(item)

    def _kwargs(self, frame: TeamFrame, *, editing: bool) -> dict:
        self.state = replace(
            frame.state,
            page=min(frame.state.page, len(frame.pages) - 1),
            choices_page=min(
                frame.state.choices_page, max((len(frame.matches) - 1) // 25, 0)
            ),
        )
        frame.state = self.state
        self._controls(frame)
        embeds = [embed.copy() for embed in frame.pages[self.state.page]]
        footer = embeds[-1].footer.text or ""
        label = dict(TEAM_TABS)[self.state.tab]
        embeds[-1].set_footer(
            text=f"{footer}\n{label} · {self.state.page + 1}/{len(frame.pages)}".strip()
        )
        return {
            "content": None,
            "embeds": embeds,
            "attachments" if editing else "files": [],
        }

    async def transition(
        self, interaction: discord.Interaction, *, mine: bool = False, **changes
    ):
        if self._expired:
            await interaction.response.send_message(
                "資訊卡已逾時，請重新查詢團隊。", ephemeral=True
            )
            return
        self._revision += 1
        revision = self._revision
        self.state = replace(self.state, **changes)
        state = self.state
        await interaction.response.defer()
        frame = await self._safe_render(state, mine=mine)
        async with self._edit_lock:
            if self._expired or revision != self._revision:
                return
            await self.edit_message(interaction, **self._kwargs(frame, editing=True))

    @override
    async def _before_start(self, *, content: str | None = None) -> MessageKwargs:
        kwargs = self._kwargs(
            await self._safe_render(self.state, mine=self._initial_mine), editing=False
        )
        self._initial_mine = False
        kwargs["content"] = content
        return cast(MessageKwargs, kwargs)

    @override
    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message(
            "請自行使用 /team 開啟自己的團隊資訊卡。", ephemeral=True
        )
        return False

    @override
    async def on_timeout(self):
        self._expired = True
        self._revision += 1
        await self.data.close()
        async with self._edit_lock:
            await super().on_timeout()

    @discord.ui.button(label="搜尋團隊", row=0)
    async def search(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(TeamSearchModal(self))

    @discord.ui.button(label="我的團隊", row=0)
    async def my_team(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.transition(interaction, mine=True)

    @discord.ui.button(label="上個月", row=1)
    async def previous_month(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        month = shift_month(self.state.month or self.data.now(), -1)
        await self.transition(
            interaction,
            month=max(month, FIRST_MONTH),
            selected_position=None,
            page=0,
            choices_page=0,
        )

    @discord.ui.button(label="選擇月份", row=1)
    async def choose_month(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await interaction.response.send_modal(TeamMonthModal(self))

    @discord.ui.button(label="下個月", row=1)
    async def next_month(self, interaction: discord.Interaction, _: discord.ui.Button):
        month = shift_month(self.state.month or self.data.now(), 1)
        await self.transition(
            interaction,
            month=min(month, self.data.now()),
            selected_position=None,
            page=0,
            choices_page=0,
        )

    @discord.ui.button(label="最新榜", row=1)
    async def latest(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.transition(
            interaction, month=None, selected_position=None, page=0, choices_page=0
        )

    @discord.ui.button(label="上一組", row=3)
    async def previous_choices(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await self.transition(
            interaction, choices_page=max(self.state.choices_page - 1, 0)
        )

    @discord.ui.button(label="下一組", row=3)
    async def next_choices(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await self.transition(interaction, choices_page=self.state.choices_page + 1)

    @discord.ui.button(label="上一頁", row=3)
    async def previous_page(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await self.transition(interaction, page=max(self.state.page - 1, 0))

    @discord.ui.button(label="1/1", row=3, disabled=True)
    async def position(self, interaction: discord.Interaction, _: discord.ui.Button):
        pass

    @discord.ui.button(label="下一頁", row=3)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.transition(interaction, page=self.state.page + 1)
