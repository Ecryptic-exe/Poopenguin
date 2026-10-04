"""
CHUNITHM song search + player-population rating charts.

The commands themselves are subcommands of the /chunithm group, which is
defined in cogs/chunithm_cog.py: /chunithm rating and /chunithm song_search
(and the !chunithm ... prefix versions). A cog can't own subcommands of a
group that lives in another cog, so ChunithmCog's two subcommands are thin
wrappers that call rating() / song_search() / song_autocomplete() /
handle_error() below. This cog holds the engine, the service lifecycle and
the Discord UI pieces; it has no commands of its own.

This cog wraps the standalone chunithm-rating-bot engine (the `ratingbot/`
package plus `engine/` and `assets/` at the project root) so it runs inside
this bot instead of as a second Discord client. The engine itself is
unchanged; everything Discord-facing lives here.

How a request flows
  1. /chunithm rating song:<text> difficulty:<D>
       - exactly one song matches the text  -> chart it straight away
       - several candidates                 -> dropdown, the requester picks
  2. ChartService (ratingbot/service.py) checks its 24h on-disk cache, and on a
     miss runs ONE worker subprocess for that single chart: fetch the public
     Chunirec page, run the numeric engine, draw a PNG.
  Nothing is fetched or drawn at startup, while searching, or in the
  background - only for the chart somebody explicitly selected.

Python 3.9 note: this file deliberately avoids `X | Y` unions and other 3.10+
syntax (the engine itself was verified on 3.9 too).

Prefix usage differs slightly from slash because "!" arguments are split on
spaces: wrap multi-word song titles in quotes, e.g.
!chunithm rating "XL TECHNO" MASTER   (slash has no such limit, and offers
autocomplete).
"""
import importlib.util
import logging
import sys
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

from config import load_settings
from i18n import t, get_guild_language
from ratingbot.catalog import DIFFICULTIES, difficulty_name   # stdlib only

# These two import requests / bs4 / python-dotenv. If one is not installed we
# must not blow up while the extension is being imported (that would stop the
# whole bot from starting) - setup() reports it and skips this cog instead.
try:
    from ratingbot.config import Settings, ensure_catalog
    from ratingbot.service import ChartService, ChartError
except ImportError as exc:
    Settings = ensure_catalog = ChartService = None
    _IMPORT_ERROR = exc

    class ChartError(Exception):
        """Placeholder so this module still imports; the cog is not loaded."""
else:
    _IMPORT_ERROR = None

logger = logging.getLogger("DiscordBot")

# The chart worker subprocess needs these; checking up front means a missing
# package shows up in the log at startup instead of as a failed first chart.
REQUIRED_MODULES = ["dotenv", "requests", "bs4", "numpy", "matplotlib", "PIL"]
if sys.platform == "win32":
    REQUIRED_MODULES.append("tzdata")   # zoneinfo has no system tz database on Windows

EMBED_COLOR = 0x008C83
AUTOCOMPLETE_LIMIT = 25      # Discord's hard cap per autocomplete response
SELECT_TIMEOUT = 120         # seconds the song dropdown stays usable


def _lang_for(guild) -> str:
    """Guild language ("english"/"chinese"); DMs and unknown guilds -> english."""
    if guild is None:
        return "english"
    return get_guild_language(load_settings(), guild.id)


def _chart_message(result: dict, song, difficulty: str):
    """Build the (embed, file) pair for a finished chart result."""
    lines = result.get("numeric_summary", {}).get(
        "compact_lines", ["個人差：資料不足", "吃分：資料不足"])
    embed = discord.Embed(
        title=f"{song.title}／{difficulty}"[:256],
        url=result["source_url"],
        color=EMBED_COLOR,
        description="\n".join(lines))
    filename = f"rating_{song.song_id}_{difficulty}.png"
    embed.set_image(url="attachment://" + filename)
    return embed, discord.File(result["image"], filename=filename)


class SongChoiceView(discord.ui.View):
    """Dropdown shown when the typed text matched several songs."""

    def __init__(self, cog, songs, difficulty, requester_id, detailed, language):
        super().__init__(timeout=SELECT_TIMEOUT)
        self.cog = cog
        self.difficulty = difficulty
        self.requester_id = requester_id
        self.detailed = detailed
        self.language = language
        self.songs = {s.song_id: s for s in songs}
        self.started = False
        self.message = None   # set by the cog after the view is sent

        self.select = discord.ui.Select(
            placeholder=t(language, "Pick the song to chart", "選擇要產圖的曲目"),
            options=[
                discord.SelectOption(
                    label=s.title[:100], value=s.song_id,
                    description=f"{difficulty} · ID {s.song_id}")
                for s in songs
            ])
        self.select.callback = self.selected
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                t(self.language,
                  "Please run your own /chunithm rating command to pick a song.",
                  "請使用自己的 /chunithm rating 指令選曲。"),
                ephemeral=True)
            return False
        return True

    async def selected(self, interaction: discord.Interaction):
        if self.started:
            await interaction.response.send_message(
                t(self.language,
                  "This selection is already being processed.",
                  "這個選曲已開始處理。"),
                ephemeral=True)
            return
        self.started = True
        song = self.songs[self.select.values[0]]
        await interaction.response.defer()
        await interaction.edit_original_response(
            content=t(self.language,
                      f"Processing {discord.utils.escape_markdown(song.title)}／{self.difficulty}…",
                      f"正在處理 {discord.utils.escape_markdown(song.title)}／{self.difficulty}…"),
            view=None)
        self.stop()
        try:
            result = await self.cog.service.get_chart(
                song, self.difficulty, detailed=self.detailed)
            embed, file = _chart_message(result, song, self.difficulty)
            try:
                await interaction.edit_original_response(
                    content=None, embed=embed, attachments=[file], view=None)
            finally:
                file.close()
        except ChartError as error:
            await interaction.edit_original_response(
                content=str(error), embed=None, attachments=[], view=None)
        except Exception:
            logger.exception("rating: chart failed after song selection")
            await interaction.edit_original_response(
                content=t(self.language,
                          "Something went wrong while building that chart; please try again later.",
                          "指令處理失敗；請稍後再試。"),
                embed=None, attachments=[], view=None)

    async def on_timeout(self):
        # Disable the dropdown instead of leaving a dead-but-clickable control.
        if self.message is None or self.started:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class RatingCog(commands.Cog, name="rating"):
    def __init__(self, bot, settings, catalog):
        self.bot = bot
        self.settings = settings
        self.catalog = catalog
        # Built inside the running event loop (the cog is loaded from
        # bot.py's async main), which Python 3.9's asyncio primitives need.
        self.service = ChartService(settings)

    async def cog_unload(self):
        await self.service.close()

    def _lang(self, ctx) -> str:
        return _lang_for(ctx.guild)

    async def handle_error(self, ctx, error):
        """Called by ChunithmCog.cog_command_error for rating / song_search."""
        language = self._lang(ctx)
        # Unwrap CommandInvokeError / HybridCommandError to the real cause.
        cause = getattr(error, "original", None) or error.__cause__ or error
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(t(language,
                f"Please wait {error.retry_after:.0f}s before searching again.",
                f"請稍等 {error.retry_after:.0f} 秒再查詢。"), ephemeral=True)
            return
        if isinstance(cause, ChartError):
            await ctx.send(str(cause))
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            await ctx.send(t(language,
                "Invalid arguments. Use `!help chunithm` for usage "
                "(wrap multi-word song titles in quotes).",
                "參數無效。使用 `!help chunithm` 查看用法（曲名含空格時請加引號）。"),
                ephemeral=True)
            return
        logger.error("rating: %s failed for %s: %r", ctx.command, ctx.author, cause,
                     exc_info=cause if isinstance(cause, BaseException) else None)
        await ctx.send(t(language,
            "Something went wrong while handling that command; please try again later.",
            "指令處理失敗；請稍後再試。"), ephemeral=True)

    # -- autocomplete -----------------------------------------------------
    async def song_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        difficulty = getattr(interaction.namespace, "difficulty", None) or "MASTER"
        try:
            rows = self.catalog.search(current or "", difficulty, AUTOCOMPLETE_LIMIT)
        except ValueError:
            rows = []
        # value = id:<source id> so same-titled songs can't be confused.
        return [app_commands.Choice(name=r["song"].title[:100], value="id:" + r["song"].song_id)
                for r in rows]

    # -- /chunithm rating ---------------------------------------------------
    async def rating(self, ctx, song: str, difficulty: str = "MASTER", detailed: bool = False):
        """Look up a song and draw its player-population rating chart."""
        language = self._lang(ctx)

        try:
            difficulty = difficulty_name(difficulty)
        except ValueError:
            await ctx.send(t(language,
                f"Unknown difficulty `{difficulty}`. Use one of: {', '.join(DIFFICULTIES)}.\n"
                'Wrap multi-word titles in quotes, e.g. `!chunithm rating "XL TECHNO" MASTER`.',
                f"未知的難度 `{difficulty}`。可用：{', '.join(DIFFICULTIES)}。\n"
                '曲名含空格時請加上引號，例如 `!chunithm rating "XL TECHNO" MASTER`。'),
                ephemeral=True)
            return

        exact = self.catalog.exact_matches(song, difficulty)
        if len(exact) == 1:
            await self._deliver(ctx, exact[0], difficulty, detailed)
            return

        known = self.catalog.exact_matches(song)
        if known and not exact:
            available = sorted({d for s in known for d in s.difficulties}, key=DIFFICULTIES.index)
            await ctx.send(t(language,
                f"The song list has no {difficulty} chart for that song. Available: {', '.join(available)}",
                f"提供的曲目資料未列此難度。可選：{', '.join(available)}"),
                ephemeral=True)
            return

        candidates = self.catalog.search(song, difficulty, AUTOCOMPLETE_LIMIT)
        if not candidates:
            await ctx.send(t(language, "No matching songs found.", "沒有找到符合的曲目。"),
                           ephemeral=True)
            return

        view = SongChoiceView(self, [r["song"] for r in candidates], difficulty,
                              ctx.author.id, detailed, language)
        view.message = await ctx.send(
            t(language, "Pick the song you want to see:", "請選擇要查看的曲目："), view=view)

    async def _deliver(self, ctx, song, difficulty, detailed):
        """Chart one already-resolved song and post the result."""
        await ctx.defer()
        try:
            result = await self.service.get_chart(song, difficulty, detailed=detailed)
        except ChartError as error:
            await ctx.send(str(error))
            return
        embed, file = _chart_message(result, song, difficulty)
        try:
            await ctx.send(embed=embed, file=file)
        finally:
            file.close()

    # -- /chunithm song_search ----------------------------------------------
    async def song_search(self, ctx, query: str):
        """Search CHUNITHM songs and their available difficulties."""
        language = self._lang(ctx)
        rows = self.catalog.search(query, limit=10)
        if not rows:
            await ctx.send(t(language, "No matching songs found.", "沒有找到符合的曲目。"),
                           ephemeral=True)
            return
        text = "\n".join(
            f"{i}. **{discord.utils.escape_markdown(r['song'].title)}** · "
            f"{', '.join(r['song'].difficulties)} · `id:{r['song'].song_id}`"
            for i, r in enumerate(rows, 1))
        await ctx.send(text[:1950], ephemeral=True)


async def setup(bot):
    # The rating feature is optional: if its packages, data or assets are
    # missing or broken, log why and let the rest of the bot start instead of
    # failing the whole load.
    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if _IMPORT_ERROR is not None or missing:
        logger.error("rating_cog NOT loaded: missing Python packages (%s). "
                     "Run: pip install -r requirements.txt",
                     _IMPORT_ERROR or ", ".join(missing))
        return
    try:
        settings = Settings.load()
        catalog = ensure_catalog(settings.root)
        cog = RatingCog(bot, settings, catalog)
    except Exception:
        logger.exception("rating_cog NOT loaded (check ratingbot/, engine/, assets/ and "
                         "data/catalog.json are present and requirements are installed)")
        return
    await bot.add_cog(cog)
    logger.info("rating_cog loaded: %d songs, %d charts",
                len(catalog.songs), catalog.metadata["charts_count"])
