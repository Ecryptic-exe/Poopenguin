# ruff: noqa: RUF001
"""Owner-only message dismissal, shared by public and private Embed views."""

import asyncio
from contextlib import suppress

import discord

from chuni_penguin.logging import logger
from chuni_penguin.utils.chunithm_i18n import ui_text


def response_closed(ctx):
    return bool(
        getattr(ctx, "_embed_closed", False)
        or getattr(getattr(ctx, "panel_session", None), "closed", False)
    )


class CloseButton(discord.ui.Button):
    def __init__(self, owner_id, *, ctx=None, row=None):
        super().__init__(
            label=ui_text(ctx, "關閉"), custom_id="penguin:dismiss", row=row
        )
        self.owner_id, self.ctx = owner_id, ctx

    async def callback(self, interaction):
        await dismiss_message(interaction, self.view, self.owner_id, ctx=self.ctx)


async def dismiss_message(interaction, view, owner_id, *, ctx=None):
    from .owner_view import private_notice

    if ctx is None:
        ctx = getattr(view, "ctx", None)
    if interaction.user.id != owner_id:
        await private_notice(interaction, ctx, "只有開啟者能關閉此訊息。")
        return
    if view is not None:
        view._embed_closed = True
        view._panel_inactive = True
    if not interaction.response.is_done():
        with suppress(discord.HTTPException):
            await interaction.response.defer()
    session = getattr(ctx, "panel_session", None)
    anchor = getattr(session, "message", None)
    current = getattr(interaction, "message", None)
    same_session = session is not None and (
        anchor is None or current is not None and anchor.id == current.id
    )
    targets = [view] if view is not None else []
    if same_session:
        session.closed = True
        pending = session.pending_login
        if pending is not None and pending is not asyncio.current_task():
            session.pending_login = None
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        session.busy = False
        active = session.active_view
        if active is not None and active is not view:
            targets.append(active)
    elif ctx is not None and session is None and hasattr(ctx, "__dict__"):
        # Error views may carry an Interaction for i18n; its slots do not
        # support the Context-only cancellation flag.
        ctx._embed_closed = True
    for target in targets:
        target._panel_inactive = True
        target._embed_closed = True
        try:
            if hasattr(target, "deactivate"):
                await target.deactivate()
            if hasattr(target, "close_network"):
                await target.close_network()
        except Exception as error:
            await logger.awarning(
                "Dismiss cleanup failed", error_type=type(error).__name__
            )
        finally:
            target.stop()
    # Component defer binds original_response to this exact message, including
    # ephemeral messages. Never use the channel API for a private message.
    try:
        await interaction.delete_original_response()
    except discord.NotFound:
        pass
    except discord.HTTPException:
        with suppress(discord.HTTPException):
            if isinstance(view, discord.ui.LayoutView):
                replacement = discord.ui.LayoutView(timeout=None)
                replacement.add_item(discord.ui.TextDisplay(ui_text(ctx, "已關閉。")))
                await interaction.edit_original_response(view=replacement)
            else:
                await interaction.edit_original_response(
                    content=ui_text(ctx, "已關閉。"),
                    embeds=[],
                    attachments=[],
                    view=None,
                )


class CloseView(discord.ui.View):
    _panel_own_navigation = True
    _menu_navigation_disabled = True

    def __init__(self, owner_id, *, ctx=None):
        super().__init__(timeout=600)
        self.add_item(CloseButton(owner_id, ctx=ctx))
        from .owner_view import bind_owner_view

        bind_owner_view(self, owner_id, ctx=ctx)


def ensure_close(view, owner_id, *, ctx=None):
    """Add exactly one close action after a view finishes rebuilding controls."""
    if getattr(view, "_embed_closed", False):
        return view
    from .owner_view import bind_owner_view

    bind_owner_view(view, owner_id, ctx=ctx)
    from .navigation import add_back_button

    add_back_button(ctx, view)
    if any(
        isinstance(item, discord.ui.Button)
        and (isinstance(item, CloseButton) or item.label in ("關閉", "Close"))
        for item in view.walk_children()
    ):
        return view
    button = CloseButton(owner_id, ctx=ctx)
    if isinstance(view, discord.ui.LayoutView):
        view.add_item(discord.ui.ActionRow(button))
        return view
    used = [0] * 5
    for item in view.children:
        row = getattr(item, "_rendered_row", None)
        if row is None:
            row = item.row
        if row is not None:
            used[row] += item.width
    # Prefer a final button row, while preserving full-width select menus.
    row = next((row for row in (4, 3, 2, 1, 0) if used[row] < 5), None)
    if row is None:
        raise ValueError("Embed controls must reserve one slot for the close button")
    button.row = row
    view.add_item(button)
    bind_owner_view(view, owner_id, ctx=ctx)
    return view


def close_response(ctx, kwargs):
    """Cover all Context Embed responses, including non-panel prefix commands."""
    view = kwargs.get("view")
    if kwargs.get("embed") is None and not kwargs.get("embeds") and view is None:
        return kwargs
    if view is None:
        view = CloseView(ctx.author.id, ctx=ctx)
    else:
        ensure_close(view, ctx.author.id, ctx=ctx)
    kwargs["view"] = view
    return kwargs
