"""
Bot entry point.

This file's only job is to build the bot, load the cogs, and run it.

  - settings.py          token, branding and optional settings (env vars / key.py)
  - config.py            JSON load/save helpers and the data/ file paths
  - i18n.py              t() / get_guild_language() English/Chinese helper
  - keyword_manager.py   engine behind the global keyword-triggered responses
  - copypasta_manager.py engine behind the fill-in-the-blank template pools
  - gacha_manager.py     gacha pull logic, rates, pity, per-user records
  - cogs/                one file per command group, loaded as extensions
  - data/                the live, hand-editable JSON data

--- Hybrid commands ---------------------------------------------------------
Every user-facing command in cogs/ is a commands.hybrid_command /
hybrid_group: one function that discord.py exposes BOTH as a prefix
command ("!help") AND as a slash command ("/help"). There's a single
implementation, so the two entry points can never drift apart.

--- Slash-command sync ------------------------------------------------------
Slash commands have to be registered ("synced") with Discord separately from
loading the cogs, which is why setup_hook() calls bot.tree.sync().
  - setup_hook() runs exactly once per process, unlike on_ready (which fires
    again on every reconnect).
  - Global syncs can take up to an hour to show up the first time.
  - Set DEV_GUILD_ID (env var or key.py) to a server you control to sync
    instantly to that server only. Stale global copies are cleared so
    commands don't appear twice in the "/" picker.
  - The bot must be invited with BOTH the `bot` and `applications.commands`
    OAuth2 scopes, or slash commands won't appear.
  - The owner-only `!sync` command (cogs/admin_cog.py) re-syncs on demand.
"""
import asyncio
import logging
import sys

import discord
from discord.ext import commands

import settings

# Logging: console + bot_responses.log. Remove this block if you don't want it.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot_responses.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("DiscordBot")

# Cog modules to load. Order doesn't matter - none depend on another.
# Comment a line out to disable that feature (e.g. "cogs.ai_cog" if you
# don't run a local model). A cog that fails to load is logged and skipped
# rather than stopping the whole bot.
INITIAL_EXTENSIONS = (
    "cogs.help_cog",
    "cogs.vote_cog",
    "cogs.admin_cog",
    "cogs.general_cog",
    "cogs.keywords_cog",
    "cogs.messages_cog",
    "cogs.copypasta_cog",
    "cogs.gacha_cog",
    "cogs.ai_cog",
)

# Rotating "Playing ..." status, 10 seconds each.
STATUS_MESSAGES = [
    f"Type {settings.COMMAND_PREFIX}help or ping me for the command manual",
    "Edit STATUS_MESSAGES in bot.py",
]
if settings.SUPPORT_CONTACT:
    STATUS_MESSAGES.append(f"Contact {settings.SUPPORT_CONTACT} for support")

intents = discord.Intents.default()
intents.message_content = True   # enable in the Developer Portal -> Bot -> Privileged intents
intents.members = True           # same: Server Members intent

bot = commands.Bot(command_prefix=settings.COMMAND_PREFIX, intents=intents)
# help_cog.py provides its own help command, so the default one has to go.
bot.remove_command("help")


async def rotate_status():
    while True:
        for status in STATUS_MESSAGES:
            await bot.change_presence(activity=discord.Game(name=status))
            await asyncio.sleep(10)


async def setup_hook():
    """Register slash commands once per process (see module docstring)."""
    try:
        if settings.DEV_GUILD_ID:
            guild = discord.Object(id=int(settings.DEV_GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash command(s) to dev guild {settings.DEV_GUILD_ID}.")

            # Discord treats the global copy and the guild copy of a command
            # as two separate entries, which is what makes every command show
            # up twice in the "/" picker. Wipe any stale globals.
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally (may take up to ~1h to appear).")
    except discord.Forbidden:
        print("Failed to sync slash commands: the bot is missing the `applications.commands` scope.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")


bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.loop.create_task(rotate_status())


# Command logs - remove this section if you don't want them.
@bot.event
async def on_command(ctx):
    logger.info(f"User {ctx.author} ({ctx.author.id}) invoked command: {ctx.command}")


@bot.event
async def on_command_completion(ctx):
    logger.info(f"Successfully responded to {ctx.author} for command: {ctx.command}")


@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Error executing {ctx.command} for {ctx.author}: {error}")
# ---------------------------------------------------------------------------


async def main():
    if not settings.DISCORD_TOKEN:
        sys.exit("No bot token found. Copy key.py.example to key.py and set DISCORD_TOKEN "
                 "(or set the DISCORD_TOKEN environment variable).")
    async with bot:
        for extension in INITIAL_EXTENSIONS:
            try:
                await bot.load_extension(extension)
            except Exception as e:
                logger.error(f"Failed to load {extension}: {e}")
        await bot.start(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
