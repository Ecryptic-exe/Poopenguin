# ruff: noqa: RUF001 -- Perlica preserves original fullwidth punctuation.
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord.ext import commands

from chuni_penguin.cogs.poopenguin import config
from chuni_penguin.cogs.poopenguin.scripture_cog.matching import (
    QuoteIndex,
    load_settings,
)

if TYPE_CHECKING:
    from chuni_penguin.cogs.poopenguin.messages_cog import MessagesCog
    from chuni_penguin.cogs.poopenguin.scripture_cog import ScriptureReply


EXTENSION = "chuni_penguin.cogs.poopenguin.scripture_cog"
MESSAGES = "chuni_penguin.cogs.poopenguin.messages_cog"
SCRIPTURE = Path(config.__file__).with_name("scripture_cog")
VERSE = "耶和華對摩西說：「你手裡拿的是什麼？」摩西說：「是一根手杖。」"
TRIGGER = "你手裡拿的是什麼"
CITATION = "出埃及記 4:2（繁中當代譯本）"


def test_original_editions_and_match_contract():
    settings = load_settings(SCRIPTURE / "settings.json")
    assert settings.channel_ids == frozenset()
    assert settings.match_mode == "bm25" and settings.match_threshold == 0.85
    index = QuoteIndex.from_files(
        settings.edition_paths, match_mode=settings.match_mode
    )
    for message, reference in [
        (TRIGGER, CITATION),
        ("＂你 手 裡 是 什 麼＂～～", "出埃及記 4:2（繁中新標點和合本）"),
        ("The lord is my sheperd", "Psalms 23:1（WEB）"),
    ]:
        quote = index.lookup(message)
        assert quote and quote.citation == reference
    quote = index.lookup(TRIGGER)
    assert quote and quote.text == VERSE
    for message in (
        "",
        "哈哈哈",
        "這是甚麼意思呢？",
        "thank you",
        "今天去超商買飲料嗎",
    ):
        assert index.lookup(message) is None


@pytest.fixture
def local_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "scripture"
    folder.mkdir()
    record = {
        "book": "出埃及記",
        "chapter": 4,
        "verse": "2",
        "text": VERSE,
        "edition": "繁中當代譯本",
    }
    (folder / "sample.jsonl").write_text(json.dumps(record), encoding="utf-8")
    settings = {
        "editions": ["sample.jsonl"],
        "match_mode": "variants",
        "channel_ids": ["100"],
        "aliases": {TRIGGER: VERSE},
    }
    (folder / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return folder


def offline_bot():
    intents = discord.Intents.none()
    intents.guilds = intents.guild_messages = intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    bot._connection.user = cast(
        discord.ClientUser, SimpleNamespace(id=999, mentioned_in=lambda _: False)
    )
    return bot


def message(bot, content=TRIGGER, *, msg_id=1, author_id=11, **overrides) -> Any:
    values = {
        "_state": bot._connection,
        "id": msg_id,
        "content": content,
        "channel": SimpleNamespace(id=100, send=AsyncMock()),
        "guild": SimpleNamespace(id=22),
        "author": SimpleNamespace(id=author_id, bot=False),
        "attachments": [],
        "type": discord.MessageType.default,
        "webhook_id": None,
        "reply": AsyncMock(),
    }
    return SimpleNamespace(**(values | overrides))


async def dispatch_chat(bot, msg, monkeypatch):
    # Drive discord.py's actual listener selection and command invocation. Drain
    # the tasks, including the guild-chat event produced by the messages Cog.
    jobs = []

    def schedule(callback, _name, *args, **kwargs):
        task = asyncio.create_task(callback(*args, **kwargs))
        jobs.append(task)
        return task

    with monkeypatch.context() as scope:
        scope.setattr(bot, "_schedule_event", schedule)
        bot.dispatch("message", msg)
        while jobs:
            pending = jobs.copy()
            jobs.clear()
            await asyncio.gather(*pending)


@pytest.mark.asyncio
async def test_scope_cooldown_duplicate_and_lifecycle(local_settings, monkeypatch):
    async with offline_bot() as bot:
        await bot.load_extension(EXTENSION)
        module = importlib.import_module(EXTENSION)
        clock = Mock(return_value=100.0)
        monkeypatch.setattr(module, "monotonic", clock)
        cases: tuple[dict[str, Any], ...] = (
            {"channel": SimpleNamespace(id=101)},
            {"guild": None},
            {"author": SimpleNamespace(id=12, bot=True)},
            {"webhook_id": 99},
            {"type": discord.MessageType.pins_add},
            {"content": ""},
        )
        for extra in cases:
            msg = message(bot, **extra)
            await dispatch_chat(bot, msg, monkeypatch)
            msg.reply.assert_not_awaited()
        msg = message(bot, type=discord.MessageType.reply)
        await dispatch_chat(bot, msg, monkeypatch)
        args, kwargs = msg.reply.await_args
        assert args == (f"「{VERSE}」\n{CITATION}",)
        assert kwargs["mention_author"] is False
        assert kwargs["allowed_mentions"].to_dict() == {"parse": []}
        await bot.reload_extension(EXTENSION)
        monkeypatch.setattr(importlib.import_module(EXTENSION), "monotonic", clock)
        assert len(bot.extra_events["on_message"]) == 1
        assert len(bot.extra_events["on_poopenguin_chat_message"]) == 1
        assert not bot.extra_events.get("on_message_edit")
        newer = message(bot, msg_id=2)
        clock.return_value = 102.999
        await dispatch_chat(bot, newer, monkeypatch)
        newer.reply.assert_not_awaited()
        clock.return_value = 103
        await dispatch_chat(bot, msg, monkeypatch)
        msg.reply.assert_awaited_once()
        await dispatch_chat(bot, newer, monkeypatch)
        newer.reply.assert_awaited_once()
        await bot.unload_extension(EXTENSION)
        assert bot.get_cog("ScriptureReply") is None
        assert not bot.extra_events["on_message"]
        assert not bot.extra_events["on_poopenguin_chat_message"]
    async with offline_bot() as fresh:
        await fresh.load_extension(EXTENSION)
        msg = message(fresh)
        await dispatch_chat(fresh, msg, monkeypatch)
        msg.reply.assert_awaited_once()
        wrong_channel = message(fresh, channel=SimpleNamespace(id=200))
        await dispatch_chat(fresh, wrong_channel, monkeypatch)
        wrong_channel.reply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_messages", [False, True])
async def test_commands_moderation_and_overlapping_chat(
    local_settings, monkeypatch, with_messages
):
    async with offline_bot() as bot:
        calls = []

        @bot.hybrid_command()
        async def ask(ctx, *, text: str):
            calls.append((ctx.author.id, text))

        await bot.load_extension(EXTENSION)
        messages = None
        if with_messages:
            await bot.load_extension(MESSAGES)
            messages = cast("MessagesCog", bot.get_cog("messages"))
            monkeypatch.setattr(
                messages.keywords, "find_match", lambda *_, **__: ("fixture", "keyword")
            )
        msg = message(bot, f"!ask {TRIGGER}")
        await dispatch_chat(bot, msg, monkeypatch)
        assert calls == [(11, TRIGGER)]
        assert bot.tree.get_command("ask") is not None
        msg.reply.assert_not_awaited()
        msg.channel.send.assert_not_awaited()

        if messages is not None:
            for handler in ("_handle_autoban", "_handle_mention"):
                with monkeypatch.context() as scope:
                    scope.setattr(messages, handler, AsyncMock(return_value=True))
                    msg = message(bot)
                    await dispatch_chat(bot, msg, monkeypatch)
                    msg.reply.assert_not_awaited()
                    msg.channel.send.assert_not_awaited()
            messages.recent_messages[100] = [
                {"content": TRIGGER, "author": user} for user in (12, 13)
            ]
        msg = message(bot)
        await dispatch_chat(bot, msg, monkeypatch)
        msg.reply.assert_awaited_once()
        if with_messages:
            assert [c.args for c in msg.channel.send.await_args_list] == [
                ("keyword",),
                (TRIGGER,),
            ]
            await bot.unload_extension(MESSAGES)
            independent = message(bot, msg_id=3)
            monkeypatch.setattr(
                importlib.import_module(EXTENSION), "monotonic", lambda: float("inf")
            )
            await dispatch_chat(bot, independent, monkeypatch)
            independent.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_failures_are_isolated_and_do_not_log_messages(
    local_settings, caplog, monkeypatch
):
    async with offline_bot() as bot:
        await bot.load_extension(EXTENSION)
        failed = message(bot)
        failed.reply.side_effect = discord.Forbidden(
            Mock(status=403, reason=""), "private detail"
        )
        await dispatch_chat(bot, failed, monkeypatch)
        await dispatch_chat(bot, failed, monkeypatch)
        failed.reply.assert_awaited_once()
        assert "HTTP 403" in caplog.text and "private detail" not in caplog.text
        assert TRIGGER not in caplog.text
        with monkeypatch.context() as scope:
            scope.setattr(
                cast("ScriptureReply", bot.get_cog("ScriptureReply")).index,
                "lookup",
                Mock(side_effect=RuntimeError("sensitive text")),
            )
            scope.setattr(
                importlib.import_module(EXTENSION), "monotonic", lambda: float("inf")
            )
            await dispatch_chat(bot, message(bot, msg_id=4), monkeypatch)
        assert (
            "Scripture lookup failed (RuntimeError)" in caplog.text
            and "sensitive text" not in caplog.text
        )
        (local_settings / "settings.json").write_text(
            '{"unexpected":true}', encoding="utf-8"
        )
        with pytest.raises(commands.ExtensionFailed):
            await bot.reload_extension(EXTENSION)
        restored = cast("ScriptureReply", bot.get_cog("ScriptureReply"))
        quote = restored.index.lookup(TRIGGER)
        assert quote and quote.citation == CITATION
        assert len(bot.extra_events["on_message"]) == 1
        await bot.unload_extension(EXTENSION)
        with pytest.raises(commands.ExtensionFailed):
            await bot.load_extension(EXTENSION)


@pytest.mark.asyncio
async def test_busy_duplicate_and_unloaded_searches_never_reply(
    local_settings, monkeypatch
):
    async with offline_bot() as bot:
        await bot.load_extension(EXTENSION)
        cog = cast("ScriptureReply", bot.get_cog("ScriptureReply"))
        lookup = Mock(return_value=cog.index.lookup(TRIGGER))
        monkeypatch.setattr(
            cog, "index", SimpleNamespace(fuzzy_enabled=True, lookup=lookup)
        )
        busy = asyncio.Event()
        release = asyncio.Event()
        started = 0

        async def delayed(callback, text):
            nonlocal started
            started += 1
            if started == 2:
                busy.set()
            await release.wait()
            return callback(text)

        monkeypatch.setattr(asyncio, "to_thread", delayed)
        first, second, third = (message(bot, msg_id=number) for number in (1, 2, 3))
        jobs = [asyncio.create_task(cog.on_message(msg)) for msg in (first, second)]
        try:
            await asyncio.wait_for(busy.wait(), timeout=2)
            await cog.on_message(first)
            await cog.on_message(third)
            await bot.unload_extension(EXTENSION)
        finally:
            release.set()
            await asyncio.gather(*jobs)
        assert started == 2
        for msg in (first, second, third):
            msg.reply.assert_not_awaited()


def test_original_fuzzy_boundary_and_strict_mode(local_settings):
    edition = local_settings / "sample.jsonl"
    record = json.loads(edition.read_text(encoding="utf-8"))
    record["text"] = "耶和華對摩西說：「你手裏是甚麼？」他說：「是杖。」"
    edition.write_text(json.dumps(record), encoding="utf-8")
    for threshold, accepted in ((0.85, True), (0.95, False)):
        index = QuoteIndex.from_files(
            [edition], match_mode="bm25", match_threshold=threshold
        )
        candidate = index.candidate(TRIGGER)
        assert candidate and candidate.score == pytest.approx(6 / 7)
        assert (index.lookup(TRIGGER) is not None) == accepted
    strict = QuoteIndex.from_files([edition], match_mode="strict")
    assert strict.lookup(TRIGGER) is None
    quote = strict.lookup(record["text"])
    assert quote and quote.citation == CITATION


@pytest.mark.asyncio
async def test_missing_intent_reports_actionable_reason(caplog):
    async with commands.Bot(command_prefix="!", intents=discord.Intents.none()) as bot:
        with pytest.raises(commands.ExtensionFailed):
            await bot.load_extension(EXTENSION)
        assert "Guild Messages and Message Content" in caplog.text
        assert bot.get_cog("ScriptureReply") is None
