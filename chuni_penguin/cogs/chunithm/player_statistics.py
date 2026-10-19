import statistics
from collections import Counter
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from chuni_penguin import flags
from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.errors import NetworkError
from chuni_penguin.adapters.kamaitachi import KamaitachiAdapter
from chuni_penguin.calculation.overpower import (
    calculate_overpower_base,
    calculate_overpower_max,
    calculate_play_overpower,
)
from chuni_penguin.constants import (
    ChunithmVersion,
)
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    DifficultyConverter,
    GenreConverter,
    Level,
    LevelRange,
    LevelRangeConverter,
    MemberOrUserConverter,
    VersionConverter,
)
from chuni_penguin.database import Chart
from chuni_penguin.database import PersonalBest as DBPersonalBest
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.types import (
    ClearLamp,
    ComboLamp,
    Difficulty,
    Genre,
    Possession,
    Rank,
)
from chuni_penguin.ui import (
    ScoreCardEmbed,
)
from chuni_penguin.utils import floor_to_ndp
from chuni_penguin.utils.formatting import bold, bold_if

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class PlayerStatisticsCog(commands.Cog, name="PlayerStatistics"):
    def __init__(self, bot: "ChuniBot"):
        self.settings = bot.settings
        self.accounts = bot.services.accounts
        self.scores = bot.services.utils.scores
        self.folder_statistics = bot.services.utils.folder_statistics

    @flags.command("statistics", aliases=["stats", "folder", "progress"])
    @flags.argument("-d", "--difficulty", required=False)
    @flags.argument("-g", "--genre", required=False, type=GenreConverter)
    @flags.argument("-v", "--version", required=False, type=VersionConverter)
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument("-o", "--omnimix", action="store_true")
    @flags.argument(
        "user", nargs=flags.OPTIONAL_INVISIBLE, default=None, type=MemberOrUserConverter
    )
    @flags.argument("level", nargs="?", default=None, type=LevelRangeConverter)
    @logged_prefix_command
    async def statistics(
        self,
        ctx: PenguinContext,
        *,
        user: discord.User | discord.Member | None = None,
        level: Level | LevelRange | None = None,
        difficulty: str | None = None,
        genre: Genre | None = None,
        version: ChunithmVersion | None = None,
        kamaitachi: bool = False,
        omnimix: bool = False,
    ):
        """View aggregated statistics on a set of charts.

        If only a version is specified, also shows progress towards achieving SPIRIT/TRIBUTE/LEGEND titles.

        **Parameters**:
        `user`: Discord username of the player. Yourself, if not provided.
        `level`: Level (from 1 to 15+) to search for. Can also be a level range (e.g. 14.3-14.5).
        `-d`: Difficulty to search for. Must be one of `BASIC`, `ADVANCED`, `EXPERT`, `MASTER`, `ULTIMA`, or `WE` if specified.
        `-g`: Genre to search for.
        `-v`: Version to search for.
        `-k`: Get scores from Kamaitachi, if the target user has a linked account.
        `-o`: Count removed songs towards statistics and the final count.
        """

        conv_diff: Difficulty | Literal["MASTER+ULTIMA"] | None = None

        if difficulty is not None:
            if difficulty.lower() in (
                "master+ultima",
                "mas+ult",
                "mst+ult",
                "masult",
                "mstult",
            ):
                conv_diff = "MASTER+ULTIMA"
            else:
                conv_diff = await DifficultyConverter().convert(ctx, difficulty)

        await self._statistics_impl(
            ctx,
            user=user,
            level=level,
            difficulty=conv_diff,
            genre=genre,
            version=version,
            kamaitachi=kamaitachi,
            omnimix=omnimix,
        )

    @app_commands.command(
        name="statistics", description="View aggregated statistics on a set of charts."
    )
    @app_commands.describe(
        user="The player. Yourself, if not provided.",
        level="Level (from 1 to 15+) to search for. Can also be a level range (e.g. 14.3-14.5).",
        difficulty="Difficulty to search for.",
        genre="Genre to search for.",
        version="Version to search for.",
        kamaitachi="Get scores from Kamaitachi, if the target user has a linked account.",
        omnimix="Count removed songs towards statistics and the final count.",
    )
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name=str(x), value=str(x))
            for x in Difficulty.__members__.values()
        ]
        + [app_commands.Choice(name="MASTER+ULTIMA", value="MASTER+ULTIMA")],  # type: ignore[reportGeneralTypeIssues]
        genre=[
            app_commands.Choice(name=str(x), value=x.value)
            for x in Genre.__members__.values()
        ],  # type: ignore[reportGeneralTypeIssues]
        version=[
            app_commands.Choice(name=x, value=x) for x in ChunithmVersion.__args__
        ],
    )
    @logged_app_command
    async def statistics_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        *,
        user: discord.User | discord.Member | None = None,
        level: str | None = None,
        difficulty: str | None = None,
        genre: Genre | None = None,
        version: ChunithmVersion | None = None,
        kamaitachi: bool = False,
        omnimix: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)
        converted_level = (
            await LevelRangeConverter().convert(ctx, level)
            if level is not None
            else None
        )

        await self._statistics_impl(
            await PenguinContext.from_interaction(interaction),
            user=user,
            level=converted_level,
            difficulty=(
                await DifficultyConverter().convert(ctx, difficulty)
                if difficulty is not None and difficulty != "MASTER+ULTIMA"
                else difficulty
            ),
            genre=genre,
            version=version,
            kamaitachi=kamaitachi,
            omnimix=omnimix,
        )

    async def _statistics_impl(
        self,
        ctx: PenguinContext,
        *,
        user: discord.User | discord.Member | None = None,
        level: Level | LevelRange | None = None,
        difficulty: Difficulty | Literal["MASTER+ULTIMA"] | None = None,
        genre: Genre | None = None,
        version: str | None = None,
        kamaitachi: bool = False,
        omnimix: bool = False,
    ):
        target_id = ctx.author.id if user is None else user.id

        async with ctx.typing():
            try:
                async with self.accounts.network(
                    ctx, target_id, kamaitachi=kamaitachi
                ) as client:
                    profile = await client.get_minimal_profile()
                    username = profile.username
                    client_name = client.NAME
            except (NetworkError, commands.CommandError):
                username = ctx.author.display_name
                client_name = (
                    KamaitachiAdapter.NAME if kamaitachi else ChunithmNetAdapter.NAME
                )

            if omnimix and client_name == ChunithmNetAdapter.NAME:
                msg = f"{client_name} does not support omnimix songs."
                raise commands.CommandError(msg)

            pbs, charts = await self.folder_statistics.load_folder(
                target_id,
                client_name,
                level=level,
                difficulty=difficulty,
                genre=genre,
                version=version,
                omnimix=omnimix,
            )

            chart_count = len(charts)

            if chart_count <= 0:
                await ctx.respond_or_edit("No charts found for the given parameters.")
                return

            pb_count = len(pbs)
            percentage_played = pb_count * 10000 // chart_count / 100
            counts: Counter[Any] = Counter([chart.difficulty for chart in charts])

            for pb in pbs:
                pb_rank = Rank.from_score(pb.score)
                pb_combo_lamp = ComboLamp(pb.combo_lamp)
                pb_clear_lamp = ClearLamp(pb.clear_lamp)

                # Checking for AJ is probably unnecessary since currently 1009900
                # guarantees an AJ... until a chart with 5100+ notes is added
                # 2026-04-23: well fuck me they actually did that
                if pb.score >= 1009900 and pb_combo_lamp in (
                    ComboLamp.all_justice,
                    ComboLamp.all_justice_critical,
                ):
                    counts["99AJ"] += 1

                for rank in (Rank.s, Rank.sp, Rank.ss, Rank.ssp, Rank.sss, Rank.sssp):
                    if pb_rank.value >= rank.value:
                        counts[rank] += 1

                        if rank.value >= Rank.s.value:
                            counts[f"{pb.difficulty}_{rank}"] += 1

                for combo_lamp in ComboLamp:
                    if combo_lamp == ComboLamp.none:
                        continue

                    if pb_combo_lamp.value >= combo_lamp.value:
                        counts[combo_lamp] += 1

                        if combo_lamp == ComboLamp.all_justice:
                            counts[f"{pb.difficulty}_{combo_lamp}"] += 1

                for clear_lamp in ClearLamp:
                    if clear_lamp == ClearLamp.failed:
                        continue

                    if pb_clear_lamp.value >= clear_lamp.value:
                        counts[clear_lamp] += 1

            embed = discord.Embed(
                color=discord.Color.yellow(),
                title=f"{escape_markdown(username)}'s folder statistics",
                timestamp=max(
                    (pb.last_played_at for pb in pbs if pb.last_played_at is not None),
                    default=None,
                ),
            )
            embed.set_footer(
                text=f"Use `{ctx.clean_prefix}sync` if statistics seem wrong."
            )

            option_parts: list[str] = []
            description_parts: list[str] = []

            if level is not None:
                option_parts.append(f"Level {level}")

            if difficulty is not None:
                option_parts.append(str(difficulty))

            if genre is not None:
                option_parts.append(str(genre))

            if version is not None:
                option_parts.append(version)

            if omnimix:
                option_parts.append("Omnimix")

            description_parts.append(", ".join(option_parts))
            description_parts.append(
                f"\n▸ **Played**: {bold_if(len(pbs) == chart_count, f'{len(pbs)} / {chart_count} ({percentage_played:.2f}%)')}\n"
            )

            if any(chart.const is not None for chart in charts):
                charts_by_id_difficulty: dict[tuple[int, str], Chart] = {}

                for chart in charts:
                    if chart.const is None:
                        continue

                    charts_by_id_difficulty[(chart.song_id, chart.difficulty)] = chart

                op = Decimal(0)
                max_op = Decimal(0)
                max_played_op = Decimal(0)

                # if a level filter is active, simply calculate the total OP of all
                # charts in the level folder to match game behavior
                # selecting only top diffs will exclude OP from e.g. dreadnought mas (14+),
                # since ult is also a 14+
                if level is not None:
                    played_charts: set[tuple[int, str]] = set()

                    for pb in pbs:
                        played_charts.add((pb.song_id, pb.difficulty))
                        chart = charts_by_id_difficulty.get((pb.song_id, pb.difficulty))

                        if chart is None or chart.const is None:
                            continue

                        op += (
                            Decimal(pb.overpower) / 1000
                            if pb.overpower is not None
                            else calculate_play_overpower(
                                calculate_overpower_base(pb.score, chart.const),
                                ComboLamp(pb.combo_lamp),
                            )
                        )

                    for chart in charts:
                        if chart.const is None:
                            continue

                        chart_max_op = calculate_overpower_max(chart.const)
                        max_op += chart_max_op

                        if (chart.song_id, chart.difficulty) in played_charts:
                            max_played_op += chart_max_op
                else:  # level folder not specified, calculate by max difficulty
                    op_by_song: dict[int, Decimal] = {}
                    max_op_by_song: dict[int, Decimal] = {}

                    for pb in pbs:
                        chart = charts_by_id_difficulty.get((pb.song_id, pb.difficulty))

                        if chart is None or chart.const is None:
                            continue

                        op_by_song[chart.song_id] = max(
                            op_by_song.get(chart.song_id, Decimal(0)),
                            (
                                Decimal(pb.overpower) / 1000
                                if pb.overpower is not None
                                else calculate_play_overpower(
                                    calculate_overpower_base(pb.score, chart.const),
                                    ComboLamp(pb.combo_lamp),
                                )
                            ),
                        )

                    for chart in charts:
                        if chart.const is None:
                            continue

                        max_op_by_song[chart.song_id] = max(
                            max_op_by_song.get(chart.song_id, Decimal(0)),
                            calculate_overpower_max(chart.const),
                        )

                    op = sum(op_by_song.values(), Decimal(0))
                    max_op = sum(max_op_by_song.values(), Decimal(0))
                    max_played_op = sum(
                        (
                            max_op
                            for song_id, max_op in max_op_by_song.items()
                            if song_id in op_by_song
                        ),
                        Decimal(0),
                    )

                op = floor_to_ndp(op, 2)
                max_op = floor_to_ndp(max_op, 2)
                max_played_op = floor_to_ndp(max_played_op, 2)
                op_percent = (
                    floor_to_ndp(op * 100 / max_op, 2) if max_op > 0 else Decimal(0)
                )
                op_played_percent = (
                    floor_to_ndp(op * 100 / max_played_op, 2)
                    if max_played_op > 0
                    else Decimal(0)
                )

                # TODO: update when new filters are added; embed colors are only calculated
                # by possession rules on the full view
                if (
                    level is None
                    and (difficulty is None or difficulty == "MASTER+ULTIMA")
                    and genre is None
                    and version is None
                ):
                    total_mas_ult = counts["MAS"] + counts["ULT"]

                    # Don't bother with the rating check because if you S every MAS/ULT
                    # you're going to end up above 16 rating anyways.
                    if op_percent >= Decimal("99.5") and (
                        counts[f"MAS_{Rank.sss}"] + counts[f"ULT_{Rank.sss}"]
                        == total_mas_ult
                    ):
                        embed.color = Possession.rainbow.color
                    elif op_percent >= 99 and (
                        counts[f"MAS_{Rank.ss}"] + counts[f"ULT_{Rank.ss}"]
                        == total_mas_ult
                    ):
                        embed.color = Possession.platinum.color
                    elif op_percent >= Decimal("97.5") and (
                        counts[f"MAS_{Rank.sp}"] + counts[f"ULT_{Rank.sp}"]
                        == total_mas_ult
                    ):
                        embed.color = Possession.gold.color
                    elif (
                        counts[f"MAS_{Rank.s}"] + counts[f"ULT_{Rank.s}"]
                        == total_mas_ult
                    ):
                        embed.color = Possession.silver.color
                    else:
                        embed.color = Possession.none.color

                description_parts.append(
                    f"▸ **OVER POWER**: {bold_if(op == max_op, f'{op} / {max_op} ({op_percent:.2f}%)')}\n"
                    f"▸ **OVER POWER (played)**: {bold_if(op == max_played_op, f'{op} / {max_played_op} ({op_played_percent:.2f}%)')}\n"
                )

            description_parts.append(
                f"▸ **Average score (played)**: {int(statistics.fmean(pb.score for pb in pbs)) if len(pbs) > 0 else 0}\n"
                f"▸ **Average score (all)**: {int(sum(pb.score for pb in pbs) / chart_count)}\n"
            )

            rank_parts = [
                f"{self.settings.icons.rank_icon(rank) if rank != '99AJ' else rank} {bold_if(counts[rank] == chart_count, counts[rank])}"
                for rank in (
                    "99AJ",
                    Rank.sssp,
                    Rank.sss,
                    Rank.ssp,
                    Rank.ss,
                    Rank.sp,
                    Rank.s,
                )
            ]
            description_parts.append(f"▸ **Ranks**: {' / '.join(rank_parts)}")

            combo_lamp_parts = [
                f"{combo_lamp.short()} {bold_if(counts[combo_lamp] == chart_count, counts[combo_lamp])}"
                for combo_lamp in ComboLamp
                if combo_lamp != ComboLamp.none
            ]
            combo_lamp_parts.reverse()
            description_parts.append(
                f"▸ **Combo lamps**: {' / '.join(combo_lamp_parts)}"
            )

            clear_lamp_parts = [
                f"{clear_lamp.short()} {bold_if(counts[clear_lamp] == chart_count, counts[clear_lamp])}"
                for clear_lamp in ClearLamp
                if clear_lamp != ClearLamp.failed
            ]
            clear_lamp_parts.reverse()
            description_parts.append(
                f"▸ **Clear lamps**: {' / '.join(clear_lamp_parts)}\n"
            )

            # TODO: update this if new filters are added; only the full version view should
            # show title completion
            if (
                version is not None
                and level is None
                and difficulty is None
                and genre is None
            ):
                description_parts.append("▸ **Title completion**:")
                description_parts.append(
                    "`Total  ` ▸ "
                    f"{Difficulty.basic.emoji()} {bold(counts[Difficulty.basic.short()])}"
                    f" / {Difficulty.advanced.emoji()} {bold(counts[Difficulty.advanced.short()])}"
                    f" / {Difficulty.expert.emoji()} {bold(counts[Difficulty.expert.short()])}"
                    f" / {Difficulty.master.emoji()} {bold(counts[Difficulty.master.short()])}"
                )
                description_parts.extend(
                    [
                        (
                            f"{name} ▸ "
                            f"{Difficulty.basic.emoji()} {bold_if(counts[f'{Difficulty.basic.short()}_{criteria}'] == counts[Difficulty.basic.short()], counts[f'{Difficulty.basic.short()}_{criteria}'])}"
                            f" / {Difficulty.advanced.emoji()} {bold_if(counts[f'{Difficulty.advanced.short()}_{criteria}'] == counts[Difficulty.advanced.short()], counts[f'{Difficulty.advanced.short()}_{criteria}'])}"
                            f" / {Difficulty.expert.emoji()} {bold_if(counts[f'{Difficulty.expert.short()}_{criteria}'] == counts[Difficulty.expert.short()], counts[f'{Difficulty.expert.short()}_{criteria}'])}"
                            f" / {Difficulty.master.emoji()} {bold_if(counts[f'{Difficulty.master.short()}_{criteria}'] == counts[Difficulty.master.short()], counts[f'{Difficulty.master.short()}_{criteria}'])}"
                        )
                        for name, criteria in [
                            ("`SPIRIT `", Rank.s),
                            ("`TRIBUTE`", Rank.sss),
                            ("`LEGEND `", ComboLamp.all_justice),
                        ]
                    ]
                )

            embed.description = "\n".join(description_parts)
            embeds = [embed]
            featured_scores: list[DBPersonalBest] = []

            if len(pbs) >= 2:
                featured_scores = [pbs[0], pbs[-1]]
            elif len(pbs) > 0:
                featured_scores = [pbs[0]]

            embeds += [
                ScoreCardEmbed(
                    await self.scores.convert_to_network_pb(pb),
                    synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                )
                for pb in featured_scores
            ]

        await ctx.respond_or_edit(embeds=embeds)


async def setup(bot: "ChuniBot"):
    await bot.add_cog(PlayerStatisticsCog(bot))
