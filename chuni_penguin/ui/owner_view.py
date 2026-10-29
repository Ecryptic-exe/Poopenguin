"""Shared owner checks, close actions and visible timeout cleanup for messages."""

from contextlib import suppress

import discord

from chuni_penguin.utils.chunithm_i18n import ui_text


def protected_dailyupdate(view):
    # Keep the dailyupdate lifecycle unchanged during this maintenance pass.
    return type(view).__module__ == "chuni_penguin.ui.dailyupdate"


async def private_notice(interaction, ctx, text):
    sender = (
        interaction.followup.send
        if interaction.response.is_done()
        else interaction.response.send_message
    )
    with suppress(discord.HTTPException):
        await sender(ui_text(ctx, text), ephemeral=True)


def remember_interaction(view, interaction):
    if not protected_dailyupdate(view):
        view._owner_interaction = interaction
        if getattr(view, "message", None) is None:
            view.message = getattr(interaction, "message", None)


def bind_owner_view(view, owner_id, *, ctx=None):
    """Preserve domain checks/cleanup and cover directly sent native Views."""
    if protected_dailyupdate(view):
        return view
    if ctx is None:
        ctx = getattr(view, "ctx", None)
    view._owner_id, view._owner_ctx = owner_id, ctx
    if getattr(view, "_owner_bound", False):
        return view
    view._owner_bound = True
    original_check, original_timeout = view.interaction_check, view.on_timeout

    async def check(interaction):
        if interaction.user.id != view._owner_id:
            await private_notice(interaction, ctx, "此面板只供開啟者操作。")
            return False
        if getattr(view, "_panel_inactive", False) or getattr(
            view, "_owner_expired", False
        ):
            await private_notice(
                interaction, ctx, "此頁面已失效，請使用 /menu 重新開啟。"
            )
            return False
        remember_interaction(view, interaction)
        return await original_check(interaction)

    async def timeout():
        try:
            await original_timeout()
        except discord.HTTPException:
            # A deleted message or expired private response needs no new reply.
            pass
        finally:
            if not getattr(view, "_owner_expired", False) and not getattr(
                view, "_preserve_timeout_view", False
            ):
                await expire_owner_view(view)

    view.interaction_check = check
    view.on_timeout = timeout
    return view


async def send_view_message(interaction, *, view, **kwargs):
    """Keep the actual sent message, including private followup message IDs."""
    if interaction.response.is_done():
        kwargs.setdefault("wait", True)
        message = await interaction.followup.send(view=view, **kwargs)
    else:
        result = await interaction.response.send_message(view=view, **kwargs)
        message = getattr(result, "resource", None)
        if not isinstance(message, discord.Message):
            with suppress(discord.HTTPException):
                message = await interaction.original_response()
    view.message = message
    return message


async def edit_view_message(interaction, *, view, **kwargs):
    message = await interaction.edit_original_response(view=view, **kwargs)
    view.message = message
    remember_interaction(view, interaction)
    return message


async def expire_owner_view(view):
    if getattr(view, "_panel_inactive", False):
        return
    view._owner_expired = view._navigation_expired = True
    for item in view.walk_children():
        if (
            isinstance(item, discord.ui.Button)
            and item.style == discord.ButtonStyle.link
        ):
            continue
        if hasattr(item, "disabled"):
            item.disabled = True
    ctx = getattr(view, "_owner_ctx", None) or getattr(view, "ctx", None)
    kwargs = dict(
        content=ui_text(ctx, "此頁面已逾時，請使用 /menu 或重新執行指令。"),
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    message = getattr(view, "message", None)
    latest = getattr(view, "_owner_interaction", None)
    private = bool(getattr(getattr(message, "flags", None), "ephemeral", False))
    if latest is not None:
        private = private or bool(
            getattr(
                getattr(getattr(latest, "message", None), "flags", None),
                "ephemeral",
                False,
            )
        )
    try:
        with suppress(discord.HTTPException):
            if private and latest is not None:
                view.message = await latest.edit_original_response(**kwargs)
            elif private:
                # A regular channel Message cannot edit ephemeral content.
                if isinstance(
                    message, (discord.InteractionMessage, discord.WebhookMessage)
                ):
                    view.message = await message.edit(**kwargs)
            elif message is not None:
                await message.edit(**kwargs)
    finally:
        view.stop()
