"""Menu/help navigation uses the clicked message and retires old readers."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from chuni_penguin.config import Config, use_config
from chuni_penguin.context import PenguinContext
from chuni_penguin.ui._base import PenguinView
from chuni_penguin.ui.dismiss import ensure_close
from chuni_penguin.ui.help_manual import HelpManualView, show_help_manual
from chuni_penguin.ui.navigation import (
    HELP_ID,
    MENU_ID,
    add_back_button,
    main_menu_embed,
    menu_categories,
    show_main_menu,
)
from chuni_penguin.ui.panel import session_for, show_panel
from tests.test_panels import Message, assert_rows, interaction
from tests.test_panels import ctx as context_fixture

ctx = context_fixture


def control(view, custom_id):
    return next(item for item in view.children if item.custom_id == custom_id)


@pytest.mark.asyncio
async def test_menu_is_one_hybrid_command_and_matches_prefix_output(ctx):
    command = ctx.bot.get_command("menu")
    assert isinstance(command, commands.HybridCommand)
    assert ctx.bot.tree.get_command("menu") is command.app_command
    ctx.defer = AsyncMock()
    await command.callback(ctx)
    prefix = ctx.message.payloads[-1]
    ctx.interaction = None
    await command.callback(ctx)
    slash = ctx.message.payloads[-1]
    assert prefix["embed"].to_dict() == slash["embed"].to_dict()
    assert prefix["view"].to_components() == slash["view"].to_components()
    assert prefix["view"].is_finished()
    ctx.defer.assert_awaited()


@pytest.mark.asyncio
async def test_help_menu_help_menu_replaces_views_on_one_message(ctx):
    help_view = await show_help_manual(ctx)
    assert isinstance(help_view, HelpManualView)
    first = interaction(ctx)
    await control(help_view, MENU_ID).callback(first)
    main = session_for(ctx).active_view
    assert help_view.is_finished() and help_view._panel_inactive
    assert session_for(ctx).route.section == "home"
    assert "Poop Penguin" in ctx.message.payloads[-1]["embed"].title
    second = interaction(ctx)
    await control(main, HELP_ID).callback(second)
    manual = session_for(ctx).active_view
    assert main.is_finished()
    assert isinstance(manual, HelpManualView)
    third = interaction(ctx)
    await control(manual, MENU_ID).callback(third)
    assert manual.is_finished() and session_for(ctx).route.section == "home"
    assert session_for(ctx).message.id == 101
    assert ctx.new_private_messages == []
    for changed in (first, second, third):
        changed.response.defer.assert_awaited_once_with(thinking=False, ephemeral=True)
        changed.followup.send.assert_not_awaited()
        changed.response.send_message.assert_not_awaited()
    assert_rows(session_for(ctx).active_view)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "section", ["account", "account_settings", "chart", "tools", "fun", "border"]
)
async def test_each_panel_can_return_to_global_menu(ctx, section):
    view = await show_panel(ctx, section)
    await control(view, MENU_ID).callback(interaction(ctx))
    assert view.is_finished()
    assert session_for(ctx).route.section == "home"
    assert ctx.new_private_messages == []
    assert_rows(session_for(ctx).active_view)


@pytest.mark.asyncio
async def test_back_button_injection_is_idempotent_and_preserves_controls(ctx):
    view = PenguinView(ctx)
    for row in range(4):
        view.add_item(
            discord.ui.Select(options=[discord.SelectOption(label="Existing")], row=row)
        )
    for _ in range(3):
        add_back_button(ctx, view)
        ensure_close(view, ctx.author.id, ctx=ctx)
    assert len([item for item in view.children if item.custom_id == MENU_ID]) == 1
    assert len(view.children) == 6
    assert_rows(view)
    view.stop()


@pytest.mark.asyncio
async def test_expiration_disables_controls_and_stale_click_explains_how_to_reopen(ctx):
    view = await show_main_menu(ctx)
    await view.on_timeout()
    payload = ctx.message.payloads[-1]
    assert "/menu" in payload["content"]
    assert all(item.disabled for item in view.children)
    assert view.is_finished()
    changed = interaction(ctx)
    await control(view, HELP_ID).callback(changed)
    changed.response.send_message.assert_awaited_once()
    assert changed.response.send_message.call_args.kwargs["ephemeral"]
    changed.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_user_cannot_open_menu_or_help(ctx):
    main = await show_main_menu(ctx)
    before = len(ctx.message.payloads)
    outsider = interaction(ctx, user_id=22)
    await control(main, HELP_ID).callback(outsider)
    outsider.response.send_message.assert_awaited_once()
    assert outsider.response.send_message.call_args.kwargs["ephemeral"]
    assert len(ctx.message.payloads) == before
    assert not main.is_finished()


@pytest.mark.asyncio
async def test_stopped_old_view_cannot_replace_new_menu(ctx):
    old = await show_panel(ctx, "tools")
    button = control(old, MENU_ID)
    await button.callback(interaction(ctx))
    current = session_for(ctx).active_view
    before = len(ctx.message.payloads)
    stale = interaction(ctx)
    await button.callback(stale)
    stale.response.send_message.assert_awaited_once()
    assert session_for(ctx).active_view is current
    assert len(ctx.message.payloads) == before


@pytest.mark.asyncio
async def test_private_overlay_return_edits_overlay_and_keeps_public_parent(ctx):
    public = await show_main_menu(ctx)
    ctx.message.flags.ephemeral = False
    overlay = PenguinView(ctx)
    add_back_button(ctx, overlay)
    private_message = Message()
    private_message.id = 202
    changed = interaction(ctx)
    changed.message = private_message
    changed.original_response.return_value = private_message
    changed.edit_original_response.side_effect = private_message.edit
    original_respond = ctx.respond_or_edit

    async def reply(content=None, **kwargs):
        # Mimic the real context's webhook edit for the new private session.
        await private_message.edit(content=content, **kwargs)
        return private_message

    # Only the copied context will use this responder.
    ctx.respond_or_edit = reply
    try:
        await control(overlay, MENU_ID).callback(changed)
    finally:
        ctx.respond_or_edit = original_respond
    assert not public.is_finished()
    assert session_for(ctx).active_view is public
    assert session_for(ctx).message.id == 101
    assert private_message.payloads[-1]["view"].message.id == 202
    assert overlay.is_finished()
    private_message.payloads[-1]["view"].stop()


@pytest.mark.asyncio
async def test_return_acknowledges_before_cancelling_login_and_stops_reader(ctx):
    view = await show_panel(ctx, "tools")
    changed = interaction(ctx)
    cleaned = asyncio.Event()

    async def wait_login():
        try:
            await asyncio.Event().wait()
        finally:
            assert changed.response.is_done()
            cleaned.set()

    pending = asyncio.create_task(wait_login())
    await asyncio.sleep(0)
    session_for(ctx).pending_login = pending
    await control(view, MENU_ID).callback(changed)
    assert cleaned.is_set() and pending.cancelled()
    assert view.is_finished() and not session_for(ctx).busy


@pytest.mark.asyncio
@pytest.mark.parametrize("already_done", [False, True])
async def test_real_context_navigation_edits_interaction_response_once(
    ctx, already_done
):
    changed = interaction(ctx)
    changed.response.is_done = lambda: already_done
    original_send = ctx.send = AsyncMock()
    ctx._panel_edit = True
    ctx.interaction = changed
    view = PenguinView(ctx)
    await PenguinContext.respond_or_edit(
        ctx, embed=discord.Embed(title="Menu"), view=view
    )
    if already_done:
        changed.edit_original_response.assert_awaited_once()
        changed.response.edit_message.assert_not_awaited()
    else:
        changed.response.edit_message.assert_awaited_once()
        changed.edit_original_response.assert_not_awaited()
    original_send.assert_not_awaited()
    changed.followup.send.assert_not_awaited()
    view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "chinese"])
async def test_menu_text_and_controls_use_one_language(ctx, language):
    with use_config(Config.from_mapping({"fun": {"language": language}})):
        view = await show_main_menu(ctx)
        embed = ctx.message.payloads[-1]["embed"]
        text = str(embed.to_dict()) + str(view.to_components())
        if language == "english":
            assert not re.search(r"[\u3400-\u9fff]", text)
            assert "Help" in text and "Song and chart search" in text
        else:
            assert "主選單" in text and "說明" in text
        assert "Fixture" in embed.footer.text
        assert len(embed.description) < 4096
        assert_rows(view)


@pytest.mark.asyncio
async def test_menu_uses_bot_avatar_and_lists_only_loaded_commands(ctx, monkeypatch):
    avatar = SimpleNamespace(url="https://cdn.discordapp.com/embed/avatars/0.png")
    monkeypatch.setattr(
        ctx.bot._connection, "user", SimpleNamespace(display_avatar=avatar)
    )
    assert main_menu_embed(ctx).thumbnail.url == avatar.url
    import chuni_penguin.ui.entrypoints as entrypoints

    monkeypatch.setattr(
        entrypoints,
        "panel_command",
        lambda bot, name: object() if name == "border" else None,
    )
    categories = menu_categories(ctx)
    assert len(categories) == 1
    assert categories[0][0] == "tools"
    assert "容錯計算" in categories[0][3]


@pytest.mark.asyncio
async def test_plain_view_helper_enforces_owner_and_runs_original_timeout(ctx):
    view = discord.ui.View()
    view.add_item(discord.ui.UserSelect())
    cleanup = AsyncMock()
    view.on_timeout = cleanup
    view.message = ctx.message
    add_back_button(ctx, view)
    outsider = interaction(ctx, user_id=22)
    assert not await view.interaction_check(outsider)
    outsider.response.send_message.assert_awaited_once()
    await view.on_timeout()
    cleanup.assert_awaited_once()
    assert view.is_finished() and all(item.disabled for item in view.children)
    assert "/menu" in ctx.message.payloads[-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_return_releases_an_untracked_reader_even_when_cleanup_fails(
    ctx, cleanup_fails
):
    view = PenguinView(ctx)
    add_back_button(ctx, view)
    view.close_network = AsyncMock(
        side_effect=RuntimeError("synthetic cleanup error") if cleanup_fails else None
    )
    view.deactivate = AsyncMock()
    session_for(ctx).active_view = None
    await control(view, MENU_ID).callback(interaction(ctx))
    view.close_network.assert_awaited_once()
    view.deactivate.assert_awaited_once()
    assert view.is_finished() and session_for(ctx).route.section == "home"
    assert session_for(ctx).active_view is not view
