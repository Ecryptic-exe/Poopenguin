# ruff: noqa: RUF001
"""Phase-one player entry points; reuse A's clients, renderers and privacy settings."""

from datetime import UTC, datetime
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete, select

from chuni_penguin.calculation import calculate_rating
from chuni_penguin.context import PenguinContext
from chuni_penguin.database import (
    Cookie,
    PendingKamaitachiImport,
    PersonalBest,
    PlayerSource,
    UserConfig,
)
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.services.player_sources import PlayerSourceService, SourceError
from chuni_penguin.ui import EmbedPaginationView, ScoreCardEmbed
from chuni_penguin.ui.autocomplete import song_title_choices

from .chunithm.best_plays import BestPlaysCog
from .chunithm.recent_scores import RecentScoresCog

Source = Literal["net", "kamaitachi", "chunirec:jp2", "chunirec:jp", "chunilib:export"]
Difficulty = Literal["BAS", "ADV", "EXP", "MAS", "ULT"]


class ChunirecLinkModal(discord.ui.Modal, title="綁定自己的 Chunirec 帳號"):
    token = discord.ui.TextInput(
        label="Chunirec Access Token", max_length=200, min_length=10
    )

    def __init__(self, cog, user_id, region, *, ctx=None):
        super().__init__(timeout=180)
        self.cog, self.user_id, self.region = cog, user_id, region
        self.ctx = ctx
        if ctx is not None:
            ctx._panel_private = True

    def clear_submission(self, interaction):
        self.token._value = ""

        def clear(node):
            if isinstance(node, dict):
                if node.get("type") == 4 and "value" in node:
                    node["value"] = ""
                for value in node.values():
                    clear(value)
            elif isinstance(node, list):
                for value in node:
                    clear(value)

        clear(getattr(interaction, "data", None))
        self.stop()

    async def on_submit(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "此表單只供本人使用。", ephemeral=True
            )
            return
        from chuni_penguin.ui.panel import defer_panel, panel_message

        anchor = panel_message(self.ctx, interaction) if self.ctx is not None else None
        if self.ctx is not None:
            self.ctx.response = anchor
            await defer_panel(self.ctx, interaction)
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.ctx is not None:
                from chuni_penguin.ui.panel import check_route

                await check_route(self.ctx, "account")
            result = await self.cog.sources.sync_chunirec(
                self.user_id, token=self.token.value.strip(), region=self.region
            )
        except (SourceError, commands.CommandError) as exc:
            content = str(exc)
        else:
            content = self.cog.sync_result(result, f"chunirec:{self.region}")
        finally:
            self.clear_submission(interaction)
        if self.ctx is None:
            await interaction.followup.send(content, ephemeral=True)
            return
        from chuni_penguin.ui.panel import PanelRoute, session_for, show_account

        session = session_for(self.ctx)
        before = session.route
        session.route = PanelRoute(
            "account", before.query, f"chunirec:{self.region}", before.difficulty
        )
        # View callbacks retain the safe parent context. Submitted credential
        # fields are cleared before the response can be retained by a new view.
        await show_account(self.ctx, notice=content)
        from chuni_penguin.ui.panel import refresh_account_parent

        await refresh_account_parent(self.ctx)

    async def on_error(self, interaction, error):
        # A Modal traceback can contain the submitted secret in locals.
        message = "綁定失敗，請檢查本機資料庫與設定後重試。"
        self.clear_submission(interaction)
        if self.ctx is not None:
            from chuni_penguin.ui.panel import (
                defer_panel,
                panel_message,
                show_account,
            )

            anchor = panel_message(self.ctx, interaction)
            self.ctx.response = anchor
            await defer_panel(self.ctx, interaction)
            await show_account(self.ctx, notice=message)
            return
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class PlayersCog(commands.Cog, name="V2Players"):
    account = app_commands.Group(
        name="account", description="私人帳號綁定、同步狀態及刪除"
    )

    def __init__(self, bot):
        self.bot = bot
        if not hasattr(bot, "player_sources"):
            bot.player_sources = PlayerSourceService(bot.services.database)
            bot.player_sources.locks = bot.services.accounts.user_locks
        self.sources = bot.player_sources
        self.best = BestPlaysCog(bot)
        self.recent_native = RecentScoresCog(bot)

    async def cog_load(self):
        await self.best.cog_load()

    async def send_result(self, interaction, content, *, ephemeral=True):
        from chuni_penguin.ui.panel import (
            PanelRoute,
            PanelView,
            session_for,
            show_account,
        )

        ctx = await PenguinContext.from_interaction(interaction)
        session = session_for(ctx)
        root = getattr(
            getattr(interaction, "command", None), "qualified_name", "home"
        ).split()[0]
        section = "account" if root in ("account", "privacy") else root
        if root in ("account", "privacy", "sync"):
            await show_account(ctx, notice=content)
            return
        route = session.route
        session.route = PanelRoute(section, route.query, route.source, route.difficulty)
        view = PanelView(ctx)
        embed = discord.Embed(
            title={
                "account": "帳號與隱私",
                "sync": "同步資料",
                "score": "我的成績",
                "compare": "成績比較",
                "b50": "Best 50",
                "recent": "最近遊玩",
            }.get(section, "CHUNITHM Penguin"),
            description=content[:4096],
            color=0x57B9CF,
        )
        if root in ("score", "compare", "b50", "recent"):
            view.message = await ctx.respond_or_edit(
                embed=embed, view=view, ephemeral=ephemeral
            )
        else:
            view.message = await interaction.followup.send(
                embed=embed, view=view, ephemeral=ephemeral, wait=True
            )

    @staticmethod
    def sync_result(result, source):
        return f"{source} 已匯入 {len(result['records'])} 筆；可信 ID 關聯 {result['mapped']} 筆，未關聯 {result['unmapped']} 筆。未關聯資料可在主資料的「來源成績」查詢。此來源不提供最近遊玩歷史。"

    @account.command(name="link", description="透過私人表單綁定自己的 Chunirec Token")
    async def link(
        self, interaction: discord.Interaction, region: Literal["jp2", "jp"] = "jp2"
    ):
        ctx = await PenguinContext.from_interaction(interaction)
        await interaction.response.send_modal(
            ChunirecLinkModal(self, interaction.user.id, region, ctx=ctx)
        )

    @account.command(name="status", description="查看自己的各來源同步狀態")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.begin_db_read() as session:
            rows = (
                await session.scalars(
                    select(PlayerSource).where(
                        PlayerSource.discord_id == interaction.user.id
                    )
                )
            ).all()
            cookie = await session.get(Cookie, interaction.user.id)
        lines = [f"{r.source} · {r.status} · {r.synced_at or '尚未同步'}" for r in rows]
        lines.append(
            f"NET：{'已綁定' if cookie and cookie.cookie else '未綁定'}；Kamaitachi：{'已綁定' if cookie and cookie.kamaitachi_token else '未綁定'}"
        )
        lines.append("Chunilib：僅支援自行匯出檔匯入；網站登入與受保護 API 尚未接入。")
        await self.send_result(interaction, "\n".join(lines), ephemeral=True)

    @account.command(
        name="unlink", description="刪除此來源憑證、匯入快照與本機成績，不修改來源網站"
    )
    async def unlink(
        self,
        interaction: discord.Interaction,
        source: Literal["chunirec:jp2", "chunirec:jp", "chunilib:export"],
    ):
        await interaction.response.defer(ephemeral=True)
        await self.sources.unlink(interaction.user.id, source)
        await self.send_result(
            interaction, f"已刪除自己的 {source} 綁定及本機資料。", ephemeral=True
        )

    @account.command(
        name="delete", description="刪除自己的所有帳號憑證、成績快取與偏好"
    )
    async def delete_account(
        self, interaction: discord.Interaction, *, confirm: bool = False
    ):
        if not confirm:
            await interaction.response.send_message(
                "此操作刪除自己的本機帳號、成績、偏好、譜面評價與評論者背景。確認後請指定 confirm: true；來源網站資料不受影響。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.send_result(
            interaction, await self.delete_own_data(interaction.user.id), ephemeral=True
        )

    async def delete_own_data(self, user_id):
        """Shared confirmed deletion for the original command and account UI."""
        if self.sources.locks[user_id].locked():
            return "帳號目前正在查詢或同步，請結束操作後重試。"
        async with self.sources.locks[user_id]:
            if self.bot.services.accounts.active_users.get(user_id, 0):
                return "請先關閉正在使用帳號的成績分頁，再刪除帳號。"

            async def erase(session):
                from chuni_penguin.database import ChartReviewerProfile

                await session.execute(
                    delete(ChartReviewerProfile).where(
                        ChartReviewerProfile.discord_id == user_id
                    )
                )
                for model in (
                    PlayerSource,
                    PersonalBest,
                    Cookie,
                    UserConfig,
                    PendingKamaitachiImport,
                ):
                    await session.execute(
                        delete(model).where(model.discord_id == user_id)
                    )

            await self.bot.database.writer.execute_fn(erase)
        return "已刪除自己的本機帳號、成績、偏好、譜面評價與評論者背景。備份檔請由管理者依保存政策清除。"

    @app_commands.command(
        name="privacy", description="設定是否允許其他 Discord 使用者查詢自己的資料"
    )
    async def privacy(self, interaction: discord.Interaction, *, share: bool = False):
        await interaction.response.defer(ephemeral=True)
        users = self.bot.services.users
        old = await users.stored_user_config(interaction.user.id)
        row = old or UserConfig(
            discord_id=interaction.user.id, synthesis_alt_jacket="default"
        )
        row.privacy_mode = not share
        await users.save_user_config(row, is_new=old is None)
        await self.send_result(
            interaction,
            "已允許他人查詢與比較；這不會改變目前訊息的公開／私人狀態。"
            if share
            else "已設為僅本人可查詢。",
            ephemeral=True,
        )

    @app_commands.command(
        name="sync",
        description="從明確指定的來源讀取自己的成績，或匯入 Chunilib 匯出檔",
    )
    @app_commands.checks.cooldown(2, 60, key=lambda i: i.user.id)
    async def sync(
        self,
        interaction: discord.Interaction,
        source: Source = "net",
        export: discord.Attachment | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if source.startswith("chunirec:"):
                result = await self.sources.sync_chunirec(
                    interaction.user.id, region=source.split(":")[1]
                )
            elif source == "chunilib:export":
                if export is None:
                    raise SourceError(
                        "Chunilib 尚無可驗證的 Bot 玩家登入 API。請提供自己的 player-data JSON 匯出檔；此操作是本機匯入。"
                    )
                if export.size > 5 * 1024 * 1024:
                    raise SourceError("匯出檔不可超過 5 MiB。")
                result = await self.sources.import_chunilib(
                    interaction.user.id, await export.read()
                )
            else:
                ctx = await PenguinContext.from_interaction(interaction)
                await self.send_result(
                    interaction,
                    await self.sync_native(ctx, source),
                    ephemeral=True,
                )
                return
            await self.send_result(
                interaction, self.sync_result(result, source), ephemeral=True
            )
        except SourceError as exc:
            await self.send_result(interaction, str(exc), ephemeral=True)

    async def source_rows(self, ctx, source, target=None, *, song_id=None):
        user_id = target.id if target else ctx.author.id
        config = await self.bot.services.users.fetch_user_config(user_id)
        if user_id != ctx.author.id and config.privacy_mode:
            raise SourceError("該玩家尚未允許分享成績。")
        if source in ("net", "kamaitachi"):
            async with self.bot.services.accounts.network(
                ctx,
                user_id,
                kamaitachi=source == "kamaitachi",
                chunithm_net=source == "net",
            ) as client:
                records = (
                    await client.get_personal_bests_on_song(song_id)
                    if song_id is not None
                    else await client.get_all_personal_bests()
                )
            return [
                {
                    "song_id": r.song.id,
                    "id": str(r.song.id),
                    "title": r.song.title,
                    "diff": r.chart.difficulty.short(),
                    "score": r.score,
                    "record": r,
                }
                for r in records
            ]
        payload, _ = await self.sources.records(user_id, source)
        return payload["records"]

    async def sync_native(self, ctx, source):
        """The same source-explicit native synchronization for commands and panels."""
        if source not in ("net", "kamaitachi"):
            raise SourceError("請選擇 NET 或 Kamaitachi。")
        try:
            async with self.bot.services.accounts.network(
                ctx, kamaitachi=source == "kamaitachi", chunithm_net=source == "net"
            ) as client:
                await client.get_minimal_profile()
                recent = await client.get_recent_scores()
                if client.SUPPORTS_DETAILED_RECENT_SCORE:
                    for played in recent:
                        await client.get_detailed_recent_score(played)
                records = await client.get_all_personal_bests()
                await self.bot.database.writer.merge(
                    PlayerSource(
                        discord_id=ctx.author.id,
                        source=source,
                        credential=None,
                        payload=None,
                        synced_at=datetime.now(UTC).isoformat(),
                        status="synced",
                    )
                )
            return f"{client.NAME} 已讀取 {len(records)} 筆個人最佳與 {len(recent)} 筆最近遊玩，並更新本機快取。"
        except Exception:
            row = await self.sources.get(ctx.author.id, source)
            row = row or PlayerSource(
                discord_id=ctx.author.id,
                source=source,
                credential=None,
                payload=None,
                synced_at=None,
            )
            row.status = "failed; partial cache possible"
            await self.bot.database.writer.merge(row)
            raise

    async def matching_rows(self, ctx, query, source, difficulty=None, target=None):
        if source in ("net", "kamaitachi"):
            song, _, _ = await ctx.find_song(query)
            rows = (
                []
                if song is None
                else await self.source_rows(ctx, source, target, song_id=song.id)
            )
        else:
            # Name filtering within this user's source snapshot does not create an ID mapping.
            rows = await self.source_rows(ctx, source, target)
            rows = [
                r
                for r in rows
                if query.casefold() in r["title"].casefold() or query == r["id"]
            ]
        return [r for r in rows if difficulty is None or r["diff"] == difficulty]

    @commands.command(name="scores", aliases=["score"])
    @logged_prefix_command
    async def scores_prefix(self, ctx: PenguinContext, *, query: str):
        """Show your CHUNITHM-NET scores for one song in a prefix command."""
        try:
            rows = await self.matching_rows(ctx, query, "net")
            if not rows:
                await ctx.reply("此歌曲沒有找到已遊玩的 CHUNITHM-NET 成績。")
                return

            embeds = [
                ScoreCardEmbed(
                    row["record"],
                    synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                    detailed=True,
                )
                for row in rows
                if "record" in row
            ]
            if not embeds:
                await ctx.reply("此來源沒有符合的已遊玩成績。")
                return

            await EmbedPaginationView(ctx, embeds).start()
        except SourceError as exc:
            await ctx.reply(str(exc))

    @app_commands.command(name="score", description="查詢自己的指定歌曲及難度成績")
    async def score(
        self,
        interaction: discord.Interaction,
        query: str,
        difficulty: Difficulty | None = None,
        source: Source = "net",
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = await PenguinContext.from_interaction(interaction)
        if source in ("net", "kamaitachi"):
            from chuni_penguin.ui.panel import PanelRoute, session_for, show_song

            session_for(ctx).route = PanelRoute(
                "score", query, source, difficulty or "MAS"
            )
            await show_song(ctx, query, tab="scores")
            return
        try:
            rows = await self.matching_rows(ctx, query, source, difficulty)
            embeds = [
                ScoreCardEmbed(r["record"])
                if "record" in r
                else discord.Embed(
                    title=r["title"][:256], description=f"{r['diff']} · {r['score']:,}"
                ).set_footer(text=f"{source} · source ID {r['id']}")
                for r in rows
            ]
            if not embeds:
                await self.send_result(
                    interaction, "此來源沒有符合的已遊玩成績。", ephemeral=True
                )
                return
            await EmbedPaginationView(ctx, embeds).start(ephemeral=True)
        except SourceError as exc:
            await self.send_result(interaction, str(exc), ephemeral=True)

    @app_commands.command(
        name="compare", description="比較自己與已允許分享的玩家成績及分數差距"
    )
    async def compare(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        query: str,
        difficulty: Difficulty = "MAS",
        source: Source = "net",
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = await PenguinContext.from_interaction(interaction)
        try:
            mine = await self.matching_rows(ctx, query, source, difficulty)
            theirs = await self.matching_rows(ctx, query, source, difficulty, user)
            by_id = {(r["id"], r["diff"]): r for r in theirs}
            lines = [
                f"{discord.utils.escape_markdown(r['title'])} · {r['diff']}：{r['score']:,} vs {by_id[(r['id'], r['diff'])]['score']:,}（差 {r['score'] - by_id[(r['id'], r['diff'])]['score']:+,}）"
                for r in mine
                if (r["id"], r["diff"]) in by_id
            ]
            deltas = [
                r["score"] - by_id[(r["id"], r["diff"])]["score"]
                for r in mine
                if (r["id"], r["diff"]) in by_id
            ]
            summary = (
                (
                    f"共同譜面 {len(deltas)} 張 · 領先 {sum(d > 0 for d in deltas)}／平手 {sum(d == 0 for d in deltas)}／落後 {sum(d < 0 for d in deltas)} · 平均分差 {sum(deltas) / len(deltas):+,.1f}\n\n"
                )
                if deltas
                else ""
            )
            embeds = [
                discord.Embed(
                    title="成績比較", description=summary + "\n".join(lines[i : i + 5])
                ).set_footer(text=source)
                for i in range(0, len(lines), 5)
            ]
            if not embeds:
                await self.send_result(
                    interaction, "雙方沒有可依來源 ID 對應的共同成績。", ephemeral=True
                )
            else:
                await EmbedPaginationView(ctx, embeds).start(ephemeral=True)
        except SourceError as exc:
            await self.send_result(interaction, str(exc), ephemeral=True)

    @app_commands.command(
        name="b50", description="查詢 Best 50；NET 沿用原 Best30＋New20 圖片"
    )
    async def b50(self, interaction: discord.Interaction, source: Source = "net"):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = await PenguinContext.from_interaction(interaction)
        if source in ("net", "kamaitachi"):
            if source == "net":
                cookie = await self.bot.database.cookies.get_by_discord_id(
                    interaction.user.id
                )
                if cookie is None or not cookie.cookie.startswith("#LWP-Cookies-2.0"):
                    await self.send_result(
                        interaction,
                        "NET 尚未登入，請先 /login；此操作不會改用其他來源。",
                        ephemeral=True,
                    )
                    return
            await self.best._best50_inner(ctx, kamaitachi=source == "kamaitachi")
            return
        try:
            payload, row = await self.sources.records(interaction.user.id, source)
            ranked = []
            for record in payload["records"]:
                constant = record.get("source_const")
                if constant is not None:
                    ranked.append((calculate_rating(record["score"], constant), record))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            lines = [
                f"{i}. {discord.utils.escape_markdown(r['title'][:150])} [{r['diff']}] · {r['score']:,} · {rating:.2f}"
                for i, (rating, r) in enumerate(ranked[:50], 1)
            ]
            if not lines:
                raise SourceError(
                    "此匯入來源尚無可信譜面定數關聯，無法計算 Best50；請使用 /score 查看原始成績。"
                )
            header = f"來源回報 Rating：{payload.get('rating') or '未提供'}\n本機依來源已確認定數排序 Best50；不代表來源遊戲內 Rating 組成。"
            embeds = [
                discord.Embed(
                    title="Best 50",
                    description=header + "\n\n" + "\n".join(lines[i : i + 10]),
                ).set_footer(text=f"{source} · 同步 {row.synced_at}")
                for i in range(0, len(lines), 10)
            ]
            await EmbedPaginationView(ctx, embeds).start(ephemeral=True)
        except SourceError as exc:
            await self.send_result(interaction, str(exc), ephemeral=True)

    @app_commands.command(
        name="recent", description="查看真正的最近遊玩歷史（NET／Kamaitachi）"
    )
    async def recent(self, interaction: discord.Interaction, source: Source = "net"):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if source not in ("net", "kamaitachi"):
            await self.send_result(
                interaction,
                "此來源目前未提供可驗證的遊玩歷史；Best 更新時間與 Rating 的 RECENT 枠不能當作最近遊玩紀錄。",
                ephemeral=True,
            )
            return
        if source == "net":
            cookie = await self.bot.database.cookies.get_by_discord_id(
                interaction.user.id
            )
            if cookie is None or not cookie.cookie.startswith("#LWP-Cookies-2.0"):
                await self.send_result(
                    interaction,
                    "NET 尚未登入，請先 /login；此操作不會改用其他來源。",
                    ephemeral=True,
                )
                return
        ctx = await PenguinContext.from_interaction(interaction)
        await self.recent_native._recent_inner(ctx, kamaitachi=source == "kamaitachi")

    @score.autocomplete("query")
    @compare.autocomplete("query")
    async def complete_query(self, interaction: discord.Interaction, current: str):
        return song_title_choices(
            self.bot.services.autocomplete, current, interaction.guild_id
        )


async def setup(bot):
    await bot.add_cog(PlayersCog(bot))
