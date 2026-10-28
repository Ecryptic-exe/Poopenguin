"""Account-independent CHUNITHM calculators, reusing A's formulas and outputs."""

import itertools
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, Range
from discord.utils import escape_markdown

from chuni_penguin.calculation import (
    calculate_border,
    calculate_overpower_base,
    calculate_overpower_max,
    calculate_play_overpower,
    calculate_rating,
    calculate_score_deduction_per_judgement,
    calculate_score_for_rating,
)
from chuni_penguin.config import config
from chuni_penguin.constants import MAX_DIFFICULTY
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import AliasNameConverter, DifficultyConverter
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.types import ComboLamp, Rank
from chuni_penguin.ui import ChartCardEmbed
from chuni_penguin.ui.autocomplete import song_title_choices
from chuni_penguin.ui.border import BorderView
from chuni_penguin.utils import floor_to_ndp
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, ui_text

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class CalculatorsCog(commands.Cog, name="Calculators"):
    @commands.hybrid_command("anmitsu", aliases=["rub"])
    @logged_prefix_command
    async def anmitsu(
        self,
        ctx: Context,
        bpm: Range[float, 1, 10000],
        note_density: Range[int, 1, 1024] = 16,
    ):
        """Determine whether you can get JUSTICE / JUSTICE CRITICAL through "anmitsu" technique.

        "Anmitsu" is a technique where when two notes appear on different lanes with slightly different timing, you hit both
        of them at the same time. When the two notes are close enough in timing, they will have a JUSTICE CRITICAL overlap
        duration where you can tap both notes at the exact same time and get JUSTICE CRITICAL for both. The closer the notes,
        the easier it is to perform the "anmitsu" technique. It is said that "anmitsu" technique is ideal when the distance
        between two notes is below 50ms (i.e. the JUSTICE CRITICAL overlap duration is at least 16.67ms).

        Rubbing the ground slider is a technique where when two tap notes appear in the same lane, you rub the ground slider
        instead of tapping the notes individually. If the two notes are close enough in timing, you will not get JUSTICE or
        ATTACK.

        Parameters
        ----------
        bpm: float
            BPM of the song. Use the `info` command to find this.
        note_density: int
            Note value, for example 16 means 1/16 notes (16 notes = 1 measure).
        """

        note_distance_1000 = int(240000 * 1000 / bpm / note_density)
        crit_overlap_1000 = max(66667 - note_distance_1000, 0)
        jus_overlap_1000 = max(133333 - note_distance_1000, 0)
        res = ui_text(
            ctx,
            "At **{0}** BPM, the distance between two **1/{1}** notes is `{2}ms`.",
            bpm,
            note_density,
            note_distance_1000 // 100 / 10,
        )
        res += ui_text(
            ctx,
            "\n- The JUSTICE CRITICAL overlap duration is `{0}ms`",
            crit_overlap_1000 // 100 / 10,
        )
        res += ui_text(
            ctx,
            "\n- The JUSTICE overlap duration is `{0}ms`",
            jus_overlap_1000 // 100 / 10,
        )

        if crit_overlap_1000 > 0:
            res += ui_text(
                ctx,
                "\n\n:white_check_mark: If these notes appear vertically, you can rub the ground slider and will not get JUSTICE and below.",
            )
        elif jus_overlap_1000 > 0:
            res += ui_text(
                ctx,
                "\n\n:warning: If these notes appear vertically and you rub the ground slider, you will not get ATTACK but you might get JUSTICE.",
            )
        else:
            res += ui_text(
                ctx,
                "\n\n:x: If these notes appear vertically, you might get ATTACK if you rub the ground slider.",
            )

        if crit_overlap_1000 > 16667:
            res += ui_text(
                ctx,
                '\n:white_check_mark: If these notes appear in different lanes, you can tap both of them at the same time ("anmitsu" technique) and get JUSTICE CRITICAL for both notes during the JUSTICE CRITICAL overlap duration.',
            )
        elif jus_overlap_1000 > 16667:
            res += ui_text(
                ctx,
                "\n:warning: If these notes appear in different lanes and you tap both of them at the same time, you are very likely to get JUSTICE or ATTACK therefore it is not recommended.",
            )
        else:
            res += ui_text(
                ctx,
                "\n:x: If these notes appear in different lanes, you should tap them individually since the notes are too far from each other.",
            )

        await ctx.reply(res, mention_author=False)

    @commands.hybrid_command("calculate", aliases=["calc"])
    @logged_prefix_command
    async def calculate(
        self,
        ctx: Context,
        score: Range[int, 0, 1010000],
        chart_constant: Optional[float] = None,
    ):
        """Calculate rating and over power from score and chart constant.

        Parameters
        ----------
        score: int
            The score to calculate play rating and over power from
        chart_constant: float
            Chart constant of the chart. Use the `info` command to find this.
        """

        if chart_constant is None and score < 900000:
            res = ui_text(
                ctx,
                "Rating calculation for scores below 900,000 is dependent on chart constant.",
            )
            res += ui_text(
                ctx, "\nPlease specify chart constant to view detailed calculations."
            )
            # (You really should just git gud though)
            await ctx.reply(res, mention_author=False)
            return

        if chart_constant is not None and (
            chart_constant < 1 or chart_constant > MAX_DIFFICULTY
        ):
            msg = ui_text(
                ctx, "Chart constant must be between 1 and {0}.", MAX_DIFFICULTY
            )
            raise commands.BadArgument(msg)

        if chart_constant is None:
            rating = calculate_rating(score, 0)
            const_text = ""
        else:
            rating = calculate_rating(score, chart_constant)
            const_text = ui_text(
                ctx, " on a chart with chart constant **{0}**", chart_constant
            )

        sign = ""
        if chart_constant is None and rating > 0:
            sign = "+"

        res = ui_text(ctx, "A score of **{0}**{1} will give:", score, const_text)
        res += f"\n- Rating: **{sign}{floor_to_ndp(rating, 2)}**"

        if chart_constant is not None:
            overpower_max = calculate_overpower_max(chart_constant)
            overpower_max_floored = floor_to_ndp(overpower_max, 2)

            if score == 1010000:
                res += f"\n- OVER POWER: **{overpower_max_floored} / {overpower_max_floored} (100.00%)**"
            elif score < 500000:
                res += f"\n- OVER POWER: **0.00 / {overpower_max_floored} (0.00%)**"
            else:
                overpower_base = calculate_overpower_base(score, chart_constant)

                res += "\n- OVER POWER:"

                if score >= 1000000:
                    overpower_ap = calculate_play_overpower(
                        overpower_base, ComboLamp.all_justice
                    )
                    overpower_ap_percentage = floor_to_ndp(
                        overpower_ap / overpower_max * 100, 2
                    )
                    res += f"\n  - AJ: **{overpower_ap} / {overpower_max_floored} ({overpower_ap_percentage}%)**"

                overpower_fc = calculate_play_overpower(
                    overpower_base, ComboLamp.full_combo
                )
                overpower_fc_percentage = floor_to_ndp(
                    overpower_fc / overpower_max * 100, 2
                )
                overpower_base_percentage = floor_to_ndp(
                    overpower_base / overpower_max * 100, 2
                )

                res += f"\n  - FC: **{overpower_fc} / {overpower_max_floored} ({overpower_fc_percentage}%)**"
                res += ui_text(
                    ctx,
                    "\n  - Non-FC: **{0} / {1} ({2}%)**",
                    overpower_base,
                    overpower_max_floored,
                    overpower_base_percentage,
                )

        await ctx.reply(res, mention_author=False)

    @commands.hybrid_command("const", aliases=["constant"])
    @logged_prefix_command
    async def const(
        self,
        ctx: Context,
        chart_constant: Range[float, 1.0, MAX_DIFFICULTY],
        mode: Literal["default", "aj"] = "default",
    ):
        """Calculate rating and over power achieved with various scores based on chart constant.

        Parameters
        ----------
        chart_constant: float
            Chart constant of the chart. Use the `info` command to find this.
        mode: str
            Sets the display mode: `default` (Display rating information only) / `aj` (Display OP information for ALL JUSTICE only)
        """

        chart_constant = round(chart_constant, 2)
        res = ui_text(ctx, "Calculation for chart constant **{0}**:", chart_constant)
        if mode == "aj":
            separator = "-------------------------"
            res += ui_text(ctx, "```  Score |         OP (AJ)\n{0}", separator)
            scores = [1009950]
            scores.extend(
                itertools.chain(
                    range(1009900, 1009450, -50),  # 1009500..=1009900
                    range(1009400, 1008900, -100),  # 1009000..=1009400
                )
            )
        else:
            separator = "---------------"
            res += ui_text(ctx, "```  Score |  Rate\n{0}", separator)
            scores = [1009000]
            scores.extend(
                itertools.chain(
                    range(1008500, 1004500, -500),  # 1005000..=1008500
                    range(1004000, 999000, -1000),  # 1000000..=1004000
                    range(997500, 972500, -2500),  # 975000..=997500
                    range(970000, 940000, -10000),  # 950000..=970000
                    range(925000, 875000, -25000),  # 900000..=925000
                )
            )
        overpower_max = calculate_overpower_max(chart_constant)
        if mode == "aj":
            rating = calculate_rating(1010000, chart_constant)
            res += f"\n1010000 | {overpower_max:>5.2f} = 100.00%"

        for score in scores:
            rating = calculate_rating(score, chart_constant)
            overpower_base = calculate_overpower_base(score, chart_constant)
            if score >= Rank.ss.min_score:
                overpower = overpower_base + Decimal(1)
                overpower_aj = f"{floor_to_ndp(overpower / overpower_max * 100, 2)}%"
            else:
                overpower_aj = "     -"

            if rating > 0:
                res += "\n"
                if mode == "aj":
                    # AJ means scores are above 1m => overpower is defined
                    res += f"{score:>7} | {overpower:>5.2f} = {overpower_aj:>7}"  # type: ignore[reportUnboundVariable]
                else:
                    res += f"{score:>7} | {floor_to_ndp(rating, 2):>5.2f}"
                    if (
                        score == Rank.sss.min_score
                        or score == Rank.ssp.min_score
                        or score == Rank.ss.min_score
                        or score == Rank.sp.min_score
                        or score == Rank.s.min_score
                    ):
                        res += f"\n{separator}"

        res += "```"

        await ctx.reply(res, mention_author=False)

    @commands.hybrid_command("rating")
    @logged_prefix_command
    async def rating(
        self, ctx: Context, rating: Range[float, 1.0, round(MAX_DIFFICULTY + 2.15, 2)]
    ):
        """Calculate score required to achieve the specified play rating.

        Parameters
        ----------
        rating: float
            Play rating you want to achieve
        """

        rating = round(rating, 2)
        res = ui_text(ctx, "Score required to achieve **{0:.2f}** play rating:", rating)
        res += ui_text(ctx, "\n```Const |   Score\n---------------")

        chart_constant_10 = int(rating - 3) * 10
        rating_10 = rating * 10
        max_10 = MAX_DIFFICULTY * 10

        if chart_constant_10 < 10:
            chart_constant_10 = 10
        while chart_constant_10 <= rating_10 and chart_constant_10 <= max_10:
            required_score = calculate_score_for_rating(
                round(rating_10 / 10, 2), round(chart_constant_10 / 10, 1)
            )

            if required_score is not None and required_score >= Rank.s.min_score:
                res += f"\n {chart_constant_10 / 10:>4.1f} | {int(required_score):>7}"
            if chart_constant_10 >= 100:
                chart_constant_10 += 1
            elif chart_constant_10 >= 70:
                chart_constant_10 += 5
            else:
                chart_constant_10 += 10
        res += "```"

        await ctx.reply(res, mention_author=False)

    async def song_title_autocomplete(
        self, interaction: "discord.Interaction[ChuniBot]", current: str
    ) -> list[app_commands.Choice[str]]:
        service = (
            interaction.client.services.autocomplete
            if interaction.client.services.available("catalog")
            else None
        )
        return song_title_choices(service, current, interaction.guild_id)

    @commands.hybrid_command("border")
    @app_commands.autocomplete(query=song_title_autocomplete)
    @logged_prefix_command
    async def border(
        self,
        ctx: PenguinContext,
        difficulty_or_notecount: str,
        *,
        query: Annotated[str | None, AliasNameConverter(lower=True)] = None,
    ):
        """Display the number of permissible JUSTICE, ATTACK and MISS to achieve specific ranks on a chart.

        The values are based on realistic JUSTICE:ATTACK:MISS ratios and are for reference only.
        In terms of scoring, the score decrease from 1 ATTACK is equivalent to 51 JUSTICE, and 1 MISS is equivalent to 101 JUSTICE.

        Parameters
        ----------
        difficulty_or_notecount: str | int
            Chart difficulty to search for (BAS/ADV/EXP/MAS/ULT/WE). Alternatively, enter a notecount here to get the border for that specific notecount.
        query: str
            Song title to search for. You don't have to be exact; try things out!
        """

        async with ctx.typing():
            if difficulty_or_notecount.isnumeric():
                notecount = int(difficulty_or_notecount)

                if notecount <= 0:
                    msg = ui_text(ctx, "Notecount should be larger than 0.")
                    raise commands.BadArgument(msg)

                if (
                    notecount
                    > 99_999_999_999_999_999_999_999_999_999_999_999_999_999_999_999
                ):
                    msg = ui_text(ctx, "Notecount is too large.")
                    raise commands.BadArgument(msg)

                deductions = calculate_score_deduction_per_judgement(notecount)

                embed = discord.Embed(
                    color=discord.Color.yellow(),
                    title=ui_text(ctx, "Rank borders and deductions"),
                )
                embed.add_field(
                    name=ui_text(ctx, "Note Count"), value=str(notecount), inline=False
                )

                borders = calculate_border(notecount)
                borders_field_value = ""

                for rank, judgements in borders.items():
                    borders_field_value += f"▸ {config.icons.rank_icon(rank)} ▸ {judgements.justice}-{judgements.attack}-{judgements.miss}\n"

                embed.add_field(
                    name=ui_text(ctx, "Borders (JUSTICE-ATTACK-MISS)"),
                    value=borders_field_value.strip(),
                )

                deductions = calculate_score_deduction_per_judgement(notecount)
                embed.add_field(
                    name=ui_text(ctx, "Score Deduction"),
                    value=(
                        f"▸ JUSTICE: -{deductions['justice']:.2f}\n"
                        f"▸ ATTACK: -{deductions['attack']:.2f}\n"
                        f"▸ MISS: -{deductions['miss']:.2f}\n"
                    ),
                )

                await BorderView(ctx, embed, notecount).start()
            else:
                if query is None:
                    raise commands.MissingRequiredArgument(ctx.command.params["query"])  # pyright: ignore[reportOptionalMemberAccess]

                difficulty = await DifficultyConverter().convert(
                    ctx, difficulty_or_notecount
                )
                chart = await ctx.find_chart(
                    difficulty,
                    query,
                    ui_text(ctx, "Select a chart to view rank borders for:"),
                )

                if chart is None:
                    return

                if chart.maxcombo is None:
                    song = chart.song
                    msg = f"We currently don't have note counts for {escape_markdown(song.title)} [{chart.difficulty}]. Try using `{ctx.clean_prefix}border <notecount>` instead."

                    raise commands.CommandError(msg)

                embed = ChartCardEmbed(
                    chart,
                    border=True,
                    synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                    language=get_chunithm_language(ctx),
                )
                await BorderView(ctx, embed, chart.maxcombo).start()


async def setup(bot):
    await bot.add_cog(CalculatorsCog())
