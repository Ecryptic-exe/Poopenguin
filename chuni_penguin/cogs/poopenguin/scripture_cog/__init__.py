"""Guild chat replies using the original Perlica quotation index."""

import asyncio
import logging
from collections import deque
from pathlib import Path
from time import monotonic
from typing import TypedDict
from weakref import WeakKeyDictionary

import discord
from discord.ext import commands

from chuni_penguin.cogs.poopenguin.config import DATA_DIR

from .matching import Quote, QuoteIndex, load_settings

LOGGER = logging.getLogger(__name__)
BUNDLED_SETTINGS = Path(__file__).with_name("settings.json")
GLOBAL_COOLDOWN = 3.0
USER_MESSAGE_TYPES = (discord.MessageType.default, discord.MessageType.reply)


class ReplyWindow(TypedDict):
    next_reply: float
    replied_messages: deque[int]


class ScriptureReply(commands.Cog):
    def __init__(
        self, bot: commands.Bot, index: QuoteIndex, channel_ids: frozenset[int]
    ) -> None:
        self.bot = bot
        self.index = index
        self.channel_ids = channel_ids
        # Indexes follow the extension lifetime. Rate limits follow the bot lifetime.
        self.window: ReplyWindow = vars(bot).setdefault(
            "_scripture_reply_window",
            ReplyWindow(next_reply=0.0, replied_messages=deque(maxlen=256)),
        )
        self._search_slots = asyncio.BoundedSemaphore(2)
        self._searching: set[int] = set()

    def _limited(self, message_id: int, now: float) -> bool:
        return (
            now < self.window["next_reply"]
            or message_id in self.window["replied_messages"]
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # The messages extension explicitly forwards ordinary guild chat after
        # its moderation, mention, keyword and repeat handlers, in that order.
        if self.bot.get_cog("messages") is None:
            await self._reply_to_chat(message, check_command=True)

    @commands.Cog.listener()
    async def on_poopenguin_chat_message(self, message: discord.Message) -> None:
        await self._reply_to_chat(message, check_command=False)

    async def _reply_to_chat(
        self, message: discord.Message, *, check_command: bool
    ) -> None:
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or message.type not in USER_MESSAGE_TYPES
            or (self.channel_ids and message.channel.id not in self.channel_ids)
            or not message.content
            or self._limited(message.id, monotonic())
            or message.id in self._searching
        ):
            return
        if check_command and (await commands.Bot.get_context(self.bot, message)).valid:
            return

        try:
            quote = await self._lookup(message)
        except Exception as error:  # noqa: BLE001 -- isolate the passive listener without logging chat.
            LOGGER.warning("Scripture lookup failed (%s)", type(error).__name__)
            return

        now = monotonic()
        if (
            quote is None
            or self.bot.get_cog(self.qualified_name) is not self
            or self._limited(message.id, now)
        ):
            return

        self.window["next_reply"] = now + GLOBAL_COOLDOWN
        self.window["replied_messages"].append(message.id)
        try:
            await message.reply(
                f"「{quote.text}」\n{quote.citation}",
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
            )
        except discord.HTTPException as error:
            LOGGER.warning("Scripture reply failed (HTTP %s)", error.status)

    async def _lookup(self, message: discord.Message) -> Quote | None:
        if self.index.fuzzy_enabled:
            if self._search_slots.locked() or message.id in self._searching:
                return None
            self._searching.add(message.id)
            try:
                async with self._search_slots:
                    quote = await asyncio.to_thread(self.index.lookup, message.content)
            finally:
                self._searching.discard(message.id)
        else:
            quote = self.index.lookup(message.content)
        return quote


def _load_index() -> tuple[QuoteIndex, frozenset[int]]:
    override = Path(DATA_DIR) / "scripture" / "settings.json"
    settings = load_settings(override if override.exists() else BUNDLED_SETTINGS)
    index = QuoteIndex.from_files(
        settings.edition_paths,
        settings.blocked_phrases,
        match_mode=settings.match_mode,
        aliases=settings.aliases,
        match_threshold=settings.match_threshold,
    )
    return index, settings.channel_ids


# discord.py restores a failed reload by calling the *old module's* setup.
# Keep its validated index with that module so rollback never rereads bad files.
_validated: WeakKeyDictionary[commands.Bot, tuple[QuoteIndex, frozenset[int]]] = (
    WeakKeyDictionary()
)


async def setup(bot: commands.Bot) -> None:
    if not (bot.intents.guild_messages and bot.intents.message_content):
        detail = "ScriptureReply requires Guild Messages and Message Content intents."
        LOGGER.error(detail)
        raise RuntimeError(detail)
    snapshot = _validated.get(bot)
    if snapshot is None:
        try:
            snapshot = await asyncio.to_thread(_load_index)
        except (OSError, ValueError) as error:
            # The settings may reference a private path. Omit exception text.
            LOGGER.error(  # noqa: TRY400
                "Cannot load scripture settings or editions (%s). Check settings.json and its edition paths.",
                type(error).__name__,
            )
            raise
    index, channels = snapshot
    await bot.add_cog(ScriptureReply(bot, index, channels))
    _validated[bot] = snapshot
