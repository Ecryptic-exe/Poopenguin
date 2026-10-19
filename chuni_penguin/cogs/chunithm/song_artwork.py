"""Original song artwork choices, independent of song-search commands."""

from typing import TYPE_CHECKING

from discord.ext import commands

from chuni_penguin.context import PenguinContext
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.utils import get_jacket_url

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class SongArtworkCog(commands.Cog, name="SongArtwork"):
    def __init__(self, bot: "ChuniBot"):
        self.settings = bot.settings
        self.database = bot.services.database

    @commands.hybrid_command("jacket")
    @logged_prefix_command
    async def jacket(self, ctx: PenguinContext, *, query: str):
        """Get a song's jacket art."""

        async with ctx.typing():
            song, _, _ = await ctx.find_song(query)

            if song is None:
                return

            if query == "67" and song.id == 45:
                await self.database.user_found_easter_egg(
                    ctx.author.id, "L9-upside-down-is-67"
                )
                await ctx.reply(
                    f"{self.settings.web.base_url}/assets/jackets/45_67.webp"
                )
            elif (
                song.id == 2698
                and self.settings.web.is_accessible
                and ctx.user_config.synthesis_alt_jacket not in {"none", "default"}
            ):
                await ctx.reply(
                    f"{self.settings.web.base_url}/assets/jackets/{song.id}_{ctx.user_config.synthesis_alt_jacket}.webp"
                )
            elif self.settings.web.is_accessible:
                await ctx.reply(
                    f"{self.settings.web.base_url}/assets/jackets/{song.id}.webp"
                )
            else:
                await ctx.reply(get_jacket_url(song))


async def setup(bot: "ChuniBot"):
    await bot.add_cog(SongArtworkCog(bot))
