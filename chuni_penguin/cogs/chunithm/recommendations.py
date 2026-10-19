# ruff: noqa: RUF003
import random
from typing import TYPE_CHECKING, Optional

import discord
from discord.ext import commands
from discord.ext.commands import Range

from chuni_penguin.calculation import (
    calculate_score_for_rating,
)
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    LevelRange,
    LevelRangeConverter,
)
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.repositories.recommendations import RandomChartFilter
from chuni_penguin.services.recommendation_data import (
    CROSSMYTHOS_RHAPSODIA_JUMPSCARE,
    CROSSMYTHOS_RHAPSODIA_SONG_ID,
    FORSAKEN_TALE_JUMPSCARE,
    FORSAKEN_TALE_SONG_ID,
    SOUTHERN_CROSS_JUMPSCARE,
    SOUTHERN_CROSS_SONG_ID,
    TOA_CHAN_TOYBOX_JUMPSCARE,
    TOA_CHAN_TOYBOX_SONG_ID,
    VOLCANIC_JUMPSCARE,
    VOLCANIC_SONG_ID,
    XL_TECHNO_JUMPSCARE,
    XL_TECHNO_SONG_ID,
)
from chuni_penguin.ui import ChartCardEmbed
from chuni_penguin.utils import (
    round_to_nearest,
)

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class RecommendationsCog(commands.Cog, name="Recommendations"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.catalog = bot.services.utils.recommendations
        self.database = bot.services.database
        self._rng = random.Random()

    @commands.hybrid_command("random", aliases=["rand"])
    @logged_prefix_command
    async def random(
        self, ctx: PenguinContext, level: str, count: Range[int, 1, 10] = 3
    ):
        """Get random charts based on level/course/chart constant.

        Parameters
        ----------
        level: str
            Level to search for. Can be a level (13+), a chart constant (13.5), a level
            range (14.0-14.8), or a course class (`i`, `ii`, `iii`, `iv`, `v`, `inf`,
            `random`, `wallpanic`).
        count: int
            Number of charts to return. Must be between 1 and 10. Not respected when
            rolling a random course.
        """

        course_levels: dict[str, list[str | None]] = {
            "i": ["10", "10+", "11"],
            "ii": ["11+", "12", "12+"],
            "iii": ["12+", "13", "13+"],
            "iv": ["13+", "14", "14+"],
            "sibyl": [None, None, None],
            "v": ["14", "14+", "15"],
            "inf": ["14+", "15", "15+"],
            "random": [None, None, None],
            "wallpanic": ["10+", "11", "11+"],
        }
        sibyl_songs: list[list[int]] = [
            [
                850,  # 《混乱》 ～ Muspell
                851,  # 《理想》 ～ Cloudland
                852,  # 《逃避》 ～ The Deserter
                853,  # 《最愛》 ～ Curse
                1029,  # 《狂乱》 ～ Cataclysm
                1031,  # 《信仰》 ～ Eudaimonia
                2091,  # 《紀律》 ～ As One
                2092,  # 《種子》 ～ Set You Free
                2458,  # 《楽土》 ～ One and Only One
                2459,  # 《散華》 ～ EMBARK
            ],
            [
                854,  # 《運命》 ～ Ray of Hope
                1030,  # 《投影》 ～ Oh My Baby Doll
                1032,  # 《選別》 ～ Refuge
                1033,  # 《本能》 ～ ReCoda
                2090,  # 《偏愛》 ～ Shattered Memories
                2093,  # 《自戒》 ～ Paganelope
                2457,  # 《真紅》 ～ Pavane Pour La Flamme
                2460,  # 《慈雨》 ～ La Symphonie de Salacia: Agony Movement
            ],
            [
                918,  # 《破滅》 ～ Rhapsody for The End
                2461,  # 《創造》 ～ Cries, beyond The End
            ],
        ]
        course_condition: dict[str, str] = {
            "i": "CLASS I: 50 LIFE, MISS -1, CLEAR +10",
            "ii": "CLASS II: 50 LIFE, MISS -1",
            "iii": "CLASS III: 30 LIFE, MISS -1",
            "iv": "CLASS IV: 500 LIFE, JUSTICE or lower -1",
            "sibyl": "CLASS IV - シビュラ精霊記 Random Set: 100 LIFE, ATTACK or lower -1",
            "v": "CLASS V: 300 LIFE, JUSTICE or lower -1",
            "inf": "CLASS ∞: 200 LIFE, JUSTICE or lower -1",
            "random": "CLASS EXTRA - RANDOM: 50 LIFE, MISS -1",
            "wallpanic": "CLASS EXTRA - Wall Panic!: 400 LIFE, JUSTICE or lower -1, JUSTICE CRITICAL +1, field wall gets further back as LIFE decreases",
        }

        async with ctx.typing():
            course_mode = level.lower() in course_levels or level.lower() == "infinite"
            if course_mode:
                course_class = level.lower()
                if course_class == "infinite":
                    course_class = "inf"
                content = course_condition[course_class]
                charts = await self.catalog.random_course(
                    course_class, course_levels[course_class], sibyl_songs
                )
            else:
                content = None
                levels = await LevelRangeConverter().convert(ctx, level)
                if isinstance(levels, LevelRange):
                    min_level, max_level = levels
                    filters = RandomChartFilter(
                        min_const=(min_level.const or min_level.inferred_const)
                        if min_level is not None
                        else None,
                        max_const=(max_level.const or max_level.inferred_max_const)
                        if max_level is not None
                        else None,
                    )
                elif levels.const is not None:
                    filters = RandomChartFilter(const=levels.const)
                else:
                    filters = RandomChartFilter(level=levels.level)
                charts = await self.catalog.random_charts(count, filters)

            if len(charts) == 0:
                await ctx.reply("No charts found.", mention_author=False)
                return

            master_song_ids = [
                chart.song.id for chart in charts if chart.difficulty == "MAS"
            ]

            if not course_mode:
                if XL_TECHNO_SONG_ID in master_song_ids:
                    await self.database.user_found_easter_egg(
                        ctx.author.id, "xl-techno-more-dance-remix-jumpscare"
                    )
                    await ctx.reply(XL_TECHNO_JUMPSCARE, mention_author=False)
                    return
                if VOLCANIC_SONG_ID in master_song_ids:
                    await self.database.user_found_easter_egg(
                        ctx.author.id, "volcanic-jumpscare"
                    )
                    await ctx.reply(VOLCANIC_JUMPSCARE, mention_author=False)
                    return
                if (
                    CROSSMYTHOS_RHAPSODIA_SONG_ID in master_song_ids
                    and FORSAKEN_TALE_SONG_ID in master_song_ids
                    and (
                        # since there's only 4 15.7s in the game as of current,
                        # if we do a random 15.7 then the jumpscare will always show up
                        # without this guard.
                        level != "15.7" or self._rng.random() < 0.25
                    )
                ):
                    if self._rng.random() > 0.5:
                        await self.database.user_found_easter_egg(
                            ctx.author.id, "crossmythos-rhapsodia-jumpscare"
                        )
                        jumpscare = CROSSMYTHOS_RHAPSODIA_JUMPSCARE
                    else:
                        await self.database.user_found_easter_egg(
                            ctx.author.id, "forsaken-tale-jumpscare"
                        )
                        jumpscare = FORSAKEN_TALE_JUMPSCARE

                    await ctx.reply(jumpscare, mention_author=False)
                    return
                if CROSSMYTHOS_RHAPSODIA_SONG_ID in master_song_ids and (
                    # since there's only 4 15.7s in the game as of current,
                    # if we do a random 15.7 then the jumpscare will always show up
                    # without this guard.
                    level != "15.7" or self._rng.random() < 0.25
                ):
                    await self.database.user_found_easter_egg(
                        ctx.author.id, "crossmythos-rhapsodia-jumpscare"
                    )
                    await ctx.reply(
                        CROSSMYTHOS_RHAPSODIA_JUMPSCARE, mention_author=False
                    )
                    return
                if FORSAKEN_TALE_SONG_ID in master_song_ids and (
                    # since there's only 4 15.7s in the game as of current,
                    # if we do a random 15.7 then the jumpscare will always show up
                    # without this guard.
                    level != "15.7" or self._rng.random() < 0.25
                ):
                    await self.database.user_found_easter_egg(
                        ctx.author.id, "forsaken-tale-jumpscare"
                    )
                    await ctx.reply(FORSAKEN_TALE_JUMPSCARE, mention_author=False)
                    return
                if TOA_CHAN_TOYBOX_SONG_ID in master_song_ids:
                    await self.database.user_found_easter_egg(
                        ctx.author.id, "toa-chans-toybox-jumpscare"
                    )
                    await ctx.reply(TOA_CHAN_TOYBOX_JUMPSCARE, mention_author=False)
                    return
                if SOUTHERN_CROSS_SONG_ID in master_song_ids:
                    await self.database.user_found_easter_egg(
                        ctx.author.id, "southern-cross-jumpscare"
                    )
                    await ctx.reply(SOUTHERN_CROSS_JUMPSCARE, mention_author=False)
                    return

            embeds: list[discord.Embed] = [
                ChartCardEmbed(
                    chart, synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket
                )
                for chart in charts
            ]
            await ctx.reply(content=content, embeds=embeds, mention_author=False)

    @commands.hybrid_command("recommend")
    @logged_prefix_command
    async def recommend(
        self,
        ctx: PenguinContext,
        count: Range[int, 1, 10] = 3,
        target_rating: Optional[float] = None,
    ):
        """Get random chart recommendations with target scores based on your rating.

        Please note that recommended charts are generated randomly.

        Parameters
        ----------
        count: int
            Number of charts to return. Must be between 1 and 4.
        target_rating: Optional[float]
            Your target play rating. If not provided, it will be automatically set based on your song records
            on CHUNITHM-NET or your Kamaitachi NaiveRating, assuming you're logged in.
        """

        async with ctx.typing():
            if target_rating is None:
                async with self.bot.services.accounts.network(ctx) as client:
                    target_rating = await self.catalog.cached_rating(
                        ctx.author.id, client.NAME
                    )

            if target_rating is None:
                msg = "There isn't enough data to give you recommendations. Please try using other score features, or specify a target rating."
                raise commands.CommandError(msg)

            # set minimum target rating to 1 to prevent funny things from happening
            if target_rating is None or target_rating < 1:
                target_rating = 1

            # Determine min-max const to recommend based on target rating.
            min_level = round(target_rating - 2.15, 2)

            if target_rating < 12:
                max_level = round(target_rating - 1, 2)
            else:
                max_level = round(target_rating - 1.5, 2)

            charts = await self.catalog.recommendation_candidates(
                ctx.author.id, min_level, max_level
            )

            if len(charts) == 0:
                await ctx.reply("No charts found.", mention_author=False)
                return

            embeds: list[discord.Embed] = []
            for row in charts:
                chart, pb = row._tuple()

                assert chart.const is not None

                target_score = calculate_score_for_rating(target_rating, chart.const)

                if target_score is None:
                    target_score = 1_009_000

                target_score = round_to_nearest(target_score, 50)

                if pb is not None and target_score <= pb:
                    continue

                embeds.append(
                    ChartCardEmbed(
                        chart,
                        target_score=target_score,
                        synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                    )
                )

                if len(embeds) >= count:
                    break

            if len(embeds) <= 0:
                await ctx.reply("No charts found.", mention_author=False)
                return

            await ctx.reply(embeds=embeds, mention_author=False)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(RecommendationsCog(bot))
