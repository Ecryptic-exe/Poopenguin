"""Global menu/help navigation for the existing owner-bound panels."""

import asyncio
from contextlib import suppress

import discord

from chuni_penguin.utils.chunithm_i18n import ui_text

MENU_ID = "penguin:navigation:menu"
HELP_ID = "penguin:navigation:help"

# Only list features whose commands are actually loaded. These are the existing
# four panel groups, not a second command implementation or a separate router.
CATEGORIES = (
    (
        "account",
        "主資料介面",
        "👤",
        (
            ("account", "帳號連結"),
            ("sync", "同步資料"),
            ("b50", "Best 50"),
            ("recent", "最近遊玩"),
        ),
    ),
    (
        "chart",
        "歌曲／譜面",
        "🎵",
        (
            ("chart", "搜尋歌曲與譜面"),
            ("score", "我的成績"),
            ("find", "篩選譜面"),
            ("alias", "歌曲別名"),
        ),
    ),
    (
        "fun",
        "Poopenguin",
        "🐧",
        (
            ("pick", "幫我選"),
            ("ask", "問問企鵝"),
            ("rng", "隨機數字"),
            ("rcg", "隨機顏色"),
            ("gacha", "抽卡"),
        ),
    ),
    (
        "tools",
        "計算工具",
        "🧮",
        (
            ("calculate", "Rating 試算"),
            ("const", "定數試算"),
            ("rating", "目標 Rating"),
            ("border", "容錯計算"),
            ("anmitsu", "餡蜜間隔"),
        ),
    ),
)


def menu_categories(ctx):
    from .entrypoints import panel_command

    if getattr(ctx, "bot", None) is None:
        return []
    result = []
    for action, label, emoji, commands in CATEGORIES:
        features = [
            ui_text(ctx, caption)
            for name, caption in commands
            if panel_command(ctx.bot, name) is not None
        ]
        if features:
            result.append((action, ui_text(ctx, label), emoji, ", ".join(features)))
    return result


def main_menu_embed(ctx):
    categories = menu_categories(ctx)
    lines = [
        f"{emoji} **{label}** — {features}" for _, label, emoji, features in categories
    ]
    groups = ["\n".join(lines[index : index + 2]) for index in range(0, len(lines), 2)]
    embed = discord.Embed(
        title=ui_text(ctx, "Poop Penguin bot 主選單"),
        description=ui_text(
            ctx,
            "*點下方按鈕選功能。\n其他指令輸入 `/` 就看得到。*",
        )
        + "\n\n"
        + "\n\n".join(groups),
        color=0x57B9CF,
    )
    avatar = getattr(getattr(ctx.bot, "user", None), "display_avatar", None)
    if avatar is not None:
        embed.set_thumbnail(url=str(avatar.url))
    name = getattr(ctx.author, "display_name", None) or getattr(ctx.author, "name", "")
    embed.set_footer(
        text=ui_text(ctx, "{0} · 只有開啟者能操作 · 閒置 10 分鐘後逾時", name[:100])
    )
    return embed


async def _notice(ctx, interaction, text):
    sender = (
        interaction.followup.send
        if interaction.response.is_done()
        else interaction.response.send_message
    )
    await sender(ui_text(ctx, text), ephemeral=True)


async def navigation_check(ctx, interaction, view=None):
    """Also covers plain discord.ui.Views that do not inherit PenguinView."""
    if interaction.user.id != ctx.author.id:
        await _notice(ctx, interaction, "此面板只供開啟者操作。")
        return False
    if view is not None and (
        getattr(view, "_panel_inactive", False)
        or getattr(view, "_navigation_expired", False)
        or view.is_finished()
    ):
        await _notice(ctx, interaction, "此頁面已失效，請使用 /menu 重新開啟。")
        return False
    return True


class NavigationButton(discord.ui.Button):
    def __init__(self, ctx, target="menu", *, row=None):
        super().__init__(
            label=ui_text(ctx, "返回主選單" if target == "menu" else "說明"),
            emoji="⬅️" if target == "menu" else "❓",
            custom_id=MENU_ID if target == "menu" else HELP_ID,
            row=row,
        )
        self.ctx, self.target = ctx, target

    async def callback(self, interaction):
        await open_navigation(self.ctx, interaction, self.target, origin=self.view)


def add_back_button(ctx, view):
    """One-line opt-in for a View; ensure_close also calls this on rebuilds."""
    if (
        ctx is None
        or getattr(ctx, "bot", None) is None
        or isinstance(view, discord.ui.LayoutView)
        or getattr(view, "_menu_navigation_disabled", False)
        or getattr(view, "_is_main_menu", False)
        or getattr(view, "_navigation_expired", False)
        or getattr(view, "_panel_inactive", False)
        or any(getattr(item, "custom_id", None) == MENU_ID for item in view.children)
    ):
        return view
    used = [0] * 5
    for item in view.children:
        row = getattr(item, "_rendered_row", None)
        if row is None:
            row = item.row
        if row is not None:
            used[row] += item.width
    row = next((row for row in (4, 3, 2, 1, 0) if used[row] < 5), None)
    if row is None or len(view.children) >= 25:
        raise ValueError("Views must reserve one button slot for the main menu")
    view.add_item(NavigationButton(ctx, row=row))
    _guard_plain_view(ctx, view)
    return view


def _guard_plain_view(ctx, view):
    from .owner_view import bind_owner_view

    bind_owner_view(view, ctx.author.id, ctx=ctx)


async def expire_navigation(view):
    """Leave visibly disabled controls and a localized instruction to reopen."""
    from .owner_view import expire_owner_view, protected_dailyupdate

    if not protected_dailyupdate(view):
        return await expire_owner_view(view)
    # Preserve the existing dailyupdate path, including its cleanup semantics.
    if getattr(view, "_panel_inactive", False):
        return
    view._navigation_expired = True
    for item in view.walk_children():
        if (
            isinstance(item, discord.ui.Button)
            and item.style == discord.ButtonStyle.link
        ):
            continue
        if hasattr(item, "disabled"):
            item.disabled = True
    try:
        if getattr(view, "message", None) is not None:
            with suppress(discord.HTTPException):
                await view.message.edit(
                    content=ui_text(
                        view.ctx, "此頁面已逾時，請使用 /menu 或重新執行指令。"
                    ),
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
    finally:
        view.stop()


async def show_main_menu(ctx):
    from .panel import show_panel

    return await show_panel(ctx, "home")


async def open_navigation(ctx, interaction, target, *, origin=None):
    """Switch the clicked message; private overlays keep their own session."""
    from .panel import (
        PanelSession,
        component_context,
        defer_panel,
        panel_message,
        retire_previous,
        session_for,
    )

    if not await navigation_check(ctx, interaction, origin):
        return
    updated = component_context(ctx, interaction)
    clicked = getattr(interaction, "message", None)
    anchor = panel_message(ctx, interaction)
    if clicked is not None and anchor is not None and clicked.id != anchor.id:
        # A review/login overlay must not edit its parent public song card.
        updated.panel_session = PanelSession(updated)
        updated.response = updated.panel_session.message = clicked
        updated.panel_session.active_view = origin
    session = session_for(updated)
    if session.closed:
        await _notice(ctx, interaction, "此頁面已失效，請使用 /menu 重新開啟。")
        return
    pending = session.pending_login
    if session.busy and pending is None:
        await _notice(ctx, interaction, "正在處理上一個操作，請稍候。")
        return
    session.busy = True
    if session.active_view is None:
        session.active_view = origin
    # Acknowledge before cancelling a reader or constructing the command manual.
    try:
        await defer_panel(updated, interaction)
        if pending is not None and pending is not asyncio.current_task():
            session.pending_login = None
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await retire_previous(updated)
        if origin is not None and origin is not session.active_view:
            origin._panel_inactive = True
            origin.stop()
        if target == "help":
            from .help_manual import show_help_manual

            return await show_help_manual(updated)
        return await show_main_menu(updated)
    finally:
        session.busy = False
