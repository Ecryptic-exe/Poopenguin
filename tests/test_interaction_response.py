import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord.ext import commands

from chuni_penguin.bot import ChuniBot
from chuni_penguin.command_tree import PenguinCommandTree
from chuni_penguin.config import Config
from chuni_penguin.context import PenguinContext


def context_for(command_name, *, done=False):
    response = SimpleNamespace(is_done=Mock(return_value=done), type=None)

    async def defer(**kwargs):
        response.is_done.return_value = True
        response.type = discord.InteractionResponseType.deferred_channel_message

    response.defer = AsyncMock(side_effect=defer)
    interaction = SimpleNamespace(
        response=response,
        user=SimpleNamespace(id=11),
        client=SimpleNamespace(fetch_user_config=AsyncMock(return_value="settings")),
        original_response=AsyncMock(),
    )
    ctx = object.__new__(PenguinContext)
    ctx.command = SimpleNamespace(qualified_name=command_name)
    ctx.interaction = interaction
    ctx.response = None
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,private",
    [
        ("vto", False),
        ("setvote", False),
        ("chat", False),
        ("sync", True),
        ("login", True),
        ("account link", True),
        ("feed list", True),
        ("kamaitachi link", True),
        ("chartview", True),
    ],
)
async def test_early_defer_preserves_visibility_and_existing_defer_calls(name, private):
    ctx = context_for(name)
    await ctx.defer()
    await ctx.defer()
    ctx.interaction.response.defer.assert_awaited_once_with(
        ephemeral=private, thinking=True
    )


@pytest.mark.asyncio
async def test_context_acknowledges_before_slow_settings_and_skips_loading_message_get(
    monkeypatch,
):
    ctx = context_for("vto")
    started = asyncio.Event()
    release = asyncio.Event()

    async def settings(user_id):
        started.set()
        await release.wait()
        return "loaded"

    ctx.interaction.client.fetch_user_config.side_effect = settings
    monkeypatch.setattr(
        commands.Context, "from_interaction", AsyncMock(return_value=ctx)
    )
    task = asyncio.create_task(PenguinContext.from_interaction(ctx.interaction))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert ctx.interaction.response.is_done()
        assert not task.done()
        ctx.interaction.original_response.assert_not_awaited()
    finally:
        release.set()
        result = await task
    assert result is ctx
    assert result.user_config == "loaded"
    assert result.response is None


@pytest.mark.asyncio
async def test_already_deferred_context_does_not_acknowledge_again(monkeypatch):
    ctx = context_for("chart", done=True)
    ctx.interaction.response.type = (
        discord.InteractionResponseType.deferred_channel_message
    )
    monkeypatch.setattr(
        commands.Context, "from_interaction", AsyncMock(return_value=ctx)
    )
    assert await PenguinContext.from_interaction(ctx.interaction) is ctx
    ctx.interaction.response.defer.assert_not_awaited()
    ctx.interaction.original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_sent_message_keeps_response_anchor(monkeypatch):
    ctx = context_for("chart", done=True)
    ctx.interaction.response.type = discord.InteractionResponseType.channel_message
    monkeypatch.setattr(
        commands.Context, "from_interaction", AsyncMock(return_value=ctx)
    )
    await PenguinContext.from_interaction(ctx.interaction)
    assert ctx.response is ctx.interaction.original_response.return_value


@pytest.mark.asyncio
async def test_prefix_defer_does_nothing():
    ctx = object.__new__(PenguinContext)
    ctx.interaction = None
    await ctx.defer()


@pytest.mark.asyncio
async def test_bot_does_not_read_interaction_settings_twice(monkeypatch, tmp_path):
    settings = Config.from_mapping(
        {"bot": {"db_connection_string": "sqlite+aiosqlite:///:memory:"}}
    )
    async with ChuniBot(
        settings, offline=True, extensions=(), cache_dir=tmp_path / "cache"
    ) as bot:
        ctx = SimpleNamespace(command=SimpleNamespace(), interaction=object())
        monkeypatch.setattr(
            commands.AutoShardedBot, "get_context", AsyncMock(return_value=ctx)
        )
        monkeypatch.setattr(bot, "fetch_user_config", AsyncMock())
        result = await bot.get_context(SimpleNamespace(guild=None))
        assert result is ctx
        bot.fetch_user_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefix_context_still_loads_settings(monkeypatch, tmp_path):
    settings = Config.from_mapping(
        {"bot": {"db_connection_string": "sqlite+aiosqlite:///:memory:"}}
    )
    async with ChuniBot(
        settings, offline=True, extensions=(), cache_dir=tmp_path / "cache"
    ) as bot:
        assert bot.intents.guild_reactions
        ctx = SimpleNamespace(
            command=SimpleNamespace(), interaction=None, author=SimpleNamespace(id=11)
        )
        monkeypatch.setattr(
            commands.AutoShardedBot, "get_context", AsyncMock(return_value=ctx)
        )
        monkeypatch.setattr(bot, "fetch_user_config", AsyncMock(return_value="loaded"))
        await bot.get_context(SimpleNamespace(guild=None))
        bot.fetch_user_config.assert_awaited_once_with(11)
        assert ctx.user_config == "loaded"


@pytest.mark.asyncio
async def test_late_interaction_is_still_allowed_to_attempt_response():
    interaction = SimpleNamespace(
        id=123,
        created_at=datetime.now(UTC) - timedelta(seconds=2.9),
        user=SimpleNamespace(id=11),
        client=SimpleNamespace(
            is_ready=Mock(return_value=True), is_owner=AsyncMock(return_value=True)
        ),
    )
    tree = object.__new__(PenguinCommandTree)
    assert await tree.interaction_check(interaction)
