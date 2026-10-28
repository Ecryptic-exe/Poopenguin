"""Global Discord event hooks; shared presentation survives this Cog's unload."""

import logging
import re
import sys
from typing import TYPE_CHECKING

import discord
from discord.app_commands import AppCommandError
from discord.ext import commands, tasks

from chuni_penguin.context import PenguinContext
from chuni_penguin.logging import logger
from chuni_penguin.privacy import private_command

STATUS_MESSAGES = (
    "Type {prefix}help or ping me for full command manual",
    "Listening to Ice - Floor Of Lava",
    "ね、簡単でしょ？",
    "Contact natherox on Discord for support",
)

_activity_logger = logging.getLogger("chuni_penguin.channel_activity")
_activity_logger.setLevel(logging.INFO)
_activity_logger.propagate = False
if not _activity_logger.handlers:
    _activity_handler = logging.StreamHandler(sys.stdout)
    _activity_handler.setFormatter(logging.Formatter("%(message)s"))
    _activity_logger.addHandler(_activity_handler)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_ -]?key|cookie|authorization)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_DISCORD_TOKEN = re.compile(r"(?<![\w-])[\w-]{20,}\.[\w-]{5,}\.[\w-]{20,}(?![\w-])")
_SENSITIVE_INPUT_COMMANDS = frozenset({"kamaitachi link"})

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.errors = bot.services.errors
        self._status_index = 0

    async def cog_load(self):
        self._old_tree_error = self.bot.tree.on_error
        self.bot.tree.on_error = self.tree_on_error

    async def cog_unload(self):
        self.rotate_status.cancel()
        if self.bot.tree.on_error == self.tree_on_error:
            self.bot.tree.on_error = self._old_tree_error

    @commands.Cog.listener()
    async def on_ready(self):
        await logger.ainfo(f"Logged in as {self.bot.user}", tag="bot_ready")
        if not self.rotate_status.is_running():
            self.rotate_status.start()

    @tasks.loop(seconds=10, reconnect=True)
    async def rotate_status(self):
        prefix = self.bot.settings.bot.default_prefix
        status = STATUS_MESSAGES[self._status_index].format(prefix=prefix)
        self._status_index = (self._status_index + 1) % len(STATUS_MESSAGES)
        await self.bot.change_presence(activity=discord.Game(name=status))

    @rotate_status.before_loop
    async def before_rotate_status(self):
        await self.bot.wait_until_ready()

    async def tree_on_error(
        self, interaction: discord.Interaction["ChuniBot"], error: AppCommandError
    ):
        await self.errors.tree_on_error(interaction, error)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Print one readable activity line for every message, including bot replies."""
        content = " ".join(message.clean_content.split())

        # Prefix auth commands can contain credentials in their arguments. Resolve
        # the command without invoking it, then keep only its name in the log.
        try:
            # Logging needs the command name, not a second database settings read.
            ctx = await commands.Bot.get_context(self.bot, message)
        except Exception:
            ctx = None

        if ctx is not None and ctx.command is not None:
            qualified_name = ctx.command.qualified_name
            if (
                private_command(ctx.command)
                or qualified_name in _SENSITIVE_INPUT_COMMANDS
            ):
                content = f"{ctx.prefix or ''}{qualified_name} <redacted>"

        content = _SECRET_ASSIGNMENT.sub(r"\1=<redacted>", content)
        content = _DISCORD_TOKEN.sub("<redacted>", content)
        if message.author.bot and message.guild is None and content:
            content = "<private DM response>"

        guild = message.guild
        server_name = guild.name if guild is not None else "Direct Message"
        channel_name = getattr(message.channel, "name", "DM")
        author_name = str(message.author)
        activity_line = (
            f"[Channel Activity] Server: '{server_name}' | "
            f"Channel: #{channel_name} ({message.channel.id}) | "
            f"Author: {author_name} -> Sent: '{content}'"
        )
        _activity_logger.info(activity_line)

    @commands.Cog.listener()
    async def on_command_error(
        self, ctx: PenguinContext, error: commands.errors.CommandInvokeError
    ):
        await self.errors.on_command_error(ctx, error)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(EventsCog(bot))
