# ruff: noqa: RUF001 - Traditional Chinese UI punctuation
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from io import BytesIO
from typing import TYPE_CHECKING, Literal, cast, override
from urllib.parse import quote

import discord
import httpx
from discord.ext import commands
from discord.utils import escape_markdown, format_dt

from chuni_penguin.adapters.errors import (
    AuthenticationError,
    ChartNotFound,
    NetworkError,
    SongNotFound,
)
from chuni_penguin.calculation import calculate_rating
from chuni_penguin.database import Chart, Song
from chuni_penguin.logging import logger
from chuni_penguin.services.song_details import NetworkChoice, SongDetailsData
from chuni_penguin.types import Difficulty, LeaderboardEntry
from chuni_penguin.utils import sdvxin_link, yt_search_link

from ._base import MessageKwargs, PenguinView
from ._embed_pages import embed_pages as _embed_pages
from .components.chart_card_embed import ChartCardEmbed
from .components.score_card_embed import ScoreCardEmbed
from .processing import processing_embed, run_with_processing
from .song_info import SongInfoEmbed
from .song_labels import availability_label, record_label

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext

Tab = Literal[
    "overview",
    "chart",
    "scores",
    "leaderboard",
    "rating",
    "reviews",
    "view",
    "jacket",
    "links",
    "rating_table",
    "borders",
]
TABS: tuple[tuple[Tab, str], ...] = (
    ("overview", "譜面詳情"),
    ("chart", "音符明細"),
    ("scores", "分數"),
    ("leaderboard", "排行榜"),
    ("rating", "成績分佈"),
    ("reviews", "評價／社群評價"),
    ("view", "視譜"),
    ("jacket", "曲繪"),
    ("links", "相關連結"),
    ("rating_table", "Rating 試算"),
    ("borders", "容錯／失分"),
)
COMMON_TABS = ()


@dataclass(frozen=True)
class SongDetailsState:
    song_index: int = 0
    tab: Tab = "overview"
    difficulty: str = "MAS"
    network: NetworkChoice = "default"
    page: int = 0
    mirror: bool = False
    review_group: str = "overall"


@dataclass
class SongDetailsPage:
    state: SongDetailsState
    charts: Sequence[Chart]
    pages: list[list[discord.Embed]]
    image: bytes | None = None
    filename: str = "chart.jpg"
    review_summary: dict | None = None
    retryable: bool = False


def _value(value: object) -> str:
    return "未知" if value is None else str(value)


class SongTabButton(discord.ui.Button["SongDetailsView"]):
    def __init__(self, tab: Tab, label: str, *, row: int, selected: bool):
        super().__init__(
            label=label,
            custom_id=f"song_details:tab:{tab}",
            row=row,
            style=discord.ButtonStyle.primary
            if selected
            else discord.ButtonStyle.secondary,
        )
        self.tab = tab

    @override
    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        await self.view.transition(interaction, tab=self.tab, page=0)


class SongDifficultySelect(discord.ui.Select["SongDetailsView"]):
    def __init__(
        self,
        charts: Sequence[Chart],
        selected: str,
        *,
        songs=(),
        variant_charts=(),
        song_index=0,
    ):
        selected_song = songs[song_index] if songs else None
        options = [
            discord.SelectOption(
                label=(
                    f"WORLD'S END · {chart.level}"
                    if chart.difficulty == "WE"
                    else f"一般 {Difficulty(chart.difficulty)} · {chart.level}"
                )[:100],
                value=chart.difficulty,
                description=(
                    (
                        ""
                        if chart.difficulty == "WE"
                        else f"定數 {_value(chart.const)} · "
                    )
                    + f"收錄版本 {selected_song.version} · {availability_label(selected_song)}"
                    if selected_song is not None
                    else f"定數 {_value(chart.const)}"
                )[:100],
                default=chart.difficulty == selected,
            )
            for chart in charts
        ]
        if variant_charts:
            positions = {song.id: index for index, song in enumerate(songs)}
            metadata = {song.id: song for song in songs}
            options = []
            for variant in variant_charts:
                song = metadata[variant.song_id]
                is_we = variant.difficulty == "WE"
                status = availability_label(song)
                options.append(
                    discord.SelectOption(
                        label=(
                            f"WORLD'S END · {variant.level}"
                            if is_we
                            else f"一般 {Difficulty(variant.difficulty)} · {variant.level}"
                        )[:100],
                        value=f"{positions[song.id]}:{variant.difficulty}",
                        description=(
                            f"收錄版本 {song.version} · {status}"
                            if is_we
                            else f"定數 {_value(variant.const)} · 收錄版本 {song.version} · {status}"
                        )[:100],
                        default=positions[song.id] == song_index
                        and variant.difficulty == selected,
                    )
                )
        super().__init__(
            placeholder="選擇難度／特殊譜面" if variant_charts else "選擇難度",
            custom_id="song_details:difficulty",
            row=2,
            options=options
            or [discord.SelectOption(label="沒有譜面資料", value="none")],
            disabled=not options,
        )

    @override
    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        value = self.values[0]
        if ":" in value:
            song_index, difficulty = value.split(":", 1)
            await self.view.transition(
                interaction, song_index=int(song_index), difficulty=difficulty, page=0
            )
        else:
            await self.view.transition(interaction, difficulty=value, page=0)


class SongMoreSelect(discord.ui.Select["SongDetailsView"]):
    def __init__(self, state, available_tabs=None, *, has_results=False):
        options = [
            discord.SelectOption(
                label=label, value="tab:" + tab, default=state.tab == tab
            )
            for tab, label in (
                ("view", "視譜"),
                ("scores", "分數"),
                ("reviews", "評價／社群評價"),
                ("overview", "譜面詳情"),
                ("rating", "成績分佈"),
                ("borders", "容錯／失分"),
            )
        ]
        options.extend(
            (
                discord.SelectOption(label="別名", value="panel:alias"),
                discord.SelectOption(label="找錯歌／重新搜尋", value="action:correct"),
            )
        )
        super().__init__(
            placeholder="選擇單曲功能",
            custom_id="song_details:more",
            row=0,
            options=options,
        )

    async def callback(self, interaction):
        assert self.view is not None
        value = self.values[0]
        if value.startswith("tab:"):
            await self.view.transition(interaction, tab=value[4:], page=0)
        elif value == "action:correct":
            await self.view.correct_song.callback(interaction)
        else:
            from .panel import navigate

            await navigate(self.view.ctx, interaction, value[6:])


class SongPageSelect(discord.ui.Select["SongDetailsView"]):
    """Navigation is a select too; Close is the card's only button."""

    def __init__(self, view, rendered):
        options = []
        if view.state.page > 0:
            options.append(discord.SelectOption(label="上一頁", value="previous_page"))
        if view.state.page + 1 < len(rendered.pages):
            options.append(discord.SelectOption(label="下一頁", value="next_page"))
        if not view.variant_charts and view.state.song_index > 0:
            options.append(discord.SelectOption(label="上一首", value="previous_song"))
        if not view.variant_charts and view.state.song_index + 1 < len(view.songs):
            options.append(discord.SelectOption(label="下一首", value="next_song"))
        super().__init__(placeholder="切換頁次／歌曲", row=1, options=options)

    async def callback(self, interaction):
        action = self.values[0]
        if action.endswith("song"):
            await self.view.transition(
                interaction,
                song_index=self.view.state.song_index
                + (1 if action == "next_song" else -1),
                page=0,
            )
        else:
            await self.view.transition(
                interaction,
                page=self.view.state.page + (1 if action == "next_page" else -1),
            )


class SongNetworkSelect(discord.ui.Select["SongDetailsView"]):
    def __init__(self, state: SongDetailsState):
        choices: list[tuple[str, NetworkChoice]] = [
            ("CHUNITHM-NET International", "chunithm_net"),
        ]
        if state.tab == "scores":
            choices.extend(
                [
                    ("Chunirec jp2", "chunirec:jp2"),
                    ("Chunirec jp", "chunirec:jp"),
                    ("Chunilib 匯出檔", "chunilib:export"),
                ]
            )
        selected = state.network
        if state.tab == "leaderboard" and selected == "default":
            selected = "chunithm_net"
        if state.tab == "leaderboard" and selected not in (
            "chunithm_net",
            "kamaitachi",
        ):
            selected = "chunithm_net"
        super().__init__(
            placeholder="選擇成績來源",
            custom_id="song_details:network",
            row=4,
            options=[
                discord.SelectOption(
                    label=label, value=value, default=value == selected
                )
                for label, value in choices
            ],
        )

    @override
    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        await self.view.transition(
            interaction, network=cast(NetworkChoice, self.values[0]), page=0
        )


class SongDetailsView(PenguinView["PenguinContext"]):
    """One song, all available information, in one message."""

    _panel_own_navigation = True

    def __init__(
        self,
        ctx: "PenguinContext",
        items: Sequence[Song],
        *,
        detailed: bool = False,
        synthesis_alt_jacket: str | None = None,
        fun_mode: bool = False,
        data: SongDetailsData | None = None,
        initial_tab: Tab | None = None,
        difficulty: str = "MAS",
        network: NetworkChoice = "default",
        mirror: bool = False,
        variant_charts: Sequence[Chart] = (),
    ):
        if not items:
            msg = "Song details requires at least one search result."
            raise ValueError(msg)
        super().__init__(ctx, timeout=600)
        self.message = None
        self.songs = tuple(items)
        self.variant_charts = tuple(variant_charts)
        self.state = SongDetailsState(
            tab=initial_tab or ("chart" if detailed else "overview"),
            difficulty=difficulty,
            network=network,
            mirror=mirror,
        )
        self.data = data or SongDetailsData(ctx)
        self.synthesis_alt_jacket = synthesis_alt_jacket
        self.fun_mode = fun_mode
        self._revision = 0
        self._edit_lock = asyncio.Lock()
        self._expired = False

    def _info_embed(self, song: Song, charts: Sequence[Chart]) -> SongInfoEmbed:
        embed = SongInfoEmbed(
            song,
            charts,
            synthesis_alt_jacket=self.synthesis_alt_jacket,
            fun_mode=self.fun_mode,
        )
        if self.variant_charts:
            labels = []
            for item in sorted(
                self.songs, key=lambda item: (item.genre == "WORLD'S END", item.id)
            ):
                item_charts = [
                    chart for chart in self.variant_charts if chart.song_id == item.id
                ]
                label = record_label(item, item_charts)
                labels.append(("▶ " if item.id == song.id else "• ") + label)
            embed.add_field(
                name="譜面版本", value="\n".join(labels)[:1024], inline=False
            )
        return embed

    def _notice(self, song: Song, text: str) -> discord.Embed:
        return discord.Embed(
            title=song.title[:256], description=text, color=discord.Color.yellow()
        )

    async def add_community_annotations(self, embed, chart, *, include_semantic=True):
        extras = getattr(self.ctx.bot, "phase1_charts", None)
        if extras is not None:
            manual = await extras.annotations_for(chart)
            for row in manual:
                details = []
                if row["tags"]:
                    details.append("標籤：" + ", ".join(row["tags"]))
                if row["sss_stars"] is not None:
                    details.append("來源 SSS 星數：" + "★" * row["sss_stars"])
                if row["note"]:
                    details.append(escape_markdown(row["note"]))
                for link in row["links"][:3]:
                    label = escape_markdown(link["label"] or "來源連結")
                    url = link["url"].replace(")", "%29")
                    details.append(f"[{label}]({url})")
                method = (
                    "Luna 核對"
                    if row["match_method"] == "agent_reviewed"
                    else "自動配對"
                )
                details.append(
                    f"來源：Poopenguin · {method} · {row['sheet']} 第 {row['excel_row']} 列"
                )
                embed.add_field(
                    name="社群譜面資料",
                    value="\n".join(details)[:1024],
                    inline=False,
                )
            for row in extras.semantic_for(chart) if include_semantic else ():
                embed.add_field(
                    name="LLM 語義（來源：" + str(row["source"])[:100] + "）",
                    value=str(row.get("summary") or "沒有文字描述")[:1024],
                    inline=False,
                )

    async def render(self, state: SongDetailsState) -> SongDetailsPage:
        song = self.songs[state.song_index]
        charts = await self.data.charts(song)
        chart = next((c for c in charts if c.difficulty == state.difficulty), None)
        if chart is None and charts:
            chart = next((c for c in charts if c.difficulty == "MAS"), charts[-1])
            state = replace(state, difficulty=chart.difficulty)

        image = None
        if state.tab == "overview":
            pages = _embed_pages(self._info_embed(song, charts))
        elif state.tab == "jacket":
            embed = self._info_embed(song, ())
            embed.description = "曲繪"
            # Respect the same alternative-jacket setting as the overview.
            if embed.thumbnail.url:
                embed.set_image(url=embed.thumbnail.url)
                embed.set_thumbnail(url=None)
            else:
                embed.description = "此歌曲目前沒有可用的曲繪。"
            pages = _embed_pages(embed)
        elif state.tab == "links":
            links = [
                f"[歌曲 Wiki](https://wikiwiki.jp/chunithmwiki/{quote(song.wikiwiki_title or song.title, safe='')})",
                f"[搜尋影片]({yt_search_link(song.title, state.difficulty)})",
            ]
            if chart is not None and chart.sdvxin_chart_view is not None:
                links.append(f"[sdvx.in 視譜]({sdvxin_link(chart.sdvxin_chart_view)})")
            jacket = self._info_embed(song, ()).thumbnail.url
            if jacket:
                links.append(f"[開啟曲繪]({jacket})")
            pages = _embed_pages(self._notice(song, "\n\n".join(links)))
        elif chart is None:
            pages = [[self._notice(song, "此歌曲目前沒有譜面資料。")]]
        elif state.tab == "chart":
            embed = ChartCardEmbed(
                chart, synthesis_alt_jacket=self.synthesis_alt_jacket
            )
            embed.description = (
                f"**Artist**: {escape_markdown(song.artist)}\n"
                f"**NOTES DESIGNER**: {escape_markdown(_value(chart.charter))}\n"
                f"**Version**: {_value(chart.version or song.version)}\n\n"
                f"**CHAIN**: {_value(chart.maxcombo)}\n"
                f"**TAP**: {_value(chart.tap)}\n"
                f"**HOLD**: {_value(chart.hold)}\n"
                f"**SLIDE**: {_value(chart.slide)}\n"
                f"**AIR**: {_value(chart.air)}\n"
                f"**FLICK**: {_value(chart.flick)}"
            )
            await self.add_community_annotations(embed, chart)
            pages = _embed_pages(embed)
        elif state.tab == "reviews":
            from chuni_penguin.services.chart_reviews import ChartReviewService

            from .chart_reviews import summary_embed

            summary = await ChartReviewService(self.ctx.bot.database).public_summary(
                chart.id
            )
            if state.review_group != "overall" and not summary.get("groups", {}).get(
                state.review_group
            ):
                state = replace(state, review_group="overall")
            embed = summary_embed(chart, summary, state.review_group)
            await self.add_community_annotations(embed, chart, include_semantic=False)
            pages = _embed_pages(embed)
            return SongDetailsPage(state, charts, pages, review_summary=summary)
        elif state.tab in ("rating_table", "borders"):
            embed = ChartCardEmbed(
                chart,
                border=state.tab == "borders",
                synthesis_alt_jacket=self.synthesis_alt_jacket,
            )
            if state.tab == "rating_table" and chart.difficulty == "WE":
                embed.add_field(
                    name="Rating", value="WORLD'S END 不計入 Rating。", inline=False
                )
            elif state.tab == "rating_table" and chart.const is None:
                embed.add_field(
                    name="Rating", value="定數未知，無法計算單曲 Rating。", inline=False
                )
            elif state.tab == "rating_table":
                targets = (
                    ("S", 975000),
                    ("S+", 990000),
                    ("SS", 1000000),
                    ("SS+", 1005000),
                    ("SSS", 1007500),
                    ("SSS+", 1009000),
                    ("MAX", 1010000),
                )
                embed.add_field(
                    name="目標分數 → 單曲 Rating",
                    value="\n".join(
                        f"{rank} · {score:,} → **{calculate_rating(score, chart.const):.2f}**"
                        for rank, score in targets
                    ),
                    inline=False,
                )
            if state.tab == "borders" and not chart.maxcombo:
                embed.add_field(
                    name="容錯", value="總物量未知，無法計算容錯與失分。", inline=False
                )
            embed.description = (
                "各目標分數的單曲 Rating。"
                if state.tab == "rating_table"
                else "各目標分數的一組 JUSTICE／ATTACK／MISS 容錯組合。"
            )
            for index, field in enumerate(embed.fields):
                embed.set_field_at(
                    index, name=field.name, value=field.value, inline=False
                )
            pages = _embed_pages(embed)
        elif state.tab == "rating":
            from chuni_penguin.services.distribution_source import (
                DistributionUnavailable,
            )

            embed = discord.Embed(
                title=song.title[:256],
                color=Difficulty(chart.difficulty).color(),
                description="來源樣本的分數分布與群體推估。",
            )
            embed.set_author(
                name=f"{Difficulty(chart.difficulty)} · {chart.level} · 定數 {_value(chart.const)}"
            )
            extras = getattr(self.ctx.bot, "phase1_charts", None)
            if extras is not None:
                try:
                    result = await extras.analysis(chart)
                except DistributionUnavailable as error:
                    result = None
                    embed.add_field(
                        name="暫時無法顯示",
                        value=str(error),
                        inline=False,
                    )
                except Exception as error:
                    result = None
                    await logger.awarning(
                        "Score distribution failed",
                        tag="distribution_error",
                        error_type=type(error).__name__,
                    )
                    embed.add_field(
                        name="產圖暫時失敗",
                        value="請稍後重新選擇「成績分佈」重試，或先切換難度。",
                        inline=False,
                    )
                if result is not None:
                    origin, image = result
                    embed.set_image(url="attachment://score-distribution.png")
                    embed.add_field(
                        name="樣本與日期",
                        value=f"**{origin['players']:,} 位玩家**\n擷取：{origin['observed_at']}",
                        inline=False,
                    )
                    if origin.get("source_kind") == "bundled":
                        embed.add_field(
                            name="備用資料",
                            value="目前使用隨附的離線快照；資料日期如上。",
                            inline=False,
                        )
                    embed.add_field(
                        name="統計來源",
                        value=f"[Chunirec 原始統計]({origin['source_url']})",
                        inline=False,
                    )
                    source_constant = origin.get("constant")
                    if source_constant is not None and str(source_constant) != str(
                        chart.const
                    ):
                        embed.add_field(
                            name="統計使用的定數",
                            value=f"Chunirec：{source_constant}；本機曲庫：{_value(chart.const)}。圖表依統計來源產生。",
                            inline=False,
                        )
                    embed.add_field(
                        name="閱讀這張圖",
                        value="沿用 C 的既有模型。網站樣本有選擇偏差，推估受樣本量與模型限制，不代表玩家個人的實測能力。來源未提供完整區域／遊戲版本。",
                        inline=False,
                    )
                elif not embed.fields:
                    embed.add_field(
                        name="C 統計",
                        value="目前沒有可用的統計資料，請稍後重試或切換難度。",
                        inline=False,
                    )
            else:
                embed.add_field(
                    name="統計資料", value="目前沒有可讀取的統計來源。", inline=False
                )
            pages = _embed_pages(embed)
            return SongDetailsPage(
                state, charts, pages, image, filename="score-distribution.png"
            )
        elif state.tab == "scores":
            result = await self.data.scores(song, state.network)
            records = [
                r
                for r in result.records
                if r.song.id == song.id
                and r.chart.difficulty == Difficulty(state.difficulty)
            ]
            pages = []
            for record in records:
                embed = ScoreCardEmbed(
                    record,
                    detailed=True,
                    synthesis_alt_jacket=self.synthesis_alt_jacket,
                )
                embed.title = "我的成績"
                original_footer = embed.footer.text or ""
                embed.set_footer(
                    text=f"{result.network} · {original_footer}".rstrip(" ·")
                )
                pages.extend(_embed_pages(embed))
            if not pages:
                pages = [
                    [self._notice(song, f"{result.network} 尚無此難度的個人成績。")]
                ]
        elif state.tab == "leaderboard":
            result = await self.data.leaderboard(chart, state.network)
            pages = []
            for start in range(0, len(result.leaderboard.ranking), 10):
                rows = result.leaderboard.ranking[start : start + 10]
                embed = self._notice(
                    song, "\n".join(self._ranking_row(row) for row in rows)
                )
                embed.set_author(
                    name=f"{Difficulty(chart.difficulty)} · {result.network}"
                )
                embed.timestamp = result.leaderboard.updated_at
                pages.extend(_embed_pages(embed))
            if not pages:
                pages = [[self._notice(song, f"{result.network} 目前沒有排行榜紀錄。")]]
        else:  # chart view
            embed = ChartCardEmbed(
                chart, synthesis_alt_jacket=self.synthesis_alt_jacket
            )
            if chart.sdvxin_chart_view is None:
                embed.description = (
                    "此難度尚無視譜資料。可從上方功能選單查看譜面詳情或其他難度。"
                )
            else:
                image = await self.data.chart_image(chart, mirror=state.mirror)
                embed.set_image(url="attachment://chart.jpg")
                embed.description = f"{'MIRROR · ' if state.mirror else ''}[視譜來源：sdvx.in]({sdvxin_link(chart.sdvxin_chart_view)})"
            pages = _embed_pages(embed)
        return SongDetailsPage(state, charts, pages, image)

    @staticmethod
    def _ranking_row(row: LeaderboardEntry) -> str:
        text = f"**{row.position}.** {escape_markdown(row.player_name)} · **{row.score:,}**"
        lamps = [
            str(lamp) for lamp in (row.clear_lamp, row.combo_lamp) if lamp is not None
        ]
        if lamps:
            text += f" · {' / '.join(lamps)}"
        if row.judgements is not None:
            j = row.judgements
            text += f" · {j.justice_critical}/{j.justice}/{j.attack}/{j.miss}"
        if row.ajc_count is not None:
            text += f" · AJC {row.ajc_count}"
        if row.achieved_at is not None:
            text += f" · {format_dt(row.achieved_at, 'f')}"
        return text

    async def _safe_render(
        self, state: SongDetailsState, *, recover_unavailable=False
    ) -> SongDetailsPage:
        try:
            notice = None
            if state.network == "default":
                state = replace(state, network="chunithm_net")
            if state.tab == "leaderboard" and state.network not in (
                "default",
                "chunithm_net",
                "kamaitachi",
            ):
                state = replace(state, network="chunithm_net")
            if recover_unavailable:
                charts = await self.data.charts(self.songs[state.song_index])
                selected = next(
                    (chart for chart in charts if chart.difficulty == state.difficulty),
                    None,
                )
                selected = selected or next(
                    (chart for chart in charts if chart.difficulty == "MAS"),
                    charts[-1] if charts else None,
                )
                unsupported = (
                    selected is None
                    and state.tab not in ("overview", "links", "jacket")
                    or selected is not None
                    and (
                        state.tab == "view"
                        and selected.sdvxin_chart_view is None
                        or state.tab == "rating_table"
                        and (selected.const is None or selected.difficulty == "WE")
                        or state.tab == "borders"
                        and not selected.maxcombo
                        or state.tab == "reviews"
                        and selected.id is None
                    )
                )
                if unsupported:
                    notice = (
                        f"目前難度沒有「{dict(TABS)[state.tab]}」資料，已返回譜面詳情。"
                    )
                    state = replace(state, tab="overview")
            rendered = await self.render(state)
            if notice:
                rendered.pages[0][0].add_field(
                    name="分頁切換", value=notice, inline=False
                )
            return rendered
        except (AuthenticationError, commands.CommandError):
            if state.tab == "scores":
                text = "無法讀取此來源的成績。請先從 /account 連結帳號，並確認歌曲在該來源可用。"
            elif state.tab == "leaderboard":
                text = "目前無法讀取此來源的排行榜。可切換來源，或請管理員確認 Bot 的帳號設定。"
            else:
                text = "暫時無法讀取這個分頁，請再按一次分頁重試。"
        except (SongNotFound, ChartNotFound):
            text = "此來源沒有這首歌曲或難度的資料。"
        except (NetworkError, httpx.HTTPError, TimeoutError):
            text = "資料來源暫時無法回應，請再按一次分頁重試；其他分頁仍可使用。"
        except NotImplementedError:
            text = "此來源目前不支援這項資料，請從選單切換來源或功能。"
        except Exception as error:  # noqa: BLE001 - isolate a failed tab; never expose remote response/credentials
            await logger.awarning(
                "Song details page failed",
                tag="song_details_error",
                error_type=type(error).__name__,
            )
            text = "此分頁暫時無法顯示，請再試一次或切換其他分頁。"
        song = self.songs[state.song_index]
        # Preserve an already-loaded difficulty menu even when a remote page fails.
        charts = self.data.cached_charts(song)
        if charts and state.difficulty not in {c.difficulty for c in charts}:
            chart = next((c for c in charts if c.difficulty == "MAS"), charts[-1])
            state = replace(state, difficulty=chart.difficulty)
        return SongDetailsPage(
            state, charts, [[self._notice(song, text)]], retryable=True
        )

    def _controls(self, rendered: SongDetailsPage):
        self.clear_items()
        chart = next(
            (c for c in rendered.charts if c.difficulty == self.state.difficulty), None
        )
        from .panel import session_for

        session = session_for(self.ctx)
        self.add_item(
            SongMoreSelect(
                self.state,
                (),
                has_results=session.chart_results is not None,
            )
        )
        session.song_card = dict(
            songs=self.songs,
            state=self.state,
            variant_charts=self.variant_charts,
            search_query=getattr(
                self, "search_query", self.songs[self.state.song_index].title
            ),
        )
        from .panel import PanelRoute

        session.route = PanelRoute(
            "score" if self.state.tab == "scores" else "chart",
            self.songs[self.state.song_index].title,
            "net"
            if self.state.network in ("default", "chunithm_net")
            else self.state.network,
            self.state.difficulty,
        )
        session.active_view = self
        self.add_item(
            SongDifficultySelect(
                rendered.charts,
                self.state.difficulty,
                songs=self.songs,
                variant_charts=self.variant_charts,
                song_index=self.state.song_index,
            )
        )
        if self.state.tab == "reviews" and chart is not None:
            from .chart_reviews import ReviewEntrySelect

            self.add_item(
                ReviewEntrySelect(
                    chart,
                    self.ctx,
                    source=self.state.network,
                    summary=rendered.review_summary,
                    selected=self.state.review_group,
                )
            )
        if len(rendered.pages) > 1 or len(self.songs) > 1 and not self.variant_charts:
            self.add_item(SongPageSelect(self, rendered))
        if self.state.tab in ("scores", "leaderboard"):
            self.add_item(SongNetworkSelect(self.state))
        from .dismiss import ensure_close

        ensure_close(self, self.ctx.author.id, ctx=self.ctx)

    def _kwargs(self, rendered: SongDetailsPage, *, editing: bool) -> dict:
        self.state = replace(
            rendered.state, page=min(rendered.state.page, len(rendered.pages) - 1)
        )
        self._controls(rendered)
        embeds = [embed.copy() for embed in rendered.pages[self.state.page]]
        footer = embeds[-1].footer.text or ""
        label = dict(TABS)[self.state.tab]
        selected_song = self.songs[self.state.song_index]
        variant = record_label(selected_song, rendered.charts)
        if len(self.songs) > 1 and not self.variant_charts:
            variant = f"歌曲 {self.state.song_index + 1}/{len(self.songs)} · {variant}"
        navigation = (
            f"{variant} · {label} · {self.state.page + 1}/{len(rendered.pages)}"
        )
        embeds[-1].set_footer(text=f"{footer}\n{navigation}".strip())
        files = []
        if rendered.image is not None:
            files.append(
                discord.File(BytesIO(rendered.image), filename=rendered.filename)
            )
        return {
            "content": None,
            "embeds": embeds,
            "attachments" if editing else "files": files,
        }

    async def transition(self, interaction: discord.Interaction, **changes):
        if self._expired:
            await interaction.response.send_message(
                "資訊卡已逾時，請重新搜尋。", ephemeral=True
            )
            return
        self._revision += 1
        revision = self._revision
        self.state = replace(self.state, **changes)
        state = self.state
        await interaction.response.defer()

        async def show_processing():
            async with self._edit_lock:
                if self._expired or revision != self._revision:
                    return
                try:
                    await self.edit_message(
                        interaction,
                        content=None,
                        embeds=[processing_embed(dict(TABS)[state.tab])],
                    )
                except discord.HTTPException:
                    pass

        rendered = await run_with_processing(
            self._safe_render(
                state,
                recover_unavailable="difficulty" in changes or "song_index" in changes,
            ),
            show_processing,
        )
        async with self._edit_lock:
            if self._expired or revision != self._revision:
                return
            kwargs = self._kwargs(rendered, editing=True)
            try:
                await self.edit_message(interaction, **kwargs)
            finally:
                for file in kwargs["attachments"]:
                    file.close()

    @override
    async def _before_start(self, *, content: str | None = None) -> MessageKwargs:
        async def show_processing():
            if hasattr(self.ctx, "respond_or_edit"):
                try:
                    await self.ctx.respond_or_edit(
                        embed=processing_embed("載入歌曲"), view=None
                    )
                except discord.HTTPException:
                    pass

        rendered = (
            await self._safe_render(self.state)
            if getattr(self.ctx, "_processing_response_active", False)
            else await run_with_processing(
                self._safe_render(self.state), show_processing
            )
        )
        kwargs = self._kwargs(rendered, editing=False)
        kwargs["content"] = content
        return cast(MessageKwargs, kwargs)

    @override
    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        # An owner's access to other menus must not allow fetching the requester's scores.
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message(
            "請自行搜尋歌曲以使用自己的資訊卡。", ephemeral=True
        )
        return False

    @override
    async def on_timeout(self):
        self._expired = True
        self._revision += 1
        await self.data.close()
        async with self._edit_lock:
            await super().on_timeout()
            if self.message is not None:
                try:
                    await self.message.edit(
                        content=f"歌曲面板已逾時，請重新開啟 /chart 搜尋「{self.songs[self.state.song_index].title[:100]}」。",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass

    async def deactivate(self):
        """A late tab load must not overwrite a different panel group."""
        self._expired = True
        self._revision += 1
        await self.data.close()
        self.stop()

    @discord.ui.button(label="找錯歌／重搜", style=discord.ButtonStyle.danger, row=0)
    async def correct_song(self, interaction, button):
        from .panel import component_context, defer_panel, retire_previous, session_for
        from .processing import processing_response
        from .song_search import discover_song

        query = getattr(self, "search_query", self.songs[self.state.song_index].title)
        updated = component_context(self.ctx, interaction)
        session = session_for(updated)
        session.route = replace(session.route, query=query)
        await defer_panel(updated, interaction)
        await retire_previous(updated)
        async with processing_response(updated, "重新比對歌曲"):
            await discover_song(
                updated,
                query,
                tab=self.state.tab,
                correction=True,
                exclude_song_id=self.songs[self.state.song_index].id,
                excluded_song_ids=tuple(song.id for song in self.songs)
                if self.variant_charts
                else (),
            )

    @discord.ui.button(label="重試此頁", row=4)
    async def retry_page(self, interaction, button):
        await self.transition(interaction)

    @discord.ui.button(label="上一首", row=3)
    async def previous_song(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await self.transition(
            interaction, song_index=max(self.state.song_index - 1, 0), page=0
        )

    @discord.ui.button(label="歌曲 1/1", row=3, disabled=True)
    async def song_position(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        pass

    @discord.ui.button(label="下一首", row=3)
    async def next_song(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.transition(
            interaction,
            song_index=min(self.state.song_index + 1, len(self.songs) - 1),
            page=0,
        )

    @discord.ui.button(label="上一頁", row=3)
    async def previous_page(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await self.transition(interaction, page=max(self.state.page - 1, 0))

    @discord.ui.button(label="下一頁", row=3)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.transition(interaction, page=self.state.page + 1)

    @discord.ui.button(label="MIRROR", row=1)
    async def mirror_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await self.transition(interaction, mirror=not self.state.mirror, page=0)
