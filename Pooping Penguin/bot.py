"""
Bot entry point.

Everything that used to be one 1000-line vto.py is now:
  - config.py            settings/votes/keywords JSON persistence
  - i18n.py               t() / get_guild_language() translation helper
  - keyword_manager.py    engine behind the global keyword-triggered responses
  - legacy_copypasta.py   the original hardcoded copypasta strings (only
                           used by migrate_keywords.py now, kept for reference)
  - cogs/                 one file per command group, loaded as extensions
  - data/keyword_sets.json  the live, editable keyword data (see
                           migrate_keywords.py to (re)generate it)

This file's only job is to build the bot, load the cogs, and run it.

--- Slash commands ---
Every user-facing command in cogs/ is now defined with
commands.hybrid_command / commands.hybrid_group instead of
commands.command / commands.group. A hybrid command is a single
function that discord.py exposes BOTH as a "!"-prefixed text command
AND as a "/" slash command - there is only one implementation, so the
two entry points can never drift apart.

Slash commands have to be registered ("synced") with Discord separately
from just loading the cogs, which is why setup_hook() below calls
bot.tree.sync(). A few things to know about this:
  - setup_hook() runs exactly once per process, right after login and
    before the gateway connects - unlike on_ready (which fires again
    on every reconnect), so the sync only ever happens once per run.
  - Global syncs (no guild argument) can take up to an hour to show
    up for users the first time Discord caches them.
  - For instant updates while developing, set a DEV_GUILD_ID env var
    to a server ID you control; setup_hook() will sync to that guild
    only, which Discord applies instantly. It also clears out any
    global copies of the same commands (see the comment in
    setup_hook()) - otherwise a command registered both globally and
    to the dev guild shows up twice in Discord's "/" picker.
  - The bot must be re-invited (or have its existing invite updated)
    with the `applications.commands` OAuth2 scope, not just `bot`,
    or slash commands won't appear at all. See GAPS.md for this and
    other setup items still needed.
"""
import asyncio
import logging  # Logs for debugging, Comment out this line if not needed
import os

import discord
from discord.ext import commands

from key import api  # api = "your-bot-token-here", see key.py.example

# Optional: set this env var to a server ID you control for instant
# (guild-scoped) slash command sync during development. Leave unset to
# sync globally (works everywhere, but can take up to ~1h to appear).
DEV_GUILD_ID = os.environ.get("DEV_GUILD_ID")

# Configure logging format and level, Comment out this section if not needed
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),                # Output to terminal
        logging.FileHandler('bot_responses.log') # Save to a file
    ]
)
logger = logging.getLogger('DiscordBot')
# L__________


# Cog modules to load. Order doesn't matter for these - none of them
# depend on another cog being loaded first.
INITIAL_EXTENSIONS = (
    "cogs.help_cog",
    "cogs.vote_cog",
    "cogs.admin_cog",
    "cogs.general_cog",
    "cogs.keywords_cog",
    "cogs.messages_cog",
    "cogs.copypasta_cog",
)

STATUS_MESSAGES = [
    "Type !help or ping me for full command manual",
    "Listening to Ice - Floor Of Lava",
    "ね、簡単でしょ？",
    "Contact natherox on Discord for support",
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
# help_cog.py provides its own !help, so the default one has to go.
bot.remove_command("help")

async def rotate_status():
    while True:
        for status in STATUS_MESSAGES:
            await bot.change_presence(activity=discord.Game(name=status))
            await asyncio.sleep(10)

async def setup_hook():
    # Register (sync) the slash-command versions of every hybrid command
    # with Discord. Without this, "!" commands work immediately but "/"
    # commands never show up in Discord's UI.
    #
    # This lives in setup_hook (called exactly once, right after login,
    # before the gateway connection opens) rather than on_ready, because
    # on_ready fires again on every reconnect - syncing there doesn't
    # corrupt anything by itself, but doing it repeatedly is wasteful
    # and easy to reason about wrong, so once-per-process is simpler.
    try:
        if DEV_GUILD_ID:
            guild = discord.Object(id=int(DEV_GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash command(s) to dev guild {DEV_GUILD_ID}.")

            # If this bot was ever globally synced before DEV_GUILD_ID was
            # set (or by an older deploy), those global commands are still
            # registered with Discord independently of the guild-scoped
            # ones above - Discord treats "global copy" and "guild copy"
            # of a same-named command as two separate entries, which is
            # what makes every command show up twice in the "/" picker.
            # Wiping the global command list here (bulk-overwrite with
            # nothing) clears out any such stale globals so only the
            # guild-scoped set remains.
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally (may take up to ~1h to appear).")
    except discord.Forbidden:
        print("Failed to sync slash commands: bot is missing the "
              "`applications.commands` scope. See GAPS.md.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

bot.setup_hook = setup_hook

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.loop.create_task(rotate_status())

# Command & Reaction Logs, Comment out this section if not needed
# Custom Message Logs in message_cog.py
@bot.event
async def on_command(ctx):
    # Logs when a command is received
    logger.info(f"User {ctx.author} ({ctx.author.id}) invoked command: !{ctx.command}")

@bot.event
async def on_command_completion(ctx):
    # Logs when a command successfully finished executing
    logger.info(f"Successfully responded to {ctx.author} for command: !{ctx.command}")

@bot.event
async def on_command_error(ctx, error):
    # Logs errors/failures
    logger.error(f"Error executing !{ctx.command} for {ctx.author}: {error}")
# L__________

async def main():
    async with bot:
        for extension in INITIAL_EXTENSIONS:
            await bot.load_extension(extension)
        await bot.start(api)


if __name__ == "__main__":
    asyncio.run(main())