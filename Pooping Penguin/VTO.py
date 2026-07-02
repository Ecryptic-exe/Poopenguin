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
"""
import asyncio
import logging  # Logs for debugging, Comment out this line if not needed
import discord
from discord.ext import commands

from key import api  # api = "your-bot-token-here", see key.py.example

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
