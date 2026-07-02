"""
Everything that used to live inside the single giant on_message() in
vto.py, split into named steps:

  1. auto-react to messages in configured channels
  2. bot-mention -> show help
  3. process !commands
  4. keyword-triggered copypasta responses (now data-driven, see
     keyword_manager.py, instead of the old hardcoded if/elif chain)
  5. "three different users said the same thing in a row" repeat echo

Kept as one on_message listener (rather than 5 separate ones) because
steps 2-5 are mutually exclusive-ish and ordered the same way the
original bot behaved - splitting them into independent listeners would
change behaviour subtly (e.g. commands firing at the same time as
keyword matches).
"""
import discord
from discord.ext import commands

from config import load_settings, save_settings
from i18n import get_guild_language
from keyword_manager import KeywordManager

# How many identical consecutive messages (from different authors) in a
# channel triggers the bot to repeat it back.
REPEAT_THRESHOLD = 3


class MessagesCog(commands.Cog, name="messages"):
    def __init__(self, bot):
        self.bot = bot
        self.keywords = KeywordManager()
        # channel_id -> list[{'content': str, 'author': int}], most recent last
        self.recent_messages = {}

    async def _handle_autoreact(self, message):
        settings = load_settings()
        channel_id = str(message.channel.id)
        autoreact = settings.get("autoreact", {})
        if channel_id not in autoreact:
            return

        entry = autoreact[channel_id]
        # Handle legacy format (bare emoji string instead of a dict).
        if isinstance(entry, str):
            entry = {"emoji": entry, "user_id": None}
            settings["autoreact"][channel_id] = entry
            save_settings(settings)

        emoji = entry["emoji"]
        user_id = entry.get("user_id")
        if user_id is None or str(message.author.id) == user_id:
            try:
                await message.add_reaction(emoji)
            except discord.Forbidden:
                print(f"Failed to auto-react in channel {channel_id}: Missing permissions")
            except discord.HTTPException:
                print(f"Failed to auto-react in channel {channel_id}: Invalid emoji")

    async def _handle_mention(self, message) -> bool:
        """Returns True if this message was a bot-mention and has been handled."""
        if not (self.bot.user.mentioned_in(message) and not message.mention_everyone):
            return False

        content = message.content.strip().lower()
        command = (
            content.replace(f"<@!{self.bot.user.id}>", "")
            .replace(f"<@{self.bot.user.id}>", "")
            .strip()
        )
        ctx = await self.bot.get_context(message)
        language = get_guild_language(load_settings(), message.guild.id)

        if not command or command in ("poop penguin", "help"):
            await self.bot.get_command("help")(ctx)
            return True

        valid_commands = [c.name for c in self.bot.commands]
        if command in valid_commands:
            await self.bot.get_command("help")(ctx, command=command)
        else:
            from i18n import t
            await message.channel.send(t(language,
                f"No command named `{command}` found. Use `!help` to see all available commands.",
                f"未找到名為 `{command}` 的命令。使用 `!help` 查看所有可用命令。"))
        return True

    async def _handle_keywords(self, message):
        content = message.content.strip()
        if not content:
            return
        match = self.keywords.find_match(content)
        if not match:
            return
        set_id, response = match
        try:
            await message.channel.send(response)
            print(f"Bot sent response from keyword set '{set_id}' in channel {message.channel.id}")
        except discord.Forbidden:
            print(f"Failed to send keyword response in channel {message.channel.id}: Missing permissions")

    async def _handle_repeats(self, message):
        content = message.content.strip()
        if not content:
            return
        channel_id = message.channel.id
        history = self.recent_messages.setdefault(channel_id, [])
        history.append({"content": content, "author": message.author.id})
        del history[:-REPEAT_THRESHOLD]  # keep only the last REPEAT_THRESHOLD

        if len(history) == REPEAT_THRESHOLD:
            same_content = len({m["content"].lower() for m in history}) == 1
            distinct_authors = len({m["author"] for m in history}) == REPEAT_THRESHOLD
            if same_content and distinct_authors:
                try:
                    await message.channel.send(content)
                    self.recent_messages[channel_id] = []
                except discord.Forbidden:
                    print(f"Failed to send repeat message in channel {channel_id}: Missing permissions")

    @commands.Cog.listener()
    async def on_message(self, message):
        # DEBUG CHANNEL LOG, Comment out this section if not needed
        # This will log EVERY message the bot sees across all servers and channels, even from other bots.
        guild_name = message.guild.name if message.guild else "Direct Message"
        print(
            f"[Channel Activity] Server: '{guild_name}' | Channel: #{message.channel} ({message.channel.id}) | Author: {message.author} -> Sent: '{message.content}'")
        # L__________

        if message.author.bot:
            return

        await self._handle_autoreact(message)

        if await self._handle_mention(message):
            return

        # NOTE: don't call self.bot.process_commands(message) here.
        # commands.Bot already runs process_commands() from its own
        # built-in on_message handler, and Cog listeners are added
        # *alongside* that (not as a replacement for it). Calling it
        # again here made every "!" command fire twice.
        await self._handle_keywords(message)
        await self._handle_repeats(message)


async def setup(bot):
    await bot.add_cog(MessagesCog(bot))
