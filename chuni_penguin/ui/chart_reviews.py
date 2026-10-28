# ruff: noqa: RUF001
"""Anonymous aggregates and private reviews with automatically loaded background."""

import discord

from chuni_penguin.services.chart_reviews import (
    DIFFICULTY_LABELS,
    EXPERIENCE_BUCKETS,
    FAMILIARITIES,
    GOALS,
    PRIMARY_LEVELS,
    RATING_BUCKETS,
    SKILLS,
    ChartReviewService,
    _profile,
)
from chuni_penguin.utils.chunithm_i18n import ui_text

from .dismiss import CloseView, dismiss_message, ensure_close

GROUP_LABELS = {
    "rating_bucket": "依 Rating 區間",
    "primary_level": "依主力等級",
    "experience_bucket": "依遊玩經驗",
}


def summary_embed(chart, summary, dimension="overall", ctx=None):
    selected = dimension
    embed = discord.Embed(
        title=ui_text(ctx, "{0} · 玩家評價", chart.song.title)[:256],
        description=ui_text(
            ctx, "{0} · 等級 {1}\n匿名統計", chart.difficulty, chart.level
        ),
    )
    if not summary["available"]:
        embed.add_field(
            name=ui_text(ctx, "尚無評價"),
            value=ui_text(ctx, "從下方「撰寫／管理評價」選單留下第一份評價。"),
            inline=False,
        )
    else:
        embed.add_field(
            name=ui_text(ctx, "整體看法"),
            value=ui_text(
                ctx,
                "{0} 份評價 · 平均喜好 {1:.2f} / 5",
                summary["count"],
                summary["enjoyment"],
            ),
            inline=False,
        )
        embed.add_field(
            name=ui_text(ctx, "體感難度"),
            value="\n".join(
                f"{ui_text(ctx, DIFFICULTY_LABELS[int(key)])}：{count}"
                for key, count in sorted(summary["difficulty"].items())
            ),
            inline=False,
        )
        for dimension, groups in summary["groups"].items():
            if groups and dimension == selected:
                embed.add_field(
                    name=ui_text(ctx, GROUP_LABELS[dimension]),
                    value="\n".join(
                        ui_text(
                            ctx,
                            "{0}：{1} 人 · 喜好 {2:.2f}/5",
                            ui_text(ctx, group["label"]),
                            group["count"],
                            group["enjoyment"],
                        )
                        + (
                            ui_text(ctx, " · 體感 ")
                            + "／".join(
                                f"{ui_text(ctx, DIFFICULTY_LABELS[int(key)])} {count}"
                                for key, count in sorted(
                                    group.get("difficulty", {}).items()
                                )
                            )
                            if group.get("difficulty")
                            else ""
                        )
                        for group in groups
                    )[:1024],
                    inline=False,
                )
    embed.set_footer(
        text=ui_text(ctx, "帳號與文字評論不公開 · 每人每張譜面一份，可修改或刪除")
    )
    return embed


class ReviewEntryButton(discord.ui.Button):
    def __init__(self, chart, ctx, *, source="default"):
        super().__init__(
            label=ui_text(ctx, "新增／管理我的評價"),
            style=discord.ButtonStyle.primary,
            row=3,
        )
        self.chart, self.ctx = chart, ctx
        self.source = source

    async def callback(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                ui_text(self.ctx, "請從自己的歌曲面板管理評價。"), ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        service = ChartReviewService(self.ctx.bot.database)
        saved = await service.profile(interaction.user.id)
        from chuni_penguin.services.review_background import load_review_background

        profile = await load_review_background(
            self.ctx, saved=saved, source=self.source
        )
        own = await service.own_review(interaction.user.id, self.chart.id)
        view = ReviewPrivateView(
            service, interaction.user.id, self.chart, profile, own, ctx=self.ctx
        )
        await interaction.edit_original_response(embed=view.embed(), view=view)


class ReviewEntrySelect(discord.ui.Select):
    def __init__(
        self, chart, ctx, *, source="default", summary=None, selected="overall"
    ):
        options = [
            discord.SelectOption(
                label=ui_text(ctx, "新增／管理我的評價"), value="write"
            )
        ]
        if any((summary or {}).get("groups", {}).values()):
            options.append(
                discord.SelectOption(
                    label=ui_text(ctx, "整體統計"),
                    value="overall",
                    default=selected == "overall",
                )
            )
            options.extend(
                discord.SelectOption(
                    label=ui_text(ctx, GROUP_LABELS[key]),
                    value=key,
                    default=selected == key,
                )
                for key, groups in summary["groups"].items()
                if groups
            )
        super().__init__(
            placeholder=ui_text(ctx, "撰寫／管理評價"),
            row=3,
            options=options,
        )
        self.chart, self.ctx, self.source = chart, ctx, source

    async def callback(self, interaction):
        if self.values[0] == "write":
            await ReviewEntryButton(self.chart, self.ctx, source=self.source).callback(
                interaction
            )
        else:
            await self.view.transition(interaction, review_group=self.values[0], page=0)


class ReviewGroupSelect(discord.ui.Select):
    def __init__(self, summary, selected, ctx=None):
        options = [
            discord.SelectOption(
                label=ui_text(ctx, "整體"),
                value="overall",
                default=selected == "overall",
            )
        ]
        options.extend(
            discord.SelectOption(
                label=ui_text(ctx, GROUP_LABELS[key]),
                value=key,
                default=selected == key,
            )
            for key, groups in summary.get("groups", {}).items()
            if groups
        )
        super().__init__(
            placeholder=ui_text(ctx, "查看不同玩家群體"), row=4, options=options
        )

    async def callback(self, interaction):
        await self.view.transition(interaction, review_group=self.values[0], page=0)


class ReviewPrivateView(discord.ui.View):
    def __init__(
        self, service, owner_id, chart, profile=None, own=None, *, draft=None, ctx=None
    ):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.service, self.owner_id, self.chart = service, owner_id, chart
        self.profile = profile or {
            "rating_bucket": "未提供",
            "primary_level": "未提供",
            "experience_bucket": "未提供",
            "strengths": [],
            "weaknesses": [],
        }
        self.own, self.draft = own, _profile(draft or self.profile)
        self.continue_review.label = (
            ui_text(ctx, "修改譜面評價") if own else ui_text(ctx, "填寫譜面評價")
        )
        self.clear_items()
        self.add_item(self.continue_review)
        self.add_item(self.background)
        if own is not None:
            self.add_item(self.remove)
        self.add_item(self.close)

        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label:
                item.label = ui_text(ctx, item.label)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            ui_text(self.ctx, "只能管理自己的評價。"), ephemeral=True
        )
        return False

    def embed(self):
        embed = discord.Embed(
            title=ui_text(self.ctx, "我的匿名評價"),
            description=ui_text(
                self.ctx,
                "**{0} [{1}]**\n評論與背景明細僅本人可見；公開僅顯示匿名統計。",
                discord.utils.escape_markdown(self.chart.song.title),
                self.chart.difficulty,
            ),
        )
        if self.own:
            embed.add_field(
                name=ui_text(self.ctx, "目前評價"),
                value=ui_text(
                    self.ctx,
                    "喜好 {0}/5 · {1}\n{2}",
                    self.own["enjoyment"],
                    ui_text(
                        self.ctx, DIFFICULTY_LABELS[self.own["perceived_difficulty"]]
                    ),
                    discord.utils.escape_markdown(self.own["comment"]),
                )[:1024],
                inline=False,
            )
        details = [
            f"{label} {ui_text(self.ctx, self.draft[key])}"
            for label, key in (
                ("Rating", "rating_bucket"),
                ("主力", "primary_level"),
                ("經驗", "experience_bucket"),
            )
            if self.draft[key] != "未提供"
        ]
        embed.add_field(
            name=ui_text(self.ctx, "本次玩家資料"),
            value=" · ".join(details)
            if details
            else ui_text(self.ctx, "暫無可用玩家資料，仍可直接填寫評價。"),
            inline=False,
        )
        return embed

    @discord.ui.button(label="補充／更新玩家背景")
    async def background(self, interaction, button):
        await interaction.response.send_modal(ReviewerProfileModal(self))

    @discord.ui.button(label="填寫譜面評價", style=discord.ButtonStyle.primary)
    async def continue_review(self, interaction, button):
        await interaction.response.send_modal(ChartReviewModal(self))

    @discord.ui.button(label="刪除此張評價", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=ui_text(self.ctx, "刪除此張評價？"),
                description=ui_text(
                    self.ctx, "會刪除這張譜面的評價與修改紀錄，並從統計移除。"
                ),
            ),
            view=ReviewDeleteView(self),
        )

    @discord.ui.button(label="關閉")
    async def close(self, interaction, button):
        await dismiss_message(interaction, self, self.owner_id)


def _select(modal, key, label, options, current=None, *, multi=False):
    current = current or []
    if isinstance(current, str):
        current = [current]
    select = discord.ui.Select(
        custom_id=key,
        options=[
            discord.SelectOption(
                label=ui_text(modal.panel.ctx, str(value)),
                value=str(value),
                default=value in current,
            )
            for value in options
        ],
        min_values=0 if multi else 1,
        max_values=len(options) if multi else 1,
        required=not multi,
    )
    modal.add_item(
        discord.ui.Label(text=ui_text(modal.panel.ctx, label), component=select)
    )
    return select


class ReviewerProfileModal(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title=ui_text(panel.ctx, "補充玩家背景（選填）"), timeout=300)
        self.panel = panel
        profile = panel.draft or panel.profile or {}
        self.rating = _select(
            self,
            "rating",
            ui_text(panel.ctx, "Rating 區間（自填）"),
            RATING_BUCKETS,
            profile.get("rating_bucket"),
        )
        self.level = _select(
            self,
            "level",
            ui_text(panel.ctx, "主力等級（穩定遊玩的等級）"),
            PRIMARY_LEVELS,
            profile.get("primary_level"),
        )
        self.experience = _select(
            self,
            "experience",
            ui_text(panel.ctx, "CHUNITHM 遊玩經驗"),
            EXPERIENCE_BUCKETS,
            profile.get("experience_bucket"),
        )
        self.strengths = _select(
            self,
            "strengths",
            ui_text(panel.ctx, "擅長項目（選填）"),
            SKILLS,
            profile.get("strengths"),
            multi=True,
        )
        self.weaknesses = _select(
            self,
            "weaknesses",
            ui_text(panel.ctx, "不擅長項目（選填）"),
            SKILLS,
            profile.get("weaknesses"),
            multi=True,
        )

    async def on_submit(self, interaction):
        if getattr(self.panel, "_embed_closed", False):
            await interaction.response.send_message(
                ui_text(self.panel.ctx, "評價面板已關閉，請重新開啟。"), ephemeral=True
            )
            return
        if interaction.user.id != self.panel.owner_id:
            await interaction.response.send_message(
                ui_text(self.panel.ctx, "只能填寫自己的資料。"), ephemeral=True
            )
            return
        try:
            profile = _profile(
                {
                    "rating_bucket": self.rating.values[0],
                    "primary_level": self.level.values[0],
                    "experience_bucket": self.experience.values[0],
                    "strengths": self.strengths.values,
                    "weaknesses": self.weaknesses.values,
                }
            )
        except (ValueError, IndexError):
            await interaction.response.send_message(
                ui_text(self.panel.ctx, "請完整選擇必填的玩家背景。"), ephemeral=True
            )
            return
        view = ReviewPrivateView(
            self.panel.service,
            self.panel.owner_id,
            self.panel.chart,
            self.panel.profile,
            self.panel.own,
            draft=profile,
            ctx=self.panel.ctx,
        )
        await interaction.response.edit_message(embed=view.embed(), view=view)
        self.panel.stop()


class ChartReviewModal(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title=ui_text(panel.ctx, "評價這張譜面"), timeout=300)
        self.panel = panel
        own = panel.own or {}
        self.enjoyment = _select(
            self,
            "enjoyment",
            ui_text(panel.ctx, "喜好程度（1 最低，5 最高）"),
            tuple(str(v) for v in range(1, 6)),
            str(own["enjoyment"]) if own else None,
        )
        self.difficulty = _select(
            self,
            "difficulty",
            ui_text(panel.ctx, "相對目前定數的體感"),
            tuple(DIFFICULTY_LABELS.values()),
            DIFFICULTY_LABELS[own["perceived_difficulty"]] if own else None,
        )
        self.familiarity = _select(
            self,
            "familiarity",
            ui_text(panel.ctx, "這張譜面的遊玩次數"),
            FAMILIARITIES,
            own.get("familiarity"),
        )
        self.goal = _select(
            self, "goal", ui_text(panel.ctx, "評價時的主要目標"), GOALS, own.get("goal")
        )
        self.comment = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=1000,
            default=own.get("comment", ""),
            placeholder=ui_text(
                panel.ctx, "哪些地方讓你喜歡／卡關？請勿填真名或聯絡方式。"
            ),
        )
        self.add_item(
            discord.ui.Label(
                text=ui_text(panel.ctx, "譜面評論（僅本人可見）"),
                component=self.comment,
            )
        )

    async def on_submit(self, interaction):
        if getattr(self.panel, "_embed_closed", False):
            await interaction.response.send_message(
                ui_text(self.panel.ctx, "評價面板已關閉，請重新開啟。"), ephemeral=True
            )
            return
        if interaction.user.id != self.panel.owner_id:
            await interaction.response.send_message(
                ui_text(self.panel.ctx, "只能提交自己的評價。"), ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            value = {
                "enjoyment": int(self.enjoyment.values[0]),
                "perceived_difficulty": next(
                    key
                    for key, label in DIFFICULTY_LABELS.items()
                    if label == self.difficulty.values[0]
                ),
                "familiarity": self.familiarity.values[0],
                "goal": self.goal.values[0],
                "comment": self.comment.value,
            }
            revision = await self.panel.service.submit(
                interaction.user.id,
                self.panel.chart.id,
                self.panel.draft,
                value,
                expected_revision=self.panel.own["revision"] if self.panel.own else 0,
            )
        except (ValueError, IndexError, StopIteration) as error:
            if getattr(self.panel, "_embed_closed", False):
                return
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title=ui_text(self.panel.ctx, "尚未儲存"),
                    description=ui_text(self.panel.ctx, str(error))
                    if isinstance(error, ValueError)
                    else ui_text(self.panel.ctx, "請完整選擇評價內容。"),
                ),
                view=self.panel,
            )
            return
        if getattr(self.panel, "_embed_closed", False):
            return
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=ui_text(self.panel.ctx, "已儲存匿名評價"),
                description=ui_text(
                    self.panel.ctx,
                    "已保存第 {0} 版及本次玩家資料。\n重新整理玩家評價即可查看統計；帳號與文字評論不公開。",
                    revision,
                ),
            ),
            view=CloseView(self.panel.owner_id, ctx=self.panel.ctx),
        )
        self.panel.stop()


class ReviewDeleteView(discord.ui.View):
    def __init__(self, panel):
        super().__init__(timeout=60)
        self.panel = panel
        ensure_close(self, panel.owner_id, ctx=panel.ctx)

        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label:
                item.label = ui_text(panel.ctx, item.label)

    async def interaction_check(self, interaction):
        return await self.panel.interaction_check(interaction)

    async def deactivate(self):
        self.panel._embed_closed = True
        self.panel.stop()

    @discord.ui.button(label="確認刪除", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        await self.panel.service.delete(interaction.user.id, self.panel.chart.id)
        if getattr(self, "_embed_closed", False):
            return
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=ui_text(self.panel.ctx, "已刪除"),
                description=ui_text(
                    self.panel.ctx, "此張評價與修改紀錄已移除，統計會重新計算。"
                ),
            ),
            view=CloseView(self.panel.owner_id, ctx=self.panel.ctx),
        )
        self.stop()
        self.panel.stop()

    @discord.ui.button(label="取消")
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            embed=self.panel.embed(), view=self.panel
        )
        self.stop()
