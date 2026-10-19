import decimal
import itertools
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from chuni_penguin import flags
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    DecimalConverter,
    DecimalTransformer,
)
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.services.rating_goals import rating_reach_content, whatif_content
from chuni_penguin.types import (
    RatingFrameType,
    RatingType,
)
from chuni_penguin.utils import (
    floor_to_ndp,
)

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class RatingGoalsCog(commands.Cog, name="RatingGoals"):
    def __init__(self, bot: "ChuniBot"):
        self.accounts = bot.services.accounts

    @flags.command("whatif")
    @flags.argument("play_rating", type=Decimal)
    @flags.argument("current_play_rating", nargs="?", default=None, type=Decimal)
    @flags.argument(
        "-r",
        "--rating-system",
        choices=["naive", "ingame"],
        default=None,
        required=False,
    )
    @flags.argument("-k", "--kamaitachi", dest="kamaitachi", action="store_true")
    @logged_prefix_command
    async def whatif(
        self,
        ctx: PenguinContext,
        *,
        play_rating: Decimal,
        current_play_rating: Decimal | None = None,
        rating_system: Literal["naive", "ingame"] | None = None,
        kamaitachi: bool = False,
    ):
        """What if you get a new play with a certain play rating?

        *Parameters*
        `play_rating`: The play rating you would achieve.
        `current_play_rating`: The current play rating of the chart if it is already in your best 50 scores. Leave blank if the chart is currently not included in your best 50 scores.
        `-r`, `--rating-system`: Choose between `ingame` (best30 + new20) or `naive` (best50) (default: depends on network).
        `-k`, `--kamaitachi`: Use your Kamaitachi account instead of your CHUNITHM International account, if both are linked.
        """

        if rating_system == "naive":
            rating_type = RatingType.naive
        elif rating_system == "ingame":
            rating_type = RatingType.in_game
        else:
            rating_type = None

        await self._whatif_impl(
            ctx, play_rating, current_play_rating, rating_type, kamaitachi=kamaitachi
        )

    @app_commands.command(
        name="whatif",
        description="What if you get a new play with a certain play rating?",
    )
    @app_commands.rename(
        play_rating="play-rating", current_play_rating="current-play-rating"
    )
    @app_commands.describe(
        play_rating="The play rating you would achieve.",
        current_play_rating="The current play rating of the chart if it is already in your best 50 scores.",
        rating_system="The rating system to calculate the rating gain for.",
        kamaitachi="Use your Kamaitachi account.",
    )
    @app_commands.choices(
        rating_system=[
            app_commands.Choice(
                name="In-game (Best 30 + New 20)", value=RatingType.in_game.value
            ),
            app_commands.Choice(name="Naive (Best 50)", value=RatingType.naive.value),
        ]
    )
    @logged_app_command
    async def whatif_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        play_rating: app_commands.Transform[Decimal, DecimalTransformer],
        current_play_rating: app_commands.Transform[Decimal | None, DecimalTransformer],
        rating_system: RatingType | None = None,
        *,
        kamaitachi: bool = False,
    ):
        await self._whatif_impl(
            await PenguinContext.from_interaction(interaction),
            play_rating,
            current_play_rating,
            rating_system,
            kamaitachi=kamaitachi,
        )

    async def _whatif_impl(
        self,
        ctx: PenguinContext,
        play_rating: Decimal,
        current_play_rating: Decimal | None,
        rating_type: RatingType | None,
        *,
        kamaitachi: bool = False,
    ):
        play_rating = floor_to_ndp(play_rating, 2)

        if current_play_rating is not None:
            current_play_rating = floor_to_ndp(current_play_rating, 2)

            if play_rating < current_play_rating:
                # swap the input parameters because we're nice
                play_rating, current_play_rating = current_play_rating, play_rating
            elif play_rating == current_play_rating:
                await ctx.reply(
                    "That wouldn't give you any rating increase! What are you expecting?",
                    mention_author=False,
                )
                return

        async with (
            ctx.typing(),
            self.accounts.network(ctx, kamaitachi=kamaitachi) as client,
        ):
            if rating_type is None:
                rating_type = client.DEFAULT_RATING_SYSTEM

            breakdown = await client.get_rating_breakdown(rating_type)

        if rating_type == RatingType.in_game:
            best30 = breakdown.frames[RatingFrameType.best]
            new20 = breakdown.frames[RatingFrameType.new]
            total_scores = best30.num_scores + new20.num_scores
            raw_rating = floor_to_ndp(
                sum(
                    (
                        entry.rating or Decimal(0)
                        for entry in itertools.chain(best30.scores, new20.scores)
                    ),
                    Decimal(0),
                )
                / total_scores,
                4,
            )
        elif rating_type == RatingType.naive:
            best50 = breakdown.frames[RatingFrameType.best]
            raw_rating = floor_to_ndp(
                sum(
                    (entry.rating or Decimal(0) for entry in best50.scores),
                    Decimal(0),
                )
                / best50.num_scores,
                4,
            )
        else:
            msg = f"Unsupported rating system {client.DEFAULT_RATING_SYSTEM}."
            raise commands.CommandError(msg)

        if current_play_rating is not None:
            rating_increase = (Decimal(play_rating) - Decimal(current_play_rating)) / 50
            await ctx.respond_or_edit(
                content=(
                    f"Replacing a **{current_play_rating:.2f}** rating play with a **{play_rating:.2f}** rating play in your best 50 would give:\n"
                    f"- {rating_type}: **+{rating_increase:.4f}** ({raw_rating:.4f} → {raw_rating + rating_increase:.4f})"
                )
            )
            return

        if rating_type == RatingType.in_game:
            best30 = breakdown.frames[RatingFrameType.best]
            new20 = breakdown.frames[RatingFrameType.new]

            await ctx.respond_or_edit(
                content=(
                    f"Getting a **{play_rating:.2f}** play for a chart currently not in your best50 would give:\n"
                    f"- {rating_type}: {whatif_content(best30, raw_rating, play_rating, ' if this is an old chart')}\n"
                    f"- {rating_type}: {whatif_content(new20, raw_rating, play_rating, ' if this is a new chart')}"
                )
            )

        elif rating_type == RatingType.naive:
            best50 = breakdown.frames[RatingFrameType.best]

            await ctx.respond_or_edit(
                content=(
                    f"Getting a **{play_rating:.2f}** play for a chart currently not in your best50 would give:\n"
                    f"- {rating_type}: {whatif_content(best50, raw_rating, play_rating)}"
                )
            )
        else:
            msg = f"Unreachable branch {current_play_rating=!r} {rating_type=!r}"
            raise commands.CommandError(msg)

    @flags.command("reach")
    @flags.argument("target")
    @flags.argument(
        "-e", "--each", dest="each", type=DecimalConverter, default=None, required=False
    )
    @flags.argument(
        "-c", "--count", dest="count", type=int, default=None, required=False
    )
    @flags.argument(
        "-r",
        "--rating-system",
        choices=["naive", "ingame"],
        default=None,
        required=False,
    )
    @flags.argument("-k", "--kamaitachi", dest="kamaitachi", action="store_true")
    @logged_prefix_command
    async def reach_prefix(
        self,
        ctx: PenguinContext,
        *,
        target: str,
        each: Decimal | None = None,
        count: int | None = None,
        rating_system: Literal["naive", "ingame"] | None = None,
        kamaitachi: bool = False,
    ):
        """Calculate how many scores of what rating is needed to reach the given rating.

        **Parameters**
        `target`: The target rating to achieve, will be floored to 2 decimal points. Alternatively, prefix the value with a `+` to interpret as a delta relative to your current rating.
        `-e`, `--each`: Fill your best30/new20/best50 with scores of this much rating (floored to 2 decimal points) until the target rating is acheived. (default: None)
        `-c`, `--count`: Specify a number of scores to set to reach the target rating (default: 1).
        `-r`, `--rating-system`: Choose between `ingame` (best30 + new20) or `naive` (best50) (default: depends on network).
        `-k`, `--kamaitachi`: Use your Kamaitachi account instead of your CHUNITHM International account, if both are linked.
        """

        if rating_system == "naive":
            rating_type = RatingType.naive
        elif rating_system == "ingame":
            rating_type = RatingType.in_game
        else:
            rating_type = None

        if count is not None and count <= 0:
            msg = "number of charts must be at least 1"
            raise commands.BadArgument(msg)

        await self._reach_impl(
            ctx, target, each, count, rating_type, kamaitachi=kamaitachi
        )

    @app_commands.command(
        name="reach",
        description="Calculate how many scores of what rating is needed to reach the given rating.",
    )
    @app_commands.rename(rating_system="rating-system")
    @app_commands.describe(
        target="Target rating to achieve, or a delta from your current rating (e.g. +0.01).",
        each="Fill your best30/new20/best50 with scores of this much rating until the target rating is acheived.",
        count="Specify a number of scores to set to reach the target rating.",
        rating_system="The rating system to calculate for.",
        kamaitachi="Use your Kamaitachi account.",
    )
    @app_commands.choices(
        rating_system=[
            app_commands.Choice(
                name="In-game (Best 30 + New 20)", value=RatingType.in_game.value
            ),
            app_commands.Choice(name="Naive (Best 50)", value=RatingType.naive.value),
        ]
    )
    @logged_app_command
    async def reach_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        target: str,
        each: app_commands.Transform[Decimal | None, DecimalTransformer] = None,
        count: app_commands.Range[int, 1] | None = None,
        rating_system: RatingType | None = None,
        *,
        kamaitachi: bool = False,
    ):
        await self._reach_impl(
            await PenguinContext.from_interaction(interaction),
            target,
            each,
            count,
            rating_system,
            kamaitachi=kamaitachi,
        )

    async def _reach_impl(
        self,
        ctx: PenguinContext,
        target: str,
        each: Decimal | None,
        count: int | None,
        rating_type: RatingType | None,
        *,
        kamaitachi: bool,
    ):
        if target.startswith("-"):
            msg = "We don't have recent rating anymore, you can't lose rating until the version changes!"
            raise commands.BadArgument(msg)

        if each is not None and count is not None:
            msg = 'You can only specify one of "each" or "count".'
            raise commands.BadArgument(msg)

        try:
            target_value = floor_to_ndp(Decimal(target), 2)
        except decimal.InvalidOperation:
            msg = f'"{escape_markdown(target)}" is not a valid number.'
            raise commands.BadArgument(msg) from None

        if each is not None:
            each = floor_to_ndp(each, 2)

        async with (
            ctx.typing(),
            self.accounts.network(ctx, kamaitachi=kamaitachi) as client,
        ):
            if rating_type is None:
                rating_type = client.DEFAULT_RATING_SYSTEM

            rating_breakdown = await client.get_rating_breakdown(rating_type)

        if target.startswith("+"):
            target_rating = rating_breakdown.rating + target_value
        else:
            target_rating = target_value

        if rating_breakdown.rating >= target_rating:
            await ctx.respond_or_edit(
                f"You have {rating_breakdown.rating:.2f} {rating_type} which is already more than {target_rating}."
            )
            return

        if rating_type == RatingType.in_game:
            best30 = rating_breakdown.frames[RatingFrameType.best]
            new20 = rating_breakdown.frames[RatingFrameType.new]
            total_scores = best30.num_scores + new20.num_scores
            raw_rating = floor_to_ndp(
                sum(
                    (
                        entry.rating or Decimal(0)
                        for entry in itertools.chain(best30.scores, new20.scores)
                    ),
                    Decimal(0),
                )
                / total_scores,
                4,
            )
            await ctx.respond_or_edit(
                (
                    f"- Best 30: {rating_reach_content(rating_type, target_rating, raw_rating, total_scores, best30, each, count)}\n"
                    f"- New 20: {rating_reach_content(rating_type, target_rating, raw_rating, total_scores, new20, each, count)}\n"
                )
            )
        elif rating_type == RatingType.naive:
            best50 = rating_breakdown.frames[RatingFrameType.best]
            raw_rating = floor_to_ndp(
                sum((entry.rating or Decimal(0) for entry in best50.scores), Decimal(0))
                / best50.num_scores,
                4,
            )

            await ctx.respond_or_edit(
                rating_reach_content(
                    rating_type,
                    target_rating,
                    raw_rating,
                    best50.num_scores,
                    best50,
                    each,
                    count,
                )
            )
        else:
            msg = f"Unsupported rating system {client.DEFAULT_RATING_SYSTEM}."
            raise commands.CommandError(msg)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(RatingGoalsCog(bot))
