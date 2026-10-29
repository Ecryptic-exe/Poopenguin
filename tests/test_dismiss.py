import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from chuni_penguin.context import PenguinContext
from chuni_penguin.ui._base import PenguinLayoutView, PenguinView
from chuni_penguin.ui.dismiss import (
    CloseButton,
    CloseView,
    close_response,
    ensure_close,
)
from chuni_penguin.ui.panel import PanelSession, refresh_account_parent
from chuni_penguin.ui.song_search import AliasOfferView


def changed(user=11):
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        message=SimpleNamespace(id=101),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            defer=AsyncMock(),
            send_message=AsyncMock(),
        ),
        delete_original_response=AsyncMock(),
        edit_original_response=AsyncMock(),
    )


def context():
    return SimpleNamespace(author=SimpleNamespace(id=11))


@pytest.mark.asyncio
async def test_only_owner_can_dismiss_and_repeated_injection_adds_one_button():
    ctx = context()
    view = PenguinView(ctx)
    for _ in range(3):
        ensure_close(view, 11, ctx=ctx)
    assert len(view.children) == 1
    denied = changed(22)
    await view.children[0].callback(denied)
    denied.delete_original_response.assert_not_awaited()
    denied.response.send_message.assert_awaited_once()
    assert not view.is_finished()
    accepted = changed()
    await view.children[0].callback(accepted)
    accepted.delete_original_response.assert_awaited_once()
    assert view.is_finished() and ctx._embed_closed


@pytest.mark.asyncio
async def test_close_cancels_login_and_releases_active_reader_even_on_processing_view():
    ctx = context()
    session = ctx.panel_session = PanelSession(ctx)
    session.message = SimpleNamespace(id=101)
    active = PenguinView(ctx)
    active.deactivate = AsyncMock()
    active.close_network = AsyncMock()
    session.active_view = active
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def pending_login():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    session.pending_login = asyncio.create_task(pending_login())
    await started.wait()
    view = CloseView(11, ctx=ctx)
    await view.children[0].callback(changed())
    assert cleaned.is_set()
    assert session.closed and not session.busy and session.pending_login is None
    active.deactivate.assert_awaited_once()
    active.close_network.assert_awaited_once()
    assert active.is_finished()
    for responder in (
        PenguinContext.respond_or_edit,
        PenguinContext.reply,
        PenguinContext.send,
    ):
        with pytest.raises(asyncio.CancelledError):
            await responder(ctx, embed=discord.Embed(title="Late result"))


@pytest.mark.asyncio
async def test_closing_private_alias_does_not_close_public_song_session():
    ctx = context()
    ctx.panel_session = PanelSession(ctx)
    ctx.panel_session.message = SimpleNamespace(id=101)
    public = PenguinView(ctx)
    ctx.panel_session.active_view = public
    offer = AliasOfferView(
        ctx, "fixture", object(), SimpleNamespace(status="available")
    )
    interaction = changed()
    interaction.message.id = 202
    await offer.dismiss.callback(interaction)
    assert offer.is_finished()
    assert not ctx.panel_session.closed and not public.is_finished()
    interaction.delete_original_response.assert_awaited_once()
    public.stop()


@pytest.mark.asyncio
async def test_private_account_completion_skips_an_already_closed_public_parent():
    parent = context()
    parent.panel_session = PanelSession(parent)
    parent.panel_session.closed = True
    private = SimpleNamespace(_account_parent=parent)
    await refresh_account_parent(private)


@pytest.mark.asyncio
async def test_cancelled_initial_delivery_releases_a_new_reader():
    ctx = context()
    ctx.respond_or_edit = AsyncMock(side_effect=asyncio.CancelledError)
    view = PenguinView(ctx)
    view.deactivate = AsyncMock()
    view.close_network = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await view.start()
    view.deactivate.assert_awaited_once()
    view.close_network.assert_awaited_once()
    assert view.is_finished()


@pytest.mark.asyncio
async def test_failed_delete_clears_embed_attachments_and_controls_without_data_actions():
    interaction = changed()
    interaction.delete_original_response.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "cannot delete"
    )
    view = CloseView(11)
    await view.children[0].callback(interaction)
    interaction.edit_original_response.assert_awaited_once_with(
        content="Closed.", embeds=[], attachments=[], view=None
    )


@pytest.mark.asyncio
async def test_timeout_disables_embed_controls_and_retains_layout_content():
    for view in (PenguinView(context()), PenguinLayoutView(context())):
        if isinstance(view, discord.ui.LayoutView):
            view.add_item(discord.ui.TextDisplay("Retained content"))
        else:
            view.add_item(discord.ui.Button(label="Old action"))
        ensure_close(view, 11)
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        replacement = view.message.edit.call_args.kwargs["view"]
        if not isinstance(view, discord.ui.LayoutView):
            assert replacement is view and view.is_finished()
            assert all(item.disabled for item in view.children)
            assert "/menu" in view.message.edit.call_args.kwargs["content"]
            continue
        assert replacement.timeout is None
        close = next(
            item
            for item in replacement.walk_children()
            if isinstance(item, CloseButton)
        )
        assert not close.disabled
        await close.callback(changed())
        assert replacement.is_finished()
        if isinstance(view, discord.ui.LayoutView):
            assert "Retained content" in str(replacement.to_components())


def test_prefix_and_plain_embed_responses_receive_close_without_duplicate_native_controls():
    kwargs = close_response(context(), {"embed": discord.Embed(title="Fixture")})
    assert isinstance(kwargs["view"], CloseView)
    native = discord.ui.View()
    native.add_item(discord.ui.Button(label="關閉"))
    assert (
        close_response(context(), {"embed": discord.Embed(), "view": native})["view"]
        is native
    )
    assert len(native.children) == 1
    native.stop()
    kwargs["view"].stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_real_discord_dismiss_uses_message_webhook_for_both_visibilities(
    monkeypatch, private
):
    from discord.webhook.async_ import async_context

    client = discord.Client(intents=discord.Intents.none())
    author = {
        "id": "42",
        "username": "Bot",
        "discriminator": "0001",
        "avatar": None,
        "bot": True,
    }
    raw = {
        "id": "101",
        "channel_id": "22",
        "author": author,
        "content": "",
        "timestamp": datetime.now(UTC).isoformat(),
        "edited_timestamp": None,
        "attachments": [],
        "embeds": [],
        "components": [],
        "pinned": False,
        "type": 0,
        "flags": 64 if private else 0,
    }
    payload = {
        "id": "999",
        "application_id": "42",
        "attachment_size_limit": 10485760,
        "type": 3,
        "token": "synthetic-interaction-token",
        "version": 1,
        "locale": "zh-TW",
        "channel_id": "22",
        "channel": {"id": "22", "type": 1},
        "user": {
            "id": "11",
            "username": "Fixture",
            "discriminator": "0001",
            "avatar": None,
        },
        "message": raw,
        "data": {"custom_id": "penguin:dismiss", "component_type": 2},
    }
    interaction = discord.Interaction(data=payload, state=client._connection)
    forbidden = AsyncMock(
        side_effect=AssertionError("private messages cannot use channel deletion")
    )
    monkeypatch.setattr(client.http, "delete_message", forbidden)
    adapter = SimpleNamespace(
        create_interaction_response=AsyncMock(
            return_value={"interaction": {"id": "999"}}
        ),
        delete_original_interaction_response=AsyncMock(),
    )
    view = CloseView(11, ctx=interaction)
    token = async_context.set(adapter)
    try:
        await view.children[0].callback(interaction)
    finally:
        async_context.reset(token)
    assert (
        adapter.create_interaction_response.call_args.kwargs["params"].payload["type"]
        == 6
    )
    adapter.delete_original_interaction_response.assert_awaited_once()
    forbidden.assert_not_awaited()
