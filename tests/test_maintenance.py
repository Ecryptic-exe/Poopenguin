"""Regressions for owner cleanup, bare mentions and protected dailyupdate UI."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from chuni_penguin.cogs.poopenguin import copypasta_cog, keywords_cog
from chuni_penguin.cogs.poopenguin.messages_cog import MessagesCog
from chuni_penguin.config import Config, use_config
from chuni_penguin.ui import copypasta
from chuni_penguin.ui.chart_reviews import ReviewDeleteView, ReviewPrivateView
from chuni_penguin.ui.dismiss import CloseView, dismiss_message, ensure_close
from chuni_penguin.ui.navigation import HELP_ID, MENU_ID, main_menu_embed
from chuni_penguin.ui.owner_view import bind_owner_view, send_view_message
from chuni_penguin.ui.song_search import AliasOfferView
from tests.test_panels import ctx as context_fixture
from tests.test_panels import interaction as panel_interaction

ctx = context_fixture


def changed(user=11, *, private=False):
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        message=SimpleNamespace(
            id=101, flags=SimpleNamespace(ephemeral=private), edit=AsyncMock()
        ),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
            defer=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        original_response=AsyncMock(),
        edit_original_response=AsyncMock(),
        delete_original_response=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory",
    [
        lambda c: copypasta.CopypastaMenu(
            c, {}, "english", service=SimpleNamespace(track_view=Mock())
        ),
        lambda c: copypasta_cog.CopypastaMenu(c, {}, "english"),
        lambda c: keywords_cog.KeywordMenu(c, {}, "english"),
        lambda c: AliasOfferView(
            c, "fixture", object(), SimpleNamespace(status="available")
        ),
    ],
)
async def test_direct_native_views_disable_close_without_context_send(factory):
    context = SimpleNamespace(author=SimpleNamespace(id=11))
    view = factory(context)
    view.message = SimpleNamespace(
        flags=SimpleNamespace(ephemeral=False), edit=AsyncMock()
    )
    await view.on_timeout()
    assert view.is_finished()
    assert all(
        item.disabled for item in view.walk_children() if hasattr(item, "disabled")
    )
    assert view.message.edit.call_args.kwargs["view"] is view
    assert "/menu" in view.message.edit.call_args.kwargs["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [copypasta, copypasta_cog, keywords_cog])
async def test_native_close_deletes_only_for_owner(module):
    context = SimpleNamespace(author=SimpleNamespace(id=11))
    if module is keywords_cog:
        view = module.KeywordMenu(context, {}, "english")
    elif module is copypasta:
        view = module.CopypastaMenu(
            context, {}, "english", service=SimpleNamespace(track_view=Mock())
        )
    else:
        view = module.CopypastaMenu(context, {}, "english")
    outsider = changed(22)
    await view.close_button.callback(outsider)
    outsider.response.send_message.assert_awaited_once()
    outsider.delete_original_response.assert_not_awaited()
    owner = changed()
    await view.close_button.callback(owner)
    owner.delete_original_response.assert_awaited_once()
    assert view.is_finished()


@pytest.mark.asyncio
async def test_timeout_refreshes_private_response_without_channel_edit():
    view = CloseView(11)
    latest = changed(private=True)
    latest.response.is_done.return_value = True
    forbidden = latest.message.edit
    forbidden.side_effect = AssertionError("ephemeral message cannot use channel API")
    assert await view.interaction_check(latest)
    await view.on_timeout()
    latest.edit_original_response.assert_awaited_once()
    forbidden.assert_not_awaited()
    assert view.is_finished() and all(item.disabled for item in view.children)


@pytest.mark.asyncio
async def test_followup_timeout_edits_actual_followup_message_not_parent():
    view = CloseView(11)
    original = changed(private=True)
    original.response.is_done.return_value = True
    message = Mock(spec=discord.WebhookMessage)
    message.flags = SimpleNamespace(ephemeral=True)
    message.edit = AsyncMock(return_value=message)
    original.followup.send.return_value = message
    await send_view_message(original, view=view, content="private", ephemeral=True)
    original.followup.send.assert_awaited_once_with(
        view=view, content="private", ephemeral=True, wait=True
    )
    await view.on_timeout()
    message.edit.assert_awaited_once()
    original.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [discord.NotFound, discord.Forbidden])
async def test_timeout_and_delete_http_failures_are_graceful(failure):
    error = failure(
        SimpleNamespace(
            status=404 if failure is discord.NotFound else 403, reason="synthetic"
        ),
        "synthetic failure",
    )
    view = CloseView(11)
    view.message = SimpleNamespace(
        flags=SimpleNamespace(ephemeral=False), edit=AsyncMock(side_effect=error)
    )
    await view.on_timeout()
    assert view.is_finished()
    interaction = changed()
    interaction.response.defer.side_effect = error
    interaction.delete_original_response.side_effect = error
    interaction.edit_original_response.side_effect = error
    await dismiss_message(interaction, CloseView(11), 11)


@pytest.mark.asyncio
async def test_close_denial_after_acknowledgement_is_ephemeral():
    interaction = changed(22)
    interaction.response.is_done.return_value = True
    view = CloseView(11)
    await dismiss_message(interaction, view, 11)
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.kwargs["ephemeral"]
    assert not view.is_finished()
    view.stop()


@pytest.mark.asyncio
async def test_cleanup_hook_runs_and_new_controls_are_also_disabled():
    view = discord.ui.View(timeout=120)
    cleanup = AsyncMock()
    view.on_timeout = cleanup
    ensure_close(view, 11)
    view.clear_items()
    view.add_item(discord.ui.UserSelect())
    ensure_close(view, 11)
    view.message = SimpleNamespace(
        flags=SimpleNamespace(ephemeral=False), edit=AsyncMock()
    )
    await view.on_timeout()
    cleanup.assert_awaited_once()
    assert len(view.children) == 2 and all(item.disabled for item in view.children)
    assert view.timeout == 120


@pytest.mark.asyncio
async def test_dailyupdate_does_not_opt_into_new_lifecycle():
    from chuni_penguin.ui.dailyupdate import DailyUpdateView

    view = DailyUpdateView.__new__(DailyUpdateView)
    discord.ui.View.__init__(view, timeout=600)
    original = view.interaction_check
    bind_owner_view(view, 11)
    assert not getattr(view, "_owner_bound", False)
    assert view.interaction_check == original and view.timeout == 600
    view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "chinese"])
async def test_only_requested_menu_sentence_is_removed(ctx, language):
    with use_config(Config.from_mapping({"fun": {"language": language}})):
        text = main_menu_embed(ctx).description
        assert "每個子面板" not in text and "every subpanel" not in text
        assert "`/`" in text
        assert (
            "Choose a feature below." in text
            if language == "english"
            else "點下方按鈕選功能。" in text
        )


def mention_bot(ctx, text, *, image=False, ai=None):
    bot = ctx.bot
    cog = MessagesCog.__new__(MessagesCog)
    cog.bot = SimpleNamespace(
        user=SimpleNamespace(id=42, mentioned_in=lambda message: True),
        get_context=AsyncMock(return_value=ctx),
        commands=bot.commands,
        help_command=bot.help_command,
        get_cog=lambda name: ai if name == "AICog" else bot.get_cog(name),
        get_command=bot.get_command,
    )
    message = SimpleNamespace(
        content=text,
        mention_everyone=False,
        attachments=[SimpleNamespace(content_type="image/png")] if image else [],
        guild=ctx.guild,
        channel=SimpleNamespace(send=AsyncMock()),
        author=ctx.author,
    )
    return cog, message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["<@42>", "<@!42>", "<@42> menu", "<@42> poop penguin"]
)
async def test_bare_mention_opens_shared_menu_and_can_visit_help(ctx, text):
    cog, message = mention_bot(ctx, text)
    assert await cog._handle_mention(message)
    view = ctx.panel_session.active_view
    assert "Poop Penguin" in ctx.message.payloads[-1]["embed"].title
    assert any(item.custom_id == HELP_ID for item in view.children)
    help_button = next(item for item in view.children if item.custom_id == HELP_ID)
    await help_button.callback(panel_interaction(ctx))
    manual = ctx.panel_session.active_view
    await next(item for item in manual.children if item.custom_id == MENU_ID).callback(
        panel_interaction(ctx)
    )
    assert ctx.panel_session.route.section == "home" and ctx.new_private_messages == []
    message.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_help_and_ai_mentions_keep_their_meaning(ctx):
    cog, message = mention_bot(ctx, "<@42> help")
    cog.bot.help_command = Mock()
    manual = cog.bot.help_command.copy.return_value
    manual.send_bot_help = AsyncMock()
    assert await cog._handle_mention(message)
    manual.send_bot_help.assert_awaited_once()
    ai = SimpleNamespace(respond_to_mention=AsyncMock())
    for text, image in [("<@42> Hello Penguin", False), ("<@42>", True)]:
        cog, message = mention_bot(ctx, text, image=image, ai=ai)
        await cog._handle_mention(message)
    assert ai.respond_to_mention.await_count == 2
    assert ai.respond_to_mention.call_args_list[0].args[1] == "Hello Penguin"


@pytest.mark.asyncio
async def test_unknown_mention_without_ai_points_to_menu(ctx):
    cog, message = mention_bot(ctx, "<@42> unknown-test-command")
    await cog._handle_mention(message)
    assert "/menu" in message.channel.send.call_args.args[0]
    assert "help" not in message.channel.send.call_args.args[0]


@pytest.mark.asyncio
async def test_review_confirmation_stops_old_timer_and_cancel_returns_live_view():
    chart = SimpleNamespace(
        id=5, difficulty="MAS", level="14", song=SimpleNamespace(title="Fixture")
    )
    panel = ReviewPrivateView(SimpleNamespace(), 11, chart)
    interaction = changed(private=True)
    await panel.remove.callback(interaction)
    confirm = interaction.response.edit_message.call_args.kwargs["view"]
    assert isinstance(confirm, ReviewDeleteView) and panel.is_finished()
    assert await confirm.interaction_check(changed(private=True))
    cancel = changed(private=True)
    await confirm.cancel.callback(cancel)
    fresh = cancel.response.edit_message.call_args.kwargs["view"]
    assert not fresh.is_finished() and fresh.owner_id == panel.owner_id
    assert fresh.chart is panel.chart and confirm.is_finished()
    fresh.stop()
