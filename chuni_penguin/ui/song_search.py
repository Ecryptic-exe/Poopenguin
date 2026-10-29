# ruff: noqa: RUF001
"""Same-message song selection and a separate owner-only alias confirmation."""

import discord
from discord.ext import commands

from chuni_penguin.services.song_discovery import SongDiscoveryService, search_key
from chuni_penguin.types import Difficulty
from chuni_penguin.ui.owner_view import bind_owner_view, edit_view_message
from chuni_penguin.utils.chunithm_i18n import ui_text

from ._base import PenguinView
from .dismiss import CloseView, dismiss_message
from .song_labels import record_label


def candidate_description(candidate, ctx=None):
    song = candidate.song
    detail = f"{record_label(song, song.charts, ctx=ctx)} · {song.artist}"
    if candidate.variants:
        detail += ui_text(ctx, " · 另有 {0} 個版本", len(candidate.variants))
    return detail


class SongCandidateSelect(discord.ui.Select):
    def __init__(self, result, ctx=None):
        descriptions = [
            candidate_description(candidate, ctx)
            for candidate in result.candidates[:25]
        ]
        keys = [
            (candidate.song.title, description)
            for candidate, description in zip(result.candidates, descriptions)
        ]
        super().__init__(
            placeholder=ui_text(ctx, "選擇你要找的歌曲"),
            row=0,
            options=[
                discord.SelectOption(
                    label=c.song.title[:100],
                    value=str(index),
                    description=(
                        descriptions[index][:72]
                        + ui_text(ctx, " · 編號 {0}", c.song.id)
                        if keys.count(keys[index]) > 1
                        else descriptions[index]
                    )[:100],
                )
                for index, c in enumerate(result.candidates[:25])
            ],
        )

    async def callback(self, interaction):
        await self.view.open_candidate(interaction, int(self.values[0]))


class SongSearchView(PenguinView):
    _panel_own_navigation = True

    def __init__(self, ctx, result, *, tab="overview"):
        super().__init__(ctx, timeout=600)
        self.result, self.tab = result, tab
        self._opening = False
        self.remove_item(self.first)
        self.remove_item(self.retry)
        self.add_item(SongCandidateSelect(result, ctx))
        self.add_item(SongSearchActionSelect(ctx))
        from .dismiss import ensure_close

        ensure_close(self, ctx.author.id, ctx=ctx)

    async def _before_start(self, **kwargs):
        from .panel import session_for

        session_for(self.ctx).active_view = self
        descriptions = [
            ui_text(
                self.ctx, "搜尋：{0}", discord.utils.escape_markdown(self.result.query)
            ),
            ui_text(
                self.ctx, "找到 {0} 個候選，請從下方選擇。", len(self.result.candidates)
            ),
        ]
        for candidate in self.result.candidates[:10]:
            if candidate.variants:
                descriptions.append(
                    ui_text(
                        self.ctx,
                        "**{0}** 可切換：",
                        discord.utils.escape_markdown(candidate.song.title),
                    )
                    + "；".join(
                        discord.utils.escape_markdown(
                            record_label(song, song.charts, ctx=self.ctx)
                        )
                        for song in candidate.variants
                    )
                )
        description = "\n\n".join(descriptions)
        return {
            "content": None,
            "embed": discord.Embed(
                title=ui_text(self.ctx, "你要找的是哪一首？"),
                description=description[:4000],
            ),
        }

    async def open_candidate(self, interaction, index):
        from .panel import component_context, defer_panel

        if self._opening or not 0 <= index < len(self.result.candidates):
            await interaction.response.send_message(
                ui_text(self.ctx, "正在開啟歌曲，請稍候。"), ephemeral=True
            )
            return
        self._opening = True
        try:
            updated = component_context(self.ctx, interaction)
            await defer_panel(updated, interaction)
            await open_song_candidate(updated, self.result, index, tab=self.tab)
            self.stop()
        finally:
            self._opening = False

    async def interaction_check(self, interaction):
        if getattr(self, "_panel_inactive", False):
            await interaction.response.send_message(
                ui_text(self.ctx, "搜尋結果已更新，請使用目前的歌曲面板。"),
                ephemeral=True,
            )
            return False
        return await super().interaction_check(interaction)

    @discord.ui.button(label="就是第一首", style=discord.ButtonStyle.primary, row=1)
    async def first(self, interaction, button):
        await self.open_candidate(interaction, 0)

    @discord.ui.button(label="重新輸入", row=1)
    async def retry(self, interaction, button):
        from .panel import PanelModal

        await interaction.response.send_modal(
            PanelModal(
                self.ctx,
                "score" if self.tab == "scores" else "chart",
                ui_text(self.ctx, "重新搜尋歌曲"),
                (("query", ui_text(self.ctx, "歌曲名稱或簡寫"), True),),
            )
        )


class SongSearchActionSelect(discord.ui.Select):
    def __init__(self, ctx=None):
        super().__init__(
            placeholder=ui_text(ctx, "重新搜尋"),
            row=1,
            options=[
                discord.SelectOption(
                    label=ui_text(ctx, "重新輸入曲名／簡寫"), value="retry"
                )
            ],
        )

    async def callback(self, interaction):
        await self.view.retry.callback(interaction)


async def discover_song(
    ctx,
    query,
    *,
    tab="overview",
    correction=False,
    exclude_song_id=None,
    excluded_song_ids=(),
):
    from .panel import retire_previous, show_panel

    service = SongDiscoveryService(ctx.bot.database, ctx.bot.utils.aliases)
    result = await service.search(
        query,
        guild_id=ctx.guild.id if ctx.guild else None,
        ignore_exact=correction,
        exclude_song_id=exclude_song_id,
        excluded_song_ids=excluded_song_ids,
    )
    if not result.candidates:
        return await show_panel(
            ctx,
            "score" if tab == "scores" else "chart",
            description=ui_text(ctx, "沒有找到合適的歌曲，請換一個簡寫或輸入更多字。"),
        )
    if result.needs_choice:
        await retire_previous(ctx)
        return await SongSearchView(ctx, result, tab=tab).start()
    return await open_song_candidate(ctx, result, 0, tab=tab)


async def open_song_candidate(ctx, result, index, *, tab="overview"):
    from .panel import retire_previous, session_for
    from .song_details import SongDetailsState, SongDetailsView

    candidate = result.candidates[index]
    route = session_for(ctx).route
    await retire_previous(ctx)
    view = SongDetailsView(
        ctx,
        candidate.songs,
        synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
        variant_charts=tuple(
            chart
            for song in sorted(
                candidate.songs,
                key=lambda song: (
                    song.genre == "WORLD'S END",
                    not song.available,
                    song.removed,
                    song.id,
                ),
            )
            for chart in sorted(
                song.charts, key=lambda chart: Difficulty(chart.difficulty).value
            )
        )
        if candidate.variants
        else (),
    )
    view.search_query = result.query
    view.state = SongDetailsState(
        tab=tab,
        difficulty=route.difficulty,
        network="chunithm_net" if route.source == "net" else route.source,
    )
    await view.start()
    if candidate.method != "artist" and result.query != search_key(
        candidate.song.title
    ):
        try:
            await offer_alias(ctx, result.query, candidate.song)
        except discord.HTTPException:
            pass  # The optional private reminder must not replace a successful song result.
    return view


class AliasOfferView(discord.ui.View):
    def __init__(self, ctx, alias, song, status):
        super().__init__(timeout=300)
        self.ctx, self.alias, self.song, self.status = ctx, alias, song, status
        self.saving = False

        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label:
                item.label = ui_text(ctx, item.label)
        if status.status != "available":
            self.remove_item(self.add_alias)

        bind_owner_view(self, ctx.author.id, ctx=ctx)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message(
            ui_text(self.ctx, "此提醒只供原搜尋者操作。"), ephemeral=True
        )
        return False

    @discord.ui.button(label="加入這個別名", style=discord.ButtonStyle.primary)
    async def add_alias(self, interaction, button):
        from chuni_penguin.services.alias_learning import AliasLearningService

        from .panel import check_route, component_context

        if self.saving:
            await interaction.response.send_message(
                ui_text(self.ctx, "正在儲存，請稍候。"), ephemeral=True
            )
            return
        self.saving = True
        await interaction.response.defer()
        try:
            await check_route(component_context(self.ctx, interaction), "alias add")
            service = AliasLearningService(
                self.ctx.bot.database, self.ctx.bot.utils.aliases
            )
            result = await service.confirm(
                self.alias,
                guild_id=self.ctx.guild.id,
                song_id=self.song.id,
                owner_id=interaction.user.id,
            )
            if getattr(self, "_embed_closed", False):
                return
            text = {
                "added": ui_text(self.ctx, "已加入此伺服器的別名，下次可以直接使用。"),
                "already_exists": ui_text(
                    self.ctx, "這個別名已經指向此歌曲，不需要重複新增。"
                ),
                "conflict": ui_text(
                    self.ctx, "這個別名已指向其他歌曲，請換個別名。原有對應未被覆寫。"
                ),
                "title_conflict": ui_text(self.ctx, "這是既有歌曲名稱，請換個別名。"),
            }[result.status]
            await edit_view_message(
                interaction,
                embed=discord.Embed(
                    title=ui_text(self.ctx, "別名結果"), description=text
                ),
                view=CloseView(self.ctx.author.id, ctx=self.ctx),
            )
            self.stop()
        except (commands.CommandError, ValueError):
            if getattr(self, "_embed_closed", False):
                return
            await edit_view_message(
                interaction,
                embed=discord.Embed(
                    title=ui_text(self.ctx, "無法加入別名"),
                    description=ui_text(
                        self.ctx, "請確認此伺服器的別名權限，或換一個別名。"
                    ),
                ),
                view=self,
            )
        finally:
            self.saving = False

    @discord.ui.button(label="換個別名")
    async def rename(self, interaction, button):
        await interaction.response.send_modal(AliasRenameModal(self))

    @discord.ui.button(label="關閉")
    async def dismiss(self, interaction, button):
        await dismiss_message(interaction, self, self.ctx.author.id)


class AliasRenameModal(discord.ui.Modal):
    def __init__(self, offer):
        super().__init__(title=ui_text(offer.ctx, "換個歌曲別名"), timeout=300)
        self.offer = offer
        self.alias_input = discord.ui.TextInput(
            label=ui_text(offer.ctx, "新別名"), default=offer.alias, max_length=100
        )
        self.add_item(self.alias_input)

    async def on_submit(self, interaction):
        if getattr(self.offer, "_embed_closed", False):
            await interaction.response.send_message(
                ui_text(self.offer.ctx, "別名提醒已關閉，請重新操作。"), ephemeral=True
            )
            return
        if interaction.user.id != self.offer.ctx.author.id:
            await interaction.response.send_message(
                ui_text(self.offer.ctx, "只能修改自己的別名提議。"), ephemeral=True
            )
            return
        from chuni_penguin.services.alias_learning import AliasLearningService

        await interaction.response.defer()
        alias = self.alias_input.value
        try:
            status = await AliasLearningService(
                self.offer.ctx.bot.database, self.offer.ctx.bot.utils.aliases
            ).inspect(
                alias, guild_id=self.offer.ctx.guild.id, song_id=self.offer.song.id
            )
        except ValueError:
            if getattr(self.offer, "_embed_closed", False):
                return
            await edit_view_message(
                interaction,
                embed=discord.Embed(
                    title=ui_text(self.offer.ctx, "別名格式不合適"),
                    description=ui_text(self.offer.ctx, "請改用 1–100 字的一般文字。"),
                ),
                view=self.offer,
            )
            return
        if getattr(self.offer, "_embed_closed", False):
            return
        view = AliasOfferView(self.offer.ctx, alias, self.offer.song, status)
        await edit_view_message(
            interaction,
            embed=alias_offer_embed(alias, self.offer.song, status, self.offer.ctx),
            view=view,
        )
        self.offer.stop()


def alias_offer_embed(alias, song, status, ctx=None):
    text = ui_text(
        ctx,
        "要把 **{0}** 設為 **{1}** 的別名嗎？\n範圍：目前伺服器共用。",
        discord.utils.escape_markdown(alias),
        discord.utils.escape_markdown(song.title),
    )
    if status.status in ("conflict", "title_conflict"):
        titles = "、".join(
            dict.fromkeys(
                discord.utils.escape_markdown(c.title) for c in status.conflicts
            )
        )
        text += ui_text(ctx, "\n此名稱已有其他對應：{0}。請換個別名。", titles[:1200])
    return discord.Embed(title=ui_text(ctx, "記住這個簡寫？"), description=text[:3500])


async def offer_alias(ctx, query, song):
    # Prefix searches still get this private offer after a component selection.
    if ctx.guild is None or getattr(ctx, "interaction", None) is None:
        return
    from .dismiss import response_closed

    if response_closed(ctx):
        return
    from chuni_penguin.services.alias_learning import AliasLearningService

    from .panel import session_for

    session = session_for(ctx)
    offered = getattr(session, "alias_offers", set())
    key = query.casefold(), song.id
    if key in offered:
        return
    try:
        status = await AliasLearningService(
            ctx.bot.database, ctx.bot.utils.aliases
        ).inspect(query, guild_id=ctx.guild.id, song_id=song.id)
    except ValueError:
        return
    if status.status == "already_exists":
        return
    offered.add(key)
    session.alias_offers = offered
    await ctx.interaction.followup.send(
        embed=alias_offer_embed(query, song, status, ctx),
        view=AliasOfferView(ctx, query, song, status),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
