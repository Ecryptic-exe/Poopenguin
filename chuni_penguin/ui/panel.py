# ruff: noqa: RUF001
"""Shared, owner-bound Embed navigation over the existing commands/services."""

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from types import SimpleNamespace

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Chart
from chuni_penguin.logging import logger
from chuni_penguin.services.player_sources import SourceError
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, ui_text

from ._base import PenguinView
from .account_state import ACCOUNT_SOURCES, CHUNILIB_URL

SECTIONS = (
    ("home", "主選單"),
    ("chart", "歌曲／譜面"),
    ("score", "我的成績"),
    ("b50", "Best 50"),
    ("recent", "最近遊玩"),
    ("source_scores", "來源成績"),
    ("compare", "成績比較"),
    ("account", "主資料介面"),
    ("sync", "同步資料"),
    ("find", "篩選譜面"),
    ("tags", "人工標籤"),
    ("alias", "歌曲別名"),
    ("tools", "計算工具"),
    ("account_settings", "帳號設定"),
    ("fun", "娛樂工具"),
)
SOURCES = ACCOUNT_SOURCES
FORM_COMMANDS = {
    "find": (
        "篩選譜面",
        "find",
        (
            ("level", "等級或定數範圍", True),
            ("difficulty", "難度（可留白）", False),
            ("version", "版本（可留白）", False),
            ("tag", "人工標籤（可留白）", False),
        ),
    ),
    "alias": ("歌曲別名", "alias list", (("query", "歌曲或別名", True),)),
    "alias_add": (
        "新增歌曲別名",
        "alias add",
        (
            ("song_title_or_alias", "歌曲名稱", True),
            ("added_alias", "新增別名", True),
            ("global_alias", "全域別名 true/false（可留白）", False),
        ),
    ),
    "alias_remove": (
        "移除歌曲別名",
        "alias remove",
        (("removed_alias", "要移除的別名", True),),
    ),
    "alias_reload": ("重新載入別名", "alias reload", ()),
    "calculate": (
        "Rating 試算",
        "calculate",
        (("score", "分數", True), ("chart_constant", "譜面定數（可留白）", False)),
    ),
    "anmitsu": (
        "餡蜜間隔",
        "anmitsu",
        (
            ("bpm", "BPM", True),
            ("note_density", "音符密度（例如 16 分音符填 16）", True),
        ),
    ),
    "const": (
        "定數試算",
        "const",
        (
            ("chart_constant", "譜面定數", True),
            ("mode", "default 或 aj（可留白）", False),
        ),
    ),
    "rating": ("目標 Rating", "rating", (("rating", "目標單曲 Rating", True),)),
    "border": (
        "容錯計算",
        "border",
        (
            ("difficulty_or_notecount", "難度或總物量", True),
            ("query", "歌曲（直接輸入物量時留白）", False),
        ),
    ),
    "ask": ("問問企鵝", "ask", (("question", "你的問題", True),)),
    "pick": ("幫我選", "pick", (("choices", "選項（空格分隔，可用引號）", True),)),
    "rng": (
        "隨機數字",
        "rng",
        (
            ("min_val", "最小值", False),
            ("max_val", "最大值", False),
            ("type", "int 或 float", False),
        ),
    ),
    "rcg": ("隨機顏色", "rcg", ()),
    "gacha": ("抽卡", "gacha", ()),
}
AUTO_PANEL_ROOTS = {
    "menu",
    "dailyupdate",
    "help",
    "tools",
    "chart",
    "chartview",
    "score",
    "compare",
    "b50",
    "recent",
    "sync",
    "account",
    "privacy",
    "login",
    "logout",
    "find",
    "alias",
    "calculate",
    "const",
    "rating",
    "border",
    "anmitsu",
    "tags",
    "ask",
    "pick",
    "rng",
    "rcg",
    "gacha",
}
NO_AUTO_PANEL = {
    "token",
    "permissions",
    "botconfig",
    "cog",
    "say",
    "treesync",
    "treediff",
}


@dataclass(frozen=True)
class PanelRoute:
    section: str = "home"
    query: str = ""
    source: str = "net"
    difficulty: str = "MAS"


class PanelSession:
    def __init__(self, ctx):
        self.owner_id = ctx.author.id
        name = getattr(getattr(ctx, "command", None), "qualified_name", "home").split()[
            0
        ]
        namespace = getattr(getattr(ctx, "interaction", None), "namespace", None)
        query = getattr(namespace, "query", "")
        source = getattr(namespace, "source", "net")
        difficulty = getattr(namespace, "difficulty", "MAS")
        self.route = PanelRoute(
            "chart"
            if name in ("info", "chartview")
            else name
            if name in dict(SECTIONS) or name in FORM_COMMANDS
            else "home",
            query if isinstance(query, str) else "",
            source
            if source
            in ("net", "kamaitachi", "chunirec:jp2", "chunirec:jp", "chunilib:export")
            else "net",
            difficulty if difficulty in ("BAS", "ADV", "EXP", "MAS", "ULT") else "MAS",
        )
        self.history = []
        self.message = None
        self.active_view = None
        self.busy = False
        self.pending_login = None
        self.parameters = {}
        self.song_card = None
        self.chart_results = None
        self.retry_action = None
        self.closed = False
        target = getattr(namespace, "user", None)
        if target is not None and getattr(target, "id", None):
            self.parameters["compare"] = {
                "user_id": str(target.id),
                "query": self.route.query,
            }

    def move(self, section, *, remember=True):
        before = self.route
        if section == "back":
            group = group_for(before.section)
            while self.history:
                candidate = self.history.pop()
                if group_for(candidate.section) == group:
                    self.route = candidate
                    break
            else:
                self.route = PanelRoute(
                    group_home(group), before.query, before.source, before.difficulty
                )
        else:
            if remember and section != before.section:
                self.history = [*self.history[-7:], before]
            if section == "home":
                self.history.clear()
            self.route = PanelRoute(
                section, before.query, before.source, before.difficulty
            )


def session_for(ctx):
    session = getattr(ctx, "panel_session", None)
    if session is None:
        session = ctx.panel_session = PanelSession(ctx)
    return session


def panel_message(ctx, interaction=None):
    """Keep the response anchor even when a modal omits its source message."""
    session = session_for(ctx)
    for message in (
        session.message,
        getattr(ctx, "response", None),
        getattr(interaction, "message", None),
    ):
        if message is not None and (
            not getattr(ctx, "_panel_new_private", False) or private_message(message)
        ):
            return message
    return None


def bind_panel_message(ctx, message):
    session = getattr(ctx, "panel_session", None)
    if session is not None and message is not None:
        ctx.panel_session.message = message
        ctx.response = message
        ctx._panel_new_private = False


async def defer_panel(ctx, interaction):
    """Acknowledge and obtain the webhook-backed response for ephemeral edits.

    interaction.message is a regular Message: editing it uses the channel API,
    which cannot edit an ephemeral message. original_response supplies the
    InteractionMessage and current interaction token for the same message ID.
    """
    anchor = getattr(ctx, "response", None)
    if not interaction.response.is_done():
        await interaction.response.defer(
            thinking=anchor is None,
            ephemeral=bool(getattr(ctx, "_panel_private", False)),
        )
    try:
        response = await interaction.original_response()
    except discord.NotFound:
        if anchor is None:
            raise
        return
    if anchor is None or response.id == anchor.id:
        bind_panel_message(ctx, response)


@asynccontextmanager
async def waiting_for_login(ctx):
    """Allow leaving the existing login prompt while it waits for user input."""
    session = getattr(ctx, "panel_session", None)
    interactive = session is not None and getattr(ctx, "_panel_edit", False)
    if interactive:
        session.pending_login = asyncio.current_task()
        session.busy = False
    try:
        yield
    finally:
        if interactive:
            session.pending_login = None
            session.busy = True


async def finish_login(ctx, message, embed):
    if getattr(ctx, "interaction", None) is not None:
        bind_panel_message(ctx, message)
        await show_account(ctx, notice=f"{embed.title}\n{embed.description or ''}")
        await refresh_account_parent(ctx)
    else:
        from .dismiss import CloseView

        await message.edit(content=None, embed=embed, view=CloseView(ctx.author.id))


def private_message(message):
    return bool(getattr(getattr(message, "flags", None), "ephemeral", False))


def component_context(ctx, interaction):
    updated = copy.copy(ctx)
    # A processing wrapper temporarily closes over its original context.
    # Never copy that bound responder into a different public/private session.
    responder = (
        getattr(ctx, "_processing_response_original", None)
        if getattr(ctx, "_processing_response_active", False)
        else ctx.respond_or_edit
    )
    if responder is not None:
        updated.respond_or_edit = (
            responder.__func__.__get__(updated, type(updated))
            if getattr(responder, "__self__", None) is not None
            else responder
        )
    updated._processing_response_active = False
    updated._processing_response_original = None
    updated.interaction = interaction
    updated.response = panel_message(ctx, interaction)
    updated.panel_session = session_for(ctx)
    updated._panel_private = private_message(updated.response) or getattr(
        ctx, "_panel_new_private", False
    )
    updated._panel_edit = True
    updated._panel_new_private = getattr(ctx, "_panel_new_private", False)
    interaction._baton = updated
    return updated


def sensitive_context(ctx, interaction):
    updated = component_context(ctx, interaction)
    if not private_message(updated.response):
        before = session_for(ctx)
        updated.panel_session = PanelSession(updated)
        updated.panel_session.route = before.route
        updated.panel_session.parameters = copy.deepcopy(before.parameters)
        updated.response = None
        updated._panel_new_private = True
        updated._account_parent = ctx
    updated._panel_private = True
    return updated


async def check_route(ctx, command_name):
    from .entrypoints import panel_command

    target = panel_command(ctx.bot, command_name)
    if isinstance(target, discord.app_commands.Group):
        target = (
            target.get_command("status")
            or target.get_command("share")
            or target.commands[0]
        )
    if target is None:
        raise commands.CommandError(ui_text(ctx, "此功能尚未啟用。"))
    ctx.command = target
    if not await ctx.bot.services.permissions.check(ctx):
        raise commands.CheckFailure(ui_text(ctx, "此功能目前無法使用。"))


async def invoke_existing(ctx, command_name, values=None):
    """Use explicit safe UI entries and the original checks/converters/callbacks."""
    if command_name not in {spec[1] for spec in FORM_COMMANDS.values()} | {
        "login",
        "logout",
        "token",
    }:
        raise commands.CheckFailure(ui_text(ctx, "此指令不能從面板執行。"))
    command = ctx.bot.get_command(command_name)
    if command is None:
        raise commands.CommandError(ui_text(ctx, "此功能尚未啟用。"))
    ctx.command = command
    # HybridCommand.can_run assumes a slash Interaction._baton. Components use
    # the original command checks and the application checks explicitly instead.
    if not await commands.Command.can_run(command, ctx):
        raise commands.CheckFailure(ui_text(ctx, "此功能目前無法使用。"))
    application_command = getattr(command, "app_command", None)
    if application_command is not None:
        for check in application_command.checks:
            if not await discord.utils.maybe_coroutine(check, ctx.interaction):
                raise commands.CheckFailure(ui_text(ctx, "此功能目前無法使用。"))
    converted = {}
    values = values or {}
    for name, param in command.clean_params.items():
        raw = values.get(name, "")
        if not raw:
            if param.required:
                raise commands.MissingRequiredArgument(param)
            converted[name] = param.default
        else:
            ctx.current_parameter = param
            ctx.current_argument = raw
            converted[name] = await commands.run_converters(
                ctx, param.converter, raw, param
            )
    ctx.args = [ctx] if command.cog is None else [command.cog, ctx]
    ctx.kwargs = converted
    ctx.invoked_with = command.name
    ctx.invoked_subcommand = None
    command._prepare_cooldowns(ctx)
    concurrency = command._max_concurrency
    if concurrency is not None:
        await concurrency.acquire(ctx)
    try:
        await command.call_before_hooks(ctx)
        await command.callback(*ctx.args, **ctx.kwargs)
    finally:
        if concurrency is not None:
            await concurrency.release(ctx)
        await command.call_after_hooks(ctx)


def group_for(section):
    if section in (
        "account",
        "sync",
        "login",
        "logout",
        "privacy",
        "run_sync",
        "link",
        "link_source",
        "b50",
        "recent",
        "source_scores",
        "account_settings",
        "account_refresh",
        "token",
        "unlink",
        "delete_account",
        "import",
    ):
        return "net"
    if section in ("fun", "ask", "pick", "rng", "rcg", "gacha"):
        return "fun"
    if section in ("tools", "calculate", "const", "rating", "border", "anmitsu"):
        return "tools"
    return "home" if section in ("home", "menu", "help") else "song"


def group_home(group):
    return {"net": "account", "song": "chart", "fun": "fun", "tools": "tools"}.get(
        group, "home"
    )


class PanelMenu(discord.ui.Select):
    def __init__(self, ctx, *, row=4):
        self.ctx = ctx
        options = []
        section = session_for(ctx).route.section
        group = group_for(section)
        related = {
            "net": ("b50", "recent", "source_scores"),
            "song": ("chart", "find", "tags"),
            "fun": ("fun", "ask", "pick", "rng", "rcg", "gacha"),
            "tools": ("tools", "calculate", "const", "rating", "border", "anmitsu"),
            "home": (),
        }[group]
        labels = {
            **dict(SECTIONS),
            **{key: value[0] for key, value in FORM_COMMANDS.items()},
        }
        options.extend(
            discord.SelectOption(
                label=ui_text(ctx, labels.get(key, "匯入 Chunilib JSON")), value=key
            )
            for key in related
            if key != section or group == "fun"
            if not (
                group == "net"
                and key == "recent"
                and session_for(ctx).route.source not in ("net", "kamaitachi")
            )
        )
        if group == "net" and section != "account" and row != 1:
            options.insert(
                0,
                discord.SelectOption(label=ui_text(ctx, "返回主資料"), value="account"),
            )
        if group == "fun" and any(
            group_for(route.section) == group for route in session_for(ctx).history
        ):
            options.append(
                discord.SelectOption(label=ui_text(ctx, "返回上一層"), value="back")
            )
        if not options:
            options.append(
                discord.SelectOption(label=ui_text(ctx, "主選單"), value="menu")
            )
        super().__init__(
            placeholder={
                "net": ui_text(ctx, "個人紀錄"),
                "song": ui_text(ctx, "單曲相關操作"),
                "fun": ui_text(ctx, "Poopenguin 功能"),
                "tools": ui_text(ctx, "其他計算工具"),
            }.get(group, ui_text(ctx, "分類入口")),
            custom_id="penguin:panel:navigation",
            row=row,
            options=options,
        )

    async def callback(self, interaction):
        await navigate(self.ctx, interaction, self.values[0])


def attach_navigation(ctx, view):
    root = getattr(getattr(ctx, "command", None), "qualified_name", "").split(" ")[0]
    if root not in AUTO_PANEL_ROOTS and not getattr(ctx, "panel_session", None):
        return
    from .navigation import add_back_button

    add_back_button(ctx, view)
    if getattr(view, "_panel_navigation", False) or getattr(
        view, "_panel_own_navigation", False
    ):
        return
    if isinstance(view, discord.ui.LayoutView):
        return
    used = {getattr(item, "_rendered_row", item.row) for item in view.children}
    row = next((row for row in (4, 3, 2, 1, 0) if row not in used), None)
    if row is None:
        return
    view.add_item(PanelMenu(ctx, row=row))
    if group_for(session_for(ctx).route.section) == "net":
        used.add(row)
        source_row = next((r for r in (2, 3, 1, 0) if r not in used), None)
        if source_row is not None:
            chooser = SourceSelect(ctx)
            chooser.row = source_row
            view.add_item(chooser)
    view._panel_navigation = True
    session_for(ctx).active_view = view


def prepare_response(ctx, content, kwargs):
    from .dismiss import close_response

    content, kwargs = _prepare_panel_response(ctx, content, kwargs)
    return content, close_response(ctx, kwargs)


def _prepare_panel_response(ctx, content, kwargs):
    """Use the slash presentation for both command entry points."""
    if getattr(ctx, "bot", None) is None:
        return content, kwargs
    root = getattr(getattr(ctx, "command", None), "qualified_name", "").split(" ")[0]
    if root in NO_AUTO_PANEL or root not in AUTO_PANEL_ROOTS:
        return content, kwargs
    if getattr(ctx, "_panel_private", False):
        kwargs["ephemeral"] = True
    if isinstance(kwargs.get("view"), discord.ui.LayoutView):
        return content, kwargs
    if content and not kwargs.get("embed") and not kwargs.get("embeds"):
        mode = session_for(ctx).route.section
        title = dict(SECTIONS).get(
            mode, FORM_COMMANDS.get(mode, ("CHUNITHM Penguin",))[0]
        )
        kwargs["embed"] = discord.Embed(
            title=ui_text(ctx, title), description=str(content)[:4096], color=0x57B9CF
        )
        content = None
    if "view" not in kwargs:
        kwargs["view"] = PanelView(ctx)
    view = kwargs.get("view")
    if isinstance(view, discord.ui.View):
        attach_navigation(ctx, view)
    files = kwargs.get("files") or ([kwargs["file"]] if kwargs.get("file") else [])
    if files and not kwargs.get("embeds") and not kwargs.get("embed"):
        embed = discord.Embed(
            title="Best 50" if root in ("b50", "best50") else "CHUNITHM Penguin",
            color=0x57B9CF,
        )
        if files[0].filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            embed.set_image(url="attachment://" + files[0].filename)
        kwargs["embed"] = embed
    return content, kwargs


class PanelButton(discord.ui.Button):
    def __init__(
        self, ctx, label, action, *, row=0, style=discord.ButtonStyle.secondary,
        emoji=None,
    ):
        super().__init__(
            label=ui_text(ctx, label),
            row=row,
            style=style,
            emoji=emoji,
            custom_id=f"penguin:panel:{action}:{row}",
        )
        self.ctx, self.action = ctx, action

    async def callback(self, interaction):
        await navigate(self.ctx, interaction, self.action)


class SourceSelect(discord.ui.Select):
    def __init__(self, ctx):
        self.ctx = ctx
        source = session_for(ctx).route.source
        state = getattr(ctx, "_account_state", None)
        super().__init__(
            placeholder=ui_text(ctx, "選擇成績來源"),
            custom_id="penguin:panel:source",
            row=2,
            options=[
                discord.SelectOption(
                    label=ui_text(ctx, label),
                    value=source
                    if value == "chunirec:jp2" and source.startswith("chunirec:")
                    else value,
                    default=value == source
                    or value == "chunirec:jp2"
                    and source.startswith("chunirec:"),
                    description=state.source(
                        source
                        if value == "chunirec:jp2" and source.startswith("chunirec:")
                        else value
                    ).status
                    if state is not None
                    else None,
                )
                for label, value in SOURCES
                if session_for(ctx).route.section != "recent" or value == "net"
            ],
        )

    async def callback(self, interaction):
        session = session_for(self.ctx)
        if interaction.user.id != session.owner_id:
            await interaction.response.send_message(
                ui_text(self.ctx, "此面板只供開啟者操作。"), ephemeral=True
            )
            return
        if session.busy:
            await interaction.response.send_message(
                ui_text(self.ctx, "正在處理上一個操作，請稍候。"), ephemeral=True
            )
            return
        old = session.route
        session.route = PanelRoute(
            old.section, old.query, self.values[0], old.difficulty
        )
        await navigate(self.ctx, interaction, old.section, remember=False)


class ChunirecRegionSelect(discord.ui.Select):
    def __init__(self, ctx):
        self.ctx = ctx
        source = session_for(ctx).route.source
        super().__init__(
            placeholder=ui_text(ctx, "Chunirec 區域"),
            row=4,
            options=[
                discord.SelectOption(
                    label=region,
                    value=f"chunirec:{region}",
                    default=source == f"chunirec:{region}",
                )
                for region in ("jp2", "jp")
            ],
        )

    async def callback(self, interaction):
        session = session_for(self.ctx)
        if interaction.user.id != session.owner_id:
            await interaction.response.send_message(
                ui_text(self.ctx, "此面板只供開啟者操作。"), ephemeral=True
            )
            return
        if session.busy or session.closed:
            await interaction.response.send_message(
                ui_text(self.ctx, "面板正在處理或已關閉，請稍後重試。"), ephemeral=True
            )
            return
        old = session.route
        session.route = replace(old, source=self.values[0])
        await navigate(self.ctx, interaction, old.section, remember=False)


class SongPanelSelect(discord.ui.Select):
    def __init__(self, ctx):
        self.ctx = ctx
        session = session_for(ctx)
        if session.route.section == "alias":
            choices = [
                (ui_text(ctx, "新增別名"), "alias_add"),
                (ui_text(ctx, "移除別名"), "alias_remove"),
                (ui_text(ctx, "返回單曲"), "song_return"),
            ]
            if getattr(ctx, "_can_reload_alias", False):
                choices.insert(2, (ui_text(ctx, "重新載入別名"), "alias_reload"))
        else:
            action = "score" if session.route.section == "score" else "chart"
            choices = [(ui_text(ctx, "搜尋歌曲／簡寫"), "input:" + action)]
            if session.song_card is not None:
                choices.append((ui_text(ctx, "返回單曲"), "song_return"))
            if session.chart_results is not None:
                choices.append((ui_text(ctx, "返回篩選結果"), "return_results"))
        super().__init__(
            placeholder=ui_text(ctx, "選擇操作"),
            row=0,
            options=[
                discord.SelectOption(label=label, value=value)
                for label, value in choices
            ],
        )

    async def callback(self, interaction):
        await navigate(self.ctx, interaction, self.values[0])


class PanelView(PenguinView):
    _panel_own_navigation = True

    def __init__(self, ctx):
        super().__init__(ctx, timeout=600)
        self.session = session_for(ctx)
        mode = self.session.route.section
        self._is_main_menu = mode == "home"
        group = group_for(mode)
        state = getattr(ctx, "_account_state", None)
        selected = (
            state.source(self.session.route.source) if state is not None else None
        )
        buttons = {
            "home": (),
            "account": (
                (selected.primary_label, selected.primary_action),
                (ui_text(ctx, "帳號設定"), "account_settings"),
            )
            if selected is not None
            else (
                (ui_text(ctx, "連結帳號"), "link_source"),
                (ui_text(ctx, "帳號設定"), "account_settings"),
            ),
            "chart": (),
            "account_settings": (),
            "fun": (
                (ui_text(ctx, "Poopenguin 主頁"), "fun"),
                (ui_text(ctx, "抽卡面板"), "gacha"),
            ),
            "alias": (),
        }.get(mode, ())
        if mode == "account_settings":
            private = getattr(getattr(ctx, "user_config", None), "privacy_mode", False)
            buttons = [
                (
                    ui_text(ctx, "允許他人查詢")
                    if private
                    else ui_text(ctx, "停止分享成績"),
                    "privacy",
                )
            ]
            if selected is not None:
                if selected.can_logout:
                    buttons.append((ui_text(ctx, "登出 NET"), "confirm:logout"))
                if selected.can_unlink:
                    buttons.append((ui_text(ctx, "解除目前來源"), "confirm:unlink"))
                if selected.can_token:
                    buttons.append((ui_text(ctx, "查看 NET 憑證"), "token"))
            buttons.append((ui_text(ctx, "刪除全部本機資料"), "confirm:delete_account"))
        if group == "fun" and mode != "fun":
            buttons = (
                (ui_text(ctx, "Poopenguin 主頁"), "fun"),
                (ui_text(ctx, "抽卡面板"), "gacha"),
            )
        for label, action in buttons:
            self.add_item(PanelButton(ctx, label, action))
        if mode == "home":
            from .navigation import NavigationButton, menu_categories

            for action, label, emoji, _ in menu_categories(ctx):
                self.add_item(PanelButton(ctx, label, action, emoji=emoji))
            self.add_item(NavigationButton(ctx, "help", row=1))
        if group == "song" and mode in ("chart", "score", "alias"):
            self.add_item(SongPanelSelect(ctx))
        elif group != "home":
            self.add_item(PanelMenu(ctx, row=1))
        if mode in (
            "source_scores",
            "b50",
            "recent",
            "compare",
            "account",
            "sync",
            "account_settings",
        ):
            self.add_item(SourceSelect(ctx))
            if group == "net" and self.session.route.source.startswith("chunirec:"):
                self.add_item(ChunirecRegionSelect(ctx))
        if mode == "account" and self.session.route.source == "chunilib:export":
            self.add_item(
                discord.ui.Button(
                    label=ui_text(ctx, "開啟 Chunilib"), url=CHUNILIB_URL, row=0
                )
            )
        actions = {
            "source_scores": ((ui_text(ctx, "查詢此來源成績"), "input:source_scores"),),
            "b50": ((ui_text(ctx, "重新整理"), "refresh"),),
            "recent": ((ui_text(ctx, "重新整理"), "refresh"),),
            "compare": ((ui_text(ctx, "選擇歌曲"), "input:compare"),),
            "fun": (
                (ui_text(ctx, "幫我選"), "pick"),
                (ui_text(ctx, "問問企鵝"), "ask"),
                (ui_text(ctx, "隨機數字"), "rng"),
                (ui_text(ctx, "隨機顏色"), "rcg"),
                (ui_text(ctx, "抽卡"), "gacha"),
            ),
        }
        for label, action in actions.get(mode, ()):
            self.add_item(
                PanelButton(ctx, label, action, row=4 if mode == "compare" else 3)
            )
        if mode == "compare":
            self.add_item(CompareUserSelect(ctx))
        if group == "net":
            if mode != "account":
                self.add_item(
                    PanelButton(ctx, ui_text(ctx, "返回主資料"), "account", row=3)
                )
            if mode not in ("b50", "recent") and not (
                mode == "account"
                and selected is not None
                and selected.primary_action == "refresh"
            ):
                self.add_item(
                    PanelButton(
                        ctx,
                        ui_text(ctx, "重新整理狀態")
                        if mode in ("account", "account_settings")
                        else ui_text(ctx, "重試"),
                        "refresh",
                        row=3,
                    )
                )
        elif group == "song" and mode not in ("chart", "score", "alias"):
            if self.session.song_card is not None:
                self.add_item(
                    PanelButton(
                        ctx,
                        ui_text(ctx, "返回單曲"),
                        "song_return",
                        row=4 if mode == "compare" else 3,
                    )
                )
            else:
                self.add_item(
                    PanelButton(
                        ctx,
                        ui_text(ctx, "返回歌曲搜尋"),
                        "song_home",
                        row=4 if mode == "compare" else 3,
                    )
                )
        if (
            self.session.retry_action is not None
            and group in ("net", "song")
            and mode not in ("chart", "score", "alias")
        ):
            self.add_item(
                PanelButton(
                    ctx,
                    ui_text(ctx, "重試上次操作"),
                    "retry_last",
                    row=4 if mode == "compare" else 3,
                )
            )
        if mode in FORM_COMMANDS and FORM_COMMANDS[mode][2] and mode != "alias":
            self.add_item(PanelButton(ctx, ui_text(ctx, "修改參數"), mode, row=3))
        from .dismiss import ensure_close

        ensure_close(self, ctx.author.id, ctx=ctx)
        self.session.active_view = self

    async def on_timeout(self):
        await super().on_timeout()
        group = group_for(self.session.route.section)
        if (
            group not in ("net", "song")
            or self.message is None
            or getattr(self, "_panel_inactive", False)
        ):
            return
        try:
            await self.message.edit(
                content=ui_text(self.ctx, "面板已逾時，請重新開啟 /account。")
                if group == "net"
                else ui_text(self.ctx, "面板已逾時，請重新開啟 /chart。"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass


class CompareUserSelect(discord.ui.UserSelect):
    def __init__(self, ctx):
        super().__init__(
            placeholder=ui_text(ctx, "選擇要比較的玩家"),
            custom_id="penguin:panel:compare_user",
            row=3,
            min_values=1,
            max_values=1,
        )
        self.ctx = ctx

    async def callback(self, interaction):
        session = session_for(self.ctx)
        await navigate(
            self.ctx,
            interaction,
            "compare",
            values={"user_id": str(self.values[0].id), "query": session.route.query},
        )


class PanelModal(discord.ui.Modal):
    def __init__(self, ctx, action, title, fields):
        super().__init__(title=ui_text(ctx, title)[:45], timeout=300)
        self.ctx, self.action = ctx, action
        self.inputs = {}
        for key, label, required in fields:
            session = session_for(ctx)
            default = (
                session.route.query
                if key == "query"
                else session.parameters.get(action, {}).get(key)
            )
            item = discord.ui.TextInput(
                label=ui_text(ctx, label)[:45],
                required=required,
                max_length=300,
                default=default or None,
            )
            self.inputs[key] = item
            self.add_item(item)

    async def on_submit(self, interaction):
        if interaction.user.id != session_for(self.ctx).owner_id:
            await interaction.response.send_message(
                ui_text(self.ctx, "此表單只供開啟者操作。"), ephemeral=True
            )
            return
        values = {key: item.value.strip() for key, item in self.inputs.items()}
        await navigate(self.ctx, interaction, self.action, values=values)

    async def on_error(self, interaction, error):
        await logger.awarning("Panel modal failed", error_type=type(error).__name__)
        message = ui_text(self.ctx, "此操作暫時無法完成，請回到面板重試。")
        updated = component_context(self.ctx, interaction)
        await defer_panel(updated, interaction)
        await show_panel(
            updated, session_for(self.ctx).route.section, description=message
        )


async def show_panel(ctx, section="home", *, description=None):
    if section in ("account", "sync"):
        return await show_account(ctx, notice=description)
    session = session_for(ctx)
    session.move(section, remember=False)
    if group_for(section) == "net":
        from .account_state import load_account_state

        ctx._account_state = await load_account_state(ctx)
    if section == "account_settings" and description is None:
        settings = await ctx.bot.services.users.fetch_user_config(ctx.author.id)
        ctx.user_config = settings
        description = (
            ui_text(ctx, "成績分享：")
            + (
                ui_text(ctx, "僅本人可查詢")
                if settings.privacy_mode
                else ui_text(ctx, "允許其他玩家查詢與比較")
            )
            + ui_text(ctx, "。\n解除連結與刪除資料需要再次確認。")
        )
    descriptions = {
        "home": ui_text(ctx, "選擇要開啟的功能。操作會在同一則訊息中更新。"),
        "chart": ui_text(ctx, "從選單搜尋歌曲，開啟視譜。"),
        "score": ui_text(ctx, "從選單搜尋歌曲，開啟個人成績。"),
        "source_scores": ui_text(
            ctx, "查詢目前來源的原始成績，包含尚未對應曲庫的匯入資料。"
        ),
        "compare": ui_text(
            ctx, "指定玩家與歌曲進行來源成績比較。最近訊息比較請使用 /compare。"
        ),
        "account_settings": ui_text(
            ctx,
            "設定成績分享，或管理目前來源的連結與本機資料。刪除與解除連結需要確認。",
        ),
        "tools": ui_text(ctx, "輸入數值試算，結果可繼續修改或回到其他功能。"),
        "fun": ui_text(ctx, "選一個工具，填入內容後即可互動。更多娛樂功能在選單。"),
        "sync": ui_text(ctx, "選擇來源後同步；Chunilib 請選取匯出檔來源並按匯入檔案。"),
    }
    embed = discord.Embed(
        title=ui_text(ctx, dict(SECTIONS).get(section, "CHUNITHM Penguin")),
        description=description
        or descriptions.get(section, ui_text(ctx, "使用按鈕與選單繼續操作。")),
        color=0x57B9CF,
    )
    embed.set_footer(
        text=ui_text(ctx, "只有開啟者能操作 · 10 分鐘無操作後請重新開啟 /account")
        if group_for(section) == "net"
        else ui_text(ctx, "只有開啟者能操作 · 10 分鐘無操作後請重新開啟 /chart")
        if group_for(section) == "song"
        else ui_text(ctx, "只有開啟者能操作")
    )
    if section == "home":
        from .navigation import main_menu_embed

        embed = main_menu_embed(ctx)
        if description:
            embed.description = description + "\n\n" + (embed.description or "")
    await retire_previous(ctx)
    view = PanelView(ctx)
    view.message = await ctx.respond_or_edit(
        embed=embed,
        view=view,
        ephemeral=private_message(getattr(ctx, "response", None)),
    )
    bind_panel_message(ctx, view.message)
    return view


async def retire_previous(ctx):
    previous = session_for(ctx).active_view
    if previous is None or getattr(previous, "_navigation_retired", False):
        return
    previous._navigation_retired = True
    previous._panel_inactive = True
    try:
        for method in ("close_network", "deactivate"):
            cleanup = getattr(previous, method, None)
            if cleanup is not None:
                try:
                    await cleanup()
                except Exception as error:
                    await logger.awarning(
                        "Panel cleanup failed", error_type=type(error).__name__
                    )
    finally:
        previous.stop()


async def show_account(ctx, *, notice=None):
    """Data landing page; status is read from existing account/source stores."""
    session = session_for(ctx)
    session.move("account", remember=False)
    from .account_state import load_account_state

    state = ctx._account_state = await load_account_state(ctx)
    selected = state.source(session.route.source)
    embed = discord.Embed(
        title=ui_text(ctx, "主資料介面"),
        description=notice[:3000]
        if notice
        else ui_text(ctx, "選擇來源，管理帳號連結、同步及個人紀錄。"),
        color=0x57B9CF,
    )
    embed.add_field(
        name=ui_text(ctx, "帳號連結"),
        value="\n".join(
            f"**{source.label}**："
            + (
                "；".join(
                    f"{region} {state.source('chunirec:' + region).status}"
                    for region in ("jp2", "jp")
                )
                if source.key == "chunirec:jp2"
                else source.status
            )
            for source in state.sources
        ),
        inline=False,
    )
    embed.add_field(
        name=ui_text(ctx, "目前來源"),
        value=ui_text(
            ctx,
            "{0}\n最後同步：{1}",
            selected.label,
            selected.last_sync or ui_text(ctx, "尚未同步"),
        )
        + (f"\n{selected.notice}" if selected.notice else ""),
        inline=False,
    )
    embed.set_footer(text=ui_text(ctx, "只有開啟者能操作 · 逾時請重新開啟 /account"))
    await retire_previous(ctx)
    view = PanelView(ctx)
    view.message = await ctx.respond_or_edit(
        embed=embed,
        view=view,
        ephemeral=private_message(getattr(ctx, "response", None)),
    )
    bind_panel_message(ctx, view.message)
    return view


async def show_song(ctx, query, *, tab="overview"):
    from .song_details import SongDetailsState, SongDetailsView

    if getattr(getattr(ctx.bot, "utils", None), "aliases", None) is not None:
        from .song_search import discover_song

        return await discover_song(ctx, query, tab=tab)

    result = await ctx.find_songs(query, load_global_aliases=True)
    if result is None:
        return None
    route = session_for(ctx).route
    view = SongDetailsView(
        ctx, result.songs, synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket
    )
    view.state = SongDetailsState(
        tab=tab,
        difficulty=route.difficulty,
        network="chunithm_net" if route.source == "net" else route.source,
    )
    await view.start()
    return view


async def restore_song_panel(ctx):
    """Recreate a closed reader without losing the selected canonical variant."""
    from .song_details import SongDetailsView

    saved = session_for(ctx).song_card
    if saved is None:
        return await show_panel(
            ctx, "chart", description=ui_text(ctx, "請先搜尋歌曲。")
        )
    await retire_previous(ctx)
    view = SongDetailsView(
        ctx,
        saved["songs"],
        variant_charts=saved["variant_charts"],
        synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
    )
    view.state = saved["state"]
    view.search_query = saved["search_query"]
    await view.start()
    return view


async def open_chart_result(ctx, interaction, chart, *, origin_view):
    """Open exactly the selected chart, never a fuzzy replacement from a list."""
    session = session_for(ctx)
    if interaction.user.id != session.owner_id or getattr(
        origin_view, "_panel_inactive", False
    ):
        await interaction.response.send_message(
            ui_text(ctx, "請使用自己的目前篩選結果。"), ephemeral=True
        )
        return
    if session.busy:
        await interaction.response.send_message(
            ui_text(ctx, "正在處理上一個操作，請稍候。"), ephemeral=True
        )
        return
    session.busy = True
    updated = component_context(ctx, interaction)
    try:
        await defer_panel(updated, interaction)
        await check_route(updated, "chart")
        async with ctx.bot.database.mapping_read_sessionmaker() as db:
            actual = await db.scalar(
                select(Chart)
                .where(Chart.id == chart.id)
                .options(joinedload(Chart.song))
            )
        if (
            actual is None
            or actual.song_id != chart.song_id
            or actual.difficulty != chart.difficulty
        ):
            await show_panel(
                updated,
                "chart",
                description=ui_text(ctx, "這張譜面已不存在，請重新篩選。"),
            )
            return
        session.chart_results = dict(
            charts=origin_view.charts,
            page=origin_view.page,
            title=origin_view.title,
            query_label=origin_view.query_label,
            route=session.route,
            parameters=copy.deepcopy(session.parameters),
        )
        from chuni_penguin.services.song_discovery import (
            DiscoveryResult,
            SongDiscoveryService,
        )

        from .processing import processing_response
        from .song_search import open_song_candidate

        async with processing_response(updated, "開啟選取譜面"):
            discovered = await SongDiscoveryService(
                ctx.bot.database, ctx.bot.utils.aliases
            ).search(actual.song.title, guild_id=ctx.guild.id if ctx.guild else None)
            candidate = next(
                (
                    candidate
                    for candidate in discovered.candidates
                    if any(song.id == actual.song_id for song in candidate.songs)
                ),
                None,
            )
            if candidate is None:
                raise commands.BadArgument(
                    ui_text(ctx, "選取譜面已無法對應歌曲，請重新篩選。")
                )
            selected = next(
                song for song in candidate.songs if song.id == actual.song_id
            )
            candidate = replace(
                candidate,
                song=selected,
                method="title",
                variants=tuple(
                    song for song in candidate.songs if song.id != actual.song_id
                ),
            )
            session.route = replace(
                session.route, query=actual.song.title, difficulty=actual.difficulty
            )
            await open_song_candidate(
                updated,
                DiscoveryResult(actual.song.title, (candidate,), False, False),
                0,
                tab="chart",
            )
    except (commands.CommandError, ValueError) as error:
        await show_panel(updated, "chart", description=str(error)[:1000])
    except Exception as error:
        await logger.awarning(
            "Opening selected chart failed", error_type=type(error).__name__
        )
        await show_panel(
            updated,
            "chart",
            description=ui_text(ctx, "此譜面暫時無法開啟，請返回篩選重試。"),
        )
    finally:
        session.busy = False


async def restore_chart_results(ctx):
    from .chart_results import ChartResultsView

    saved = session_for(ctx).chart_results
    if saved is None:
        return await show_panel(
            ctx, "chart", description=ui_text(ctx, "篩選結果已過期，請重新查詢。")
        )
    session = session_for(ctx)
    await retire_previous(ctx)
    session.route = saved["route"]
    session.parameters = copy.deepcopy(saved["parameters"])
    await check_route(ctx, session.route.section)
    view = ChartResultsView(
        ctx,
        saved["charts"],
        title=saved["title"],
        query_label=saved["query_label"],
        page=saved["page"],
    )
    await view.start()
    return view


async def refresh_account_parent(ctx):
    """Refresh only safe source status on a still-relevant public parent."""
    parent = getattr(ctx, "_account_parent", None)
    if parent is None or private_message(getattr(parent, "response", None)):
        return
    if session_for(parent).closed:
        return
    if session_for(parent).route.section not in ("account", "account_settings", "sync"):
        return
    try:
        await show_account(parent)
    except discord.HTTPException:
        pass


async def show_song_aliases(ctx, *, notice=""):
    saved = session_for(ctx).song_card
    song = saved["songs"][saved["state"].song_index]
    rows = await ctx.bot.utils.aliases.for_song(song.id)
    visible = [
        row
        for row in rows
        if row.guild_id == 0 or ctx.guild is not None and row.guild_id == ctx.guild.id
    ]
    lines = [
        f"{ui_text(ctx, '全域') if row.guild_id == 0 else ui_text(ctx, '本伺服器')}：{discord.utils.escape_markdown(row.alias)}"
        for row in visible
    ]
    ctx._can_reload_alias = await ctx.bot.is_owner(ctx.author)
    description = (
        f"**{discord.utils.escape_markdown(song.title)} [{saved['state'].difficulty}]**\n"
        + ("\n".join(lines) if lines else ui_text(ctx, "尚無可用別名。"))
    )
    if notice:
        description = notice + "\n\n" + description
    await show_panel(ctx, "alias", description=description[:4000])


async def _render_route(ctx, action, values):
    from .components.score_card_embed import ScoreCardEmbed
    from .embeds import EmbedPaginationView

    session = session_for(ctx)
    route = session.route
    players = ctx.bot.get_cog("V2Players")
    if action == "song_return":
        await restore_song_panel(ctx)
    elif action == "return_results":
        await restore_chart_results(ctx)
    elif action == "song_home":
        session.route = replace(route, section="chart", query="")
        session.song_card = None
        session.chart_results = None
        await show_panel(ctx, "chart")
    elif action == "alias" and session.song_card is not None:
        await show_song_aliases(ctx)
    elif action == "alias_add" and session.song_card is not None:
        from chuni_penguin.services.alias_learning import AliasLearningService

        if ctx.guild is None:
            raise commands.NoPrivateMessage
        saved = session.song_card
        song = saved["songs"][saved["state"].song_index]
        try:
            result = await AliasLearningService(
                ctx.bot.database, ctx.bot.utils.aliases
            ).confirm(
                values["added_alias"],
                guild_id=ctx.guild.id,
                song_id=song.id,
                owner_id=ctx.author.id,
            )
        except ValueError as error:
            raise commands.BadArgument(
                ui_text(ctx, "請使用 1–100 字的一般文字作為別名。")
            ) from error
        if result.status not in ("added", "already_exists"):
            titles = "、".join(
                dict.fromkeys(conflict.title for conflict in result.conflicts)
            )
            raise commands.BadArgument(
                ui_text(ctx, "名稱已指向 {0}，請換個別名。", titles[:800])
            )
        await show_song_aliases(
            ctx,
            notice=ui_text(ctx, "已加入本伺服器共用別名。")
            if result.status == "added"
            else ui_text(ctx, "此別名已存在，未重複新增。"),
        )
    elif action in FORM_COMMANDS:
        await invoke_existing(ctx, FORM_COMMANDS[action][1], values)
        if action in ("alias_remove", "alias_reload") and session.song_card is not None:
            await show_song_aliases(ctx, notice=ui_text(ctx, "別名操作已完成。"))
    elif action in ("chart", "score", "source_scores"):
        if not route.query:
            await show_panel(ctx, action)
        elif action in ("chart", "score"):
            await show_song(
                ctx, route.query, tab="scores" if action == "score" else "view"
            )
        else:
            rows = await players.matching_rows(ctx, route.query, route.source)
            embeds = [
                ScoreCardEmbed(row["record"], language=get_chunithm_language(ctx))
                if "record" in row
                else discord.Embed(
                    title=row["title"][:256],
                    description=f"{row['diff']} · {row['score']:,}",
                ).set_footer(text=route.source)
                for row in rows
            ]
            if embeds:
                await EmbedPaginationView(ctx, embeds).start()
            else:
                await show_panel(
                    ctx,
                    action,
                    description=ui_text(ctx, "此來源沒有符合的已遊玩成績。"),
                )
    elif action in ("b50", "recent"):
        if route.source not in ("net", "kamaitachi"):
            if action == "recent":
                await show_panel(
                    ctx,
                    action,
                    description=ui_text(
                        ctx,
                        "此來源沒有可驗證的最近遊玩歷史；PB 更新時間不能當作遊玩紀錄。",
                    ),
                )
                return
            payload, row = await players.sources.records(ctx.author.id, route.source)
            from chuni_penguin.calculation import calculate_rating

            ranked = sorted(
                (
                    (calculate_rating(r["score"], r["source_const"]), r)
                    for r in payload["records"]
                    if r.get("source_const") is not None
                ),
                key=lambda item: item[0],
                reverse=True,
            )[:50]
            if not ranked:
                raise SourceError(
                    ui_text(ctx, "此來源缺少可信定數，請先使用個人成績查詢。")
                )
            embeds = [
                discord.Embed(
                    title="Best 50",
                    description="\n".join(
                        f"{index + offset + 1}. {r['title'][:150]} [{r['diff']}] · {r['score']:,} · {rating:.2f}"
                        for offset, (rating, r) in enumerate(ranked[index : index + 10])
                    ),
                ).set_footer(
                    text=ui_text(
                        ctx,
                        "{0} · 本機排序，並非遊戲內 Rating 組成 · {1}",
                        route.source,
                        row.synced_at,
                    )
                )
                for index in range(0, len(ranked), 10)
            ]
            await EmbedPaginationView(ctx, embeds).start()
        else:
            if route.source == "net":
                cookie = await ctx.bot.database.cookies.get_by_discord_id(ctx.author.id)
                if cookie is None or not cookie.cookie.startswith("#LWP-Cookies-2.0"):
                    await show_panel(
                        ctx,
                        "account",
                        description=ui_text(ctx, "NET 尚未登入，請按下登入。"),
                    )
                    return
            if action == "b50":
                await players.best._best50_inner(
                    ctx, kamaitachi=route.source == "kamaitachi"
                )
            else:
                await players.recent_native._recent_inner(
                    ctx, kamaitachi=route.source == "kamaitachi"
                )
    elif action == "account":
        await show_account(ctx)
    elif action == "privacy":
        users = ctx.bot.services.users
        settings = await users.fetch_user_config(ctx.author.id)
        settings.privacy_mode = not settings.privacy_mode
        await users.save_user_config(
            settings, is_new=await users.stored_user_config(ctx.author.id) is None
        )
        await show_panel(ctx, "account_settings")
    elif action == "login":
        await invoke_existing(ctx, "login")
    elif action == "link_source":
        text = (
            ui_text(
                ctx,
                "Kamaitachi 需先由管理者配置並啟用原模組，再使用 /kamaitachi link 連結。",
            )
            if route.source == "kamaitachi"
            else ui_text(
                ctx, "請選擇 Chunilib 匯出檔來源，按「匯入檔案」上傳自己的匯出檔。"
            )
        )
        await show_account(ctx, notice=text)
    elif action == "logout":
        await invoke_existing(ctx, "logout")
        await show_account(ctx, notice=ui_text(ctx, "已登出 NET。"))
        await refresh_account_parent(ctx)
    elif action == "token":
        value = await ctx.bot.get_cog("Auth").token_value(ctx.author.id)
        await ctx.respond_or_edit(
            ui_text(ctx, "NET 憑證：||{0}||\n請勿提供給其他人。", value), ephemeral=True
        )
    elif action == "unlink":
        if route.source not in ("chunirec:jp2", "chunirec:jp", "chunilib:export"):
            raise SourceError(
                ui_text(ctx, "NET 請使用登出；Kamaitachi 請使用其原帳號管理功能。")
            )
        await players.sources.unlink(ctx.author.id, route.source)
        await show_account(ctx, notice=ui_text(ctx, "已解除此來源並刪除本機匯入資料。"))
        await refresh_account_parent(ctx)
    elif action == "delete_account":
        text = await players.delete_own_data(ctx.author.id, ctx)
        await show_account(ctx, notice=text)
        await refresh_account_parent(ctx)
    elif action == "run_sync":
        from .entrypoints import panel_command

        command = panel_command(ctx.bot, "sync")
        for check in command.checks:
            if not await discord.utils.maybe_coroutine(check, ctx.interaction):
                raise commands.CheckFailure(ui_text(ctx, "此功能目前無法使用。"))
        if route.source.startswith("chunirec:"):
            result = await players.sources.sync_chunirec(
                ctx.author.id, region=route.source.split(":")[1]
            )
            text = players.sync_result(result, route.source, ctx)
        elif route.source == "chunilib:export":
            text = ui_text(
                ctx, "請選擇 Chunilib 匯出檔來源，按「匯入檔案」上傳自己的匯出檔。"
            )
        else:
            text = await players.sync_native(ctx, route.source)
        await show_panel(ctx, "sync", description=text)
    elif action == "tags":
        rows = await ctx.bot.phase1_charts.tag_matches(values["tag"])
        from .chart_results import ChartResultsView

        ids = list(dict.fromkeys(row["chart_id"] for _, row in rows))
        async with ctx.bot.database.mapping_read_sessionmaker() as db:
            charts = (
                list(
                    await db.scalars(
                        select(Chart)
                        .where(Chart.id.in_(ids))
                        .options(joinedload(Chart.song))
                    )
                )
                if ids
                else []
            )
        await ChartResultsView(
            ctx,
            charts,
            title=ui_text(ctx, "人工標籤"),
            query_label=ui_text(ctx, "配置標籤：{0}", values["tag"]),
        ).start()
    elif action == "compare":
        target = values.get("user_id", "")
        if not target or not route.query:
            await show_panel(ctx, "compare")
            return
        if not target.isdecimal() or not 0 < int(target) < 2**64:
            raise commands.BadArgument(ui_text(ctx, "請輸入有效的 Discord 玩家 ID。"))
        settings = await ctx.bot.services.users.fetch_user_config(int(target))
        if int(target) != ctx.author.id and settings.privacy_mode:
            raise SourceError(ui_text(ctx, "該玩家尚未允許分享成績。"))
        saved = session.song_card
        selected_song = (
            saved["songs"][saved["state"].song_index] if saved is not None else None
        )
        if selected_song is not None and route.query == selected_song.title:
            mine = [
                row
                for row in await players.source_rows(
                    ctx, route.source, song_id=selected_song.id
                )
                if row.get("song_id") == selected_song.id
                and row["diff"] == route.difficulty
            ]
            theirs = [
                row
                for row in await players.source_rows(
                    ctx,
                    route.source,
                    SimpleNamespace(id=int(target)),
                    song_id=selected_song.id,
                )
                if row.get("song_id") == selected_song.id
                and row["diff"] == route.difficulty
            ]
        else:
            mine = await players.matching_rows(
                ctx, route.query, route.source, route.difficulty
            )
            theirs = await players.matching_rows(
                ctx,
                route.query,
                route.source,
                route.difficulty,
                SimpleNamespace(id=int(target)),
            )
        theirs = {(r["id"], r["diff"]): r for r in theirs}
        pairs = [
            (r, theirs[(r["id"], r["diff"])])
            for r in mine
            if (r["id"], r["diff"]) in theirs
        ]
        lines = [
            ui_text(
                ctx,
                "**{0}** [{1}]\n{2:,} vs {3:,} · 差 {4:+,}",
                discord.utils.escape_markdown(a["title"][:150]),
                a["diff"],
                a["score"],
                b["score"],
                a["score"] - b["score"],
            )
            for a, b in pairs
        ]
        deltas = [a["score"] - b["score"] for a, b in pairs]
        summary = (
            ui_text(
                ctx,
                "共同譜面 {0} 張 · 領先 {1}／平手 {2}／落後 {3} · 平均分差 {4:+,.1f}\n\n",
                len(deltas),
                sum(d > 0 for d in deltas),
                sum(d == 0 for d in deltas),
                sum(d < 0 for d in deltas),
                sum(deltas) / len(deltas),
            )
            if deltas
            else ""
        )
        embeds = [
            discord.Embed(
                title=ui_text(ctx, "成績比較"),
                description=summary + "\n\n".join(lines[index : index + 5]),
            ).set_footer(text=route.source)
            for index in range(0, len(lines), 5)
        ]
        if embeds:
            await EmbedPaginationView(ctx, embeds).start()
        else:
            await show_panel(
                ctx,
                action,
                description=ui_text(ctx, "雙方沒有此來源可對應的共同成績。"),
            )
    else:
        await show_panel(ctx, action)


async def navigate(ctx, interaction, action, *, values=None, remember=True):
    if action in ("menu", "help"):
        from .navigation import open_navigation

        return await open_navigation(ctx, interaction, action)
    session = session_for(ctx)
    origin_session = session
    if session.closed:
        await interaction.response.send_message(
            ui_text(ctx, "面板已關閉，請重新開啟對應指令。"), ephemeral=True
        )
        return
    if interaction.user.id != session.owner_id:
        await interaction.response.send_message(
            ui_text(ctx, "此面板只供開啟者操作。"), ephemeral=True
        )
        return
    current_group = group_for(session.route.section)
    if action == "home":
        action = group_home(current_group)
    target = (
        action.split(":", 1)[-1]
        if action.startswith(("input:", "confirm:"))
        else action
    )
    if (
        current_group != "home"
        and action not in ("back", "refresh", "retry_last")
        and group_for(target) != current_group
    ):
        updated = component_context(ctx, interaction)
        await defer_panel(updated, interaction)
        await show_panel(
            updated,
            session.route.section,
            description=ui_text(
                ctx, "此功能屬於其他類別，請使用對應指令開啟獨立介面。"
            ),
        )
        return
    pending = session.pending_login
    if pending is not None and pending is not asyncio.current_task():
        session.pending_login = None
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    if session.busy:
        await interaction.response.send_message(
            ui_text(ctx, "正在處理上一個操作，請稍候。"), ephemeral=True
        )
        return
    if action == "retry_last":
        retry = session.retry_action
        if retry is None:
            action = "refresh"
        else:
            action, values = retry["action"], retry["values"]
    if action == "chart" and values and values.get("query"):
        session.chart_results = None
    if action.startswith("input:"):
        kind = action.split(":", 1)[1]
        fields = (("query", ui_text(ctx, "歌曲名稱或別名"), True),)
        await interaction.response.send_modal(
            PanelModal(ctx, kind, ui_text(ctx, "查詢歌曲與成績"), fields)
        )
        return
    if (
        action in ("chart", "score", "source_scores")
        and not session.route.query
        and values is None
    ):
        await navigate(ctx, interaction, "input:" + action)
        return
    if action in FORM_COMMANDS and FORM_COMMANDS[action][2] and values is None:
        title, _, fields = FORM_COMMANDS[action]
        if action == "alias" and session.song_card is not None:
            fields = ()
        if action == "alias_add" and session.song_card is not None:
            fields = (("added_alias", ui_text(ctx, "新增別名（本伺服器共用）"), True),)
        if fields:
            await interaction.response.send_modal(
                PanelModal(ctx, action, title, fields)
            )
            return
    if action == "tags" and values is None:
        await interaction.response.send_modal(
            PanelModal(
                ctx,
                action,
                ui_text(ctx, "搜尋人工標籤"),
                (("tag", ui_text(ctx, "配置標籤"), True),),
            )
        )
        return
    if action == "import":
        await interaction.response.send_modal(ImportPlayerModal(ctx))
        return
    if action == "link_source":
        if session.route.source == "net":
            action = "login"
        elif session.route.source.startswith("chunirec:"):
            action = "link"
    if action == "link":
        from chuni_penguin.cogs.v2_players import ChunirecLinkModal

        region = (
            session.route.source.split(":")[1]
            if session.route.source.startswith("chunirec:")
            else "jp2"
        )
        try:
            await check_route(component_context(ctx, interaction), "account")
        except commands.CommandError:
            await interaction.response.send_message(
                ui_text(ctx, "此功能目前無法使用。"), ephemeral=True
            )
            return
        await interaction.response.send_modal(
            ChunirecLinkModal(
                ctx.bot.get_cog("V2Players"),
                session.owner_id,
                region,
                ctx=sensitive_context(ctx, interaction),
            )
        )
        return
    if action.startswith("confirm:"):
        confirm_ctx = sensitive_context(ctx, interaction)
        target_action = action.split(":", 1)[1]
        view = ConfirmPanel(confirm_ctx, target_action)
        prompt = {
            "logout": ui_text(ctx, "確定要登出自己的 NET 帳號嗎？"),
            "unlink": ui_text(ctx, "確定解除目前來源並刪除此來源的本機匯入資料？"),
            "delete_account": ui_text(
                ctx,
                "確定刪除自己的本機帳號、成績、偏好、譜面評價與評論者背景？來源網站資料不受影響。",
            ),
        }[target_action]
        if private_message(interaction.message):
            await interaction.response.edit_message(
                content=None,
                embeds=[
                    discord.Embed(title=ui_text(ctx, "確認操作"), description=prompt)
                ],
                attachments=[],
                view=view,
            )
            view.message = interaction.message
            bind_panel_message(confirm_ctx, view.message)
        else:
            await interaction.response.send_message(
                embed=discord.Embed(title=ui_text(ctx, "確認操作"), description=prompt),
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()
            bind_panel_message(confirm_ctx, view.message)
        session_for(confirm_ctx).active_view = view
        return
    session.busy = True
    before, old_history = session.route, list(session.history)
    previous = session.active_view
    try:
        updated = (
            sensitive_context(ctx, interaction)
            if action in ("login", "token", "logout", "unlink", "delete_account")
            else component_context(ctx, interaction)
        )
        if updated.panel_session is not session:
            session = updated.panel_session
            previous = None
        await defer_panel(updated, interaction)
        updated.user_config = await ctx.bot.services.users.fetch_user_config(
            session.owner_id
        )
        if action == "refresh":
            action = session.route.section
            remember = False
        session.move(action, remember=remember)
        if action == "recent" and session.route.source not in ("net", "kamaitachi"):
            current = session.route
            session.route = PanelRoute(
                current.section, current.query, "net", current.difficulty
            )
        if values and values.get("query"):
            current = session.route
            session.route = PanelRoute(
                current.section, values["query"], current.source, current.difficulty
            )
        target_name = {
            "home": "help",
            "tools": "calculate",
            "fun": "pick",
            "privacy": "privacy",
            "run_sync": "sync",
            "link_source": "account",
            "account": "account",
            "account_settings": "account",
            "source_scores": "score",
            "unlink": "account unlink",
            "delete_account": "account delete",
            "song_home": "chart",
            "song_return": "chart",
            "return_results": "chart",
        }.get(session.route.section, session.route.section)
        if target_name.startswith("alias_"):
            target_name = FORM_COMMANDS[target_name][1]
        await check_route(updated, target_name)
        if group_for(session.route.section) == "net":
            from .account_state import load_account_state

            updated._account_state = await load_account_state(updated)
        if previous is not None:
            await retire_previous(updated)
        if values:
            session.parameters[session.route.section] = {
                **session.parameters.get(session.route.section, {}),
                **values,
            }
        from .processing import processing_response

        session.retry_action = None
        async with processing_response(
            updated, dict(SECTIONS).get(session.route.section, "資料")
        ):
            await _render_route(
                updated,
                session.route.section,
                session.parameters.get(session.route.section, {}),
            )
        session.retry_action = None
    except (commands.CommandError, SourceError) as error:
        session.retry_action = {"action": action, "values": values}
        session.route, session.history = before, old_history
        updated = component_context(ctx, interaction)
        await show_panel(
            updated, before.section, description=ui_text(ctx, str(error))[:1500]
        )
    except discord.app_commands.CommandOnCooldown:
        session.retry_action = {"action": action, "values": values}
        session.route, session.history = before, old_history
        await show_panel(
            component_context(ctx, interaction),
            before.section,
            description=ui_text(ctx, "操作太頻繁，請稍後再試。"),
        )
    except Exception as error:
        session.retry_action = {"action": action, "values": values}
        session.route, session.history = before, old_history
        await logger.awarning(
            "Panel action failed", action=action, error_type=type(error).__name__
        )
        updated = component_context(ctx, interaction)
        await show_panel(
            updated,
            before.section,
            description=ui_text(ctx, "此操作暫時無法完成，請重新整理或切換功能。"),
        )
    finally:
        session.busy = False
        origin_session.busy = False
        if (
            previous is not None
            and session.active_view is not previous
            and not getattr(previous, "_navigation_retired", False)
        ):
            previous._panel_inactive = True
            previous.stop()


class ImportPlayerModal(discord.ui.Modal, title="匯入自己的 Chunilib 匯出檔"):
    def __init__(self, ctx):
        super().__init__(title=ui_text(ctx, "匯入自己的 Chunilib 匯出檔"), timeout=300)
        self.ctx = ctx
        self.upload = discord.ui.FileUpload(min_values=1, max_values=1)
        self.add_item(
            discord.ui.Label(
                text=ui_text(ctx, "自己的 player-data JSON（最大 5 MiB）"),
                component=self.upload,
            )
        )

    async def on_submit(self, interaction):
        if interaction.user.id != session_for(self.ctx).owner_id:
            await interaction.response.send_message(
                ui_text(self.ctx, "此表單只供本人使用。"), ephemeral=True
            )
            return
        ctx = sensitive_context(self.ctx, interaction)
        await defer_panel(ctx, interaction)
        try:
            await check_route(ctx, "sync")
            from .entrypoints import panel_command

            for check in panel_command(ctx.bot, "sync").checks:
                if not await discord.utils.maybe_coroutine(check, interaction):
                    raise commands.CheckFailure(
                        ui_text(self.ctx, "此操作目前無法使用。")
                    )
            attachment = self.upload.values[0]
            if attachment.size > 5 * 1024 * 1024:
                raise SourceError(ui_text(self.ctx, "匯出檔不可超過 5 MiB。"))
            players = ctx.bot.get_cog("V2Players")
            result = await players.sources.import_chunilib(
                ctx.author.id, await attachment.read()
            )
            old = session_for(ctx).route
            session_for(ctx).route = PanelRoute(
                "account", old.query, "chunilib:export", old.difficulty
            )
            await show_account(
                ctx, notice=players.sync_result(result, "chunilib:export", ctx)
            )
            await refresh_account_parent(ctx)
        except (
            commands.CommandError,
            discord.app_commands.CommandOnCooldown,
            SourceError,
        ) as error:
            await show_account(ctx, notice=ui_text(ctx, str(error))[:1000])

    async def on_error(self, interaction, error):
        await logger.awarning(
            "Player import form failed", error_type=type(error).__name__
        )
        ctx = sensitive_context(self.ctx, interaction)
        await defer_panel(ctx, interaction)
        await show_account(
            ctx, notice=ui_text(self.ctx, "匯入失敗，請確認匯出檔格式後重試。")
        )


class ConfirmPanel(PenguinView):
    _panel_own_navigation = True

    def __init__(self, ctx, action):
        super().__init__(ctx, timeout=60)
        self.add_item(
            PanelButton(
                ctx, ui_text(ctx, "確認操作"), action, style=discord.ButtonStyle.danger
            )
        )
        self.add_item(PanelButton(ctx, ui_text(ctx, "取消"), "account"))
        from .dismiss import ensure_close

        ensure_close(self, ctx.author.id, ctx=ctx)
