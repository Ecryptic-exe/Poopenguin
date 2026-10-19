import random
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from discord.ext.commands import Context, Range

from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.services.chart_view import compose_chart_view as compose_chart_view
from chuni_penguin.services.rating_goals import (
    rating_reach_content as rating_reach_content,
)
from chuni_penguin.services.rating_goals import whatif_content as whatif_content
from chuni_penguin.services.recommendation_data import (
    CROSSMYTHOS_RHAPSODIA_JUMPSCARE as CROSSMYTHOS_RHAPSODIA_JUMPSCARE,
)
from chuni_penguin.services.recommendation_data import (
    CROSSMYTHOS_RHAPSODIA_SONG_ID as CROSSMYTHOS_RHAPSODIA_SONG_ID,
)
from chuni_penguin.services.recommendation_data import (
    FORSAKEN_TALE_JUMPSCARE as FORSAKEN_TALE_JUMPSCARE,
)
from chuni_penguin.services.recommendation_data import (
    FORSAKEN_TALE_SONG_ID as FORSAKEN_TALE_SONG_ID,
)
from chuni_penguin.services.recommendation_data import (
    SOUTHERN_CROSS_JUMPSCARE as SOUTHERN_CROSS_JUMPSCARE,
)
from chuni_penguin.services.recommendation_data import (
    SOUTHERN_CROSS_SONG_ID as SOUTHERN_CROSS_SONG_ID,
)
from chuni_penguin.services.recommendation_data import (
    TOA_CHAN_TOYBOX_JUMPSCARE as TOA_CHAN_TOYBOX_JUMPSCARE,
)
from chuni_penguin.services.recommendation_data import (
    TOA_CHAN_TOYBOX_SONG_ID as TOA_CHAN_TOYBOX_SONG_ID,
)
from chuni_penguin.services.recommendation_data import (
    VOLCANIC_JUMPSCARE as VOLCANIC_JUMPSCARE,
)
from chuni_penguin.services.recommendation_data import (
    VOLCANIC_SONG_ID as VOLCANIC_SONG_ID,
)
from chuni_penguin.services.recommendation_data import (
    XL_TECHNO_JUMPSCARE as XL_TECHNO_JUMPSCARE,
)
from chuni_penguin.services.recommendation_data import (
    XL_TECHNO_SONG_ID as XL_TECHNO_SONG_ID,
)

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ToolsCog(commands.Cog, name="Tools"):
    def __init__(self, bot: "ChuniBot") -> None:
        self._rng = random.Random()

    @commands.hybrid_command("roll", extras={"invoke_on_edit": False})
    @logged_prefix_command
    async def roll(
        self,
        ctx: Context,
        max: Range[int, 1] = 100,
    ):
        """Rolls a random number between 1 and the specified maximum.

        Parameters
        ----------
        max: int
            The maximum roll. Must be an integer larger than 1.
        """

        if max > 10**4000 - 1:
            msg = "Maximum roll cannot be larger than 10^4000 - 1 due to Discord character limit."
            raise commands.BadArgument(msg)

        prefix = f"{ctx.author.mention} rolled a "

        # Add 1 since randrange is max-exclusive like range()
        result = self._rng.randrange(1, max + 1)
        content = f"{prefix}{result}"

        if len(content) <= 2000:
            await ctx.reply(content=content, mention_author=False)
        elif len(content) <= 4000 + len(prefix):
            view = discord.ui.LayoutView()

            container = discord.ui.Container()
            view.add_item(container)

            if len(content) <= 4000:
                text_display = discord.ui.TextDisplay(content)
            else:
                text_display = discord.ui.TextDisplay(str(result))

            container.add_item(text_display)

            await ctx.reply(view=view, mention_author=False)
        else:
            msg = "Roll result was too large to fit in a single message. Try a smaller maximum roll."
            raise commands.CommandError(msg)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(ToolsCog(bot))
