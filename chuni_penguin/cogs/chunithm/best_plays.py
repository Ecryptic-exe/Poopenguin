# ruff: noqa: E731
"""Best-play frames and filtered top/bottom scores with shared services."""

import asyncio
import itertools
import math
import random
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Optional

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from chuni_penguin import flags
from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.utils import calculate_ongeki_rating_breakdown
from chuni_penguin.constants import CACHE_DIR, CURRENT_CHUNITHM_VERSION, ChunithmVersion
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    DifficultyConverter,
    GenreConverter,
    LevelConverter,
    MemberOrUserConverter,
    RankConverter,
    VersionConverter,
)
from chuni_penguin.database import UserConfig
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.renderers.b50 import render_b30
from chuni_penguin.renderers.b50_ongeki import render_b30 as render_b30_ongeki
from chuni_penguin.services.jackets import JacketCache
from chuni_penguin.types import (
    ClearLamp,
    Difficulty,
    Genre,
    PersonalBest,
    Profile,
    Rank,
    RatingBreakdown,
    RatingFrameType,
    RatingType,
    Score,
)
from chuni_penguin.ui import B30N20View, B30View, FriendCodeOfferView
from chuni_penguin.utils import AsyncTemporaryFile
from chuni_penguin.utils.misc import Reversor

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class BestPlaysCog(commands.Cog, name="BestPlays"):
    def __init__(self, bot: "ChuniBot"):
        self.accounts = bot.services.accounts
        self.users = bot.services.users
        self.database = bot.services.database
        self._random = random.Random()
        self.jacket_cache = JacketCache()

    async def _prepare_jackets(self, records):
        cache = getattr(self, "jacket_cache", None)
        if cache is None:
            cache = self.jacket_cache = JacketCache()
        return await cache.ensure(records)

    async def cog_load(self):
        (CACHE_DIR / "b50").mkdir(parents=True, exist_ok=True)

    async def _best50_ongeki(
        self,
        ctx: PenguinContext,
        profile: Profile,
        rating_breakdown: RatingBreakdown,
        user_config: UserConfig | None = None,
    ):
        current_rating = float(rating_breakdown.rating)
        records = rating_breakdown.frames[RatingFrameType.best].scores
        record_slots = rating_breakdown.frames[RatingFrameType.best].num_scores
        platinum_records = rating_breakdown.frames[RatingFrameType.platinum].scores
        platinum_record_slots = rating_breakdown.frames[
            RatingFrameType.platinum
        ].num_scores

        if (new_frame := rating_breakdown.frames.get(RatingFrameType.new)) is not None:
            new_records = new_frame.scores
            new_record_slots = new_frame.num_scores
        else:
            new_records = None
            new_record_slots = 0

        await self._prepare_jackets([*records, *(new_records or ()), *platinum_records])
        async with AsyncTemporaryFile() as f:
            await asyncio.to_thread(
                render_b30_ongeki,
                player_name=profile.username,
                output=f,
                records=records,
                record_slots=record_slots,
                new_records=new_records,
                new_record_slots=new_record_slots,
                platinum_records=platinum_records,
                platinum_record_slots=platinum_record_slots,
                current_rating=current_rating,
                user_config=user_config or ctx.user_config,
            )

            generation_timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")

            await ctx.respond_or_edit(
                files=[
                    discord.File(
                        f,
                        filename=f"chuni-penguin-b50-{profile.username}-{generation_timestamp}.png",
                    )
                ],
            )

    async def _best50_from_friend_code(
        self,
        ctx: PenguinContext,
        friend_code: str,
        *,
        classic: bool = False,
        rating_system: Literal["naive", "ingame", "ongeki", "ongeki-naive"]
        | None = None,
    ):
        pbs: list[PersonalBest] = []
        records: list[PersonalBest] = []
        record_slots: int = 30
        new_records: list[PersonalBest] | None = None
        new_record_slots: int = 20

        async with ctx.typing():
            (
                profile,
                pbs,
            ) = await self.accounts.fetch_chunithm_net_from_friend_code(
                ctx, friend_code
            )

        pbs.sort(
            key=lambda pb: (
                pb.rating,
                pb.score,
                pb.combo_lamp,
                pb.chart.internal_level,
            ),
            reverse=True,
        )

        if rating_system == "naive":
            records = pbs[:50]
            record_slots = 50
            new_records = None
            new_record_slots = 0
            current_rating = None
        elif rating_system in ("ongeki", "ongeki-naive"):
            breakdown = calculate_ongeki_rating_breakdown(
                RatingType.ongeki
                if rating_system == "ongeki"
                else RatingType.ongeki_naive,
                pbs,
            )
            await self._best50_ongeki(ctx, profile, breakdown, ctx.user_config)
            return
        else:
            new_records = []
            current_rating = profile.rating_systems[0].value

            for pb in pbs:
                if (
                    pb.song.version == CURRENT_CHUNITHM_VERSION
                    and len(new_records) < new_record_slots
                ):
                    new_records.append(pb)

                if (
                    pb.song.version != CURRENT_CHUNITHM_VERSION
                    and len(records) < record_slots
                ):
                    records.append(pb)

                if (
                    len(records) >= record_slots
                    and len(new_records) >= new_record_slots
                ):
                    break

        async with ctx.typing():
            await self._best50_respond(
                ctx,
                profile,
                ctx.user_config,
                current_rating,
                records,
                record_slots,
                new_records,
                new_record_slots,
                "default",
                classic=classic,
            )

    async def _best50_inner(
        self,
        ctx: PenguinContext,
        user: discord.User | discord.Member | None = None,
        *,
        classic: bool = False,
        kamaitachi: bool = False,
        new_rating: bool = False,
        rating_system: Literal["naive", "ingame", "ongeki", "ongeki-naive"]
        | None = None,
    ):
        target_id = ctx.author.id if user is None else user.id
        cookie = await self.database.cookies.get_by_discord_id(target_id)

        if (
            user is None
            and not kamaitachi
            and (
                cookie is None
                or (not cookie.cookie and cookie.kamaitachi_token is None)
            )
        ):
            view = FriendCodeOfferView(
                ctx, cookie.friend_code if cookie is not None else None
            )
            await view.start()
            return

        rating_type = None

        if rating_system == "ingame":
            rating_type = RatingType.in_game
        elif rating_system == "naive":
            rating_type = RatingType.naive
        elif rating_system == "ongeki":
            rating_type = RatingType.ongeki
        elif rating_system == "ongeki-naive":
            rating_type = RatingType.ongeki_naive
        elif rating_system is not None:
            msg = f"Unknown rating system {rating_system}"
            raise commands.BadArgument(msg)

        async with (
            ctx.typing(),
            self.accounts.network(ctx, target_id, kamaitachi=kamaitachi) as client,
        ):
            user_config = await self.users.fetch_user_config(target_id)
            profile = await client.get_profile()

            if rating_type is None:
                rating_type = (
                    client.DEFAULT_RATING_SYSTEM
                    if not new_rating
                    else RatingType.in_game
                )

            breakdown = await client.get_rating_breakdown(rating_type)

            if rating_type in (RatingType.ongeki, RatingType.ongeki_naive):
                await self._best50_ongeki(ctx, profile, breakdown, user_config)
                return

            current_rating = float(breakdown.rating)
            records = breakdown.frames[RatingFrameType.best].scores
            record_slots = breakdown.frames[RatingFrameType.best].num_scores

            if (new_frame := breakdown.frames.get(RatingFrameType.new)) is not None:
                new_records = new_frame.scores
                new_record_slots = new_frame.num_scores
            else:
                new_records = None
                new_record_slots = 0

            await self._best50_respond(
                ctx,
                profile,
                user_config,
                current_rating,
                records,
                record_slots,
                new_records,
                new_record_slots,
                "default",
                classic=classic,
            )

    async def _best50_respond(
        self,
        ctx: PenguinContext,
        profile: Profile,
        user_config: UserConfig,
        current_rating: float | None,
        records: list[PersonalBest],
        record_slots: int,
        new_records: list[PersonalBest] | None,
        new_record_slots: int,
        theme: Literal["default", "xverse"],
        *,
        classic: bool,
    ):
        if classic:
            if new_records is not None:
                view = B30N20View(
                    ctx,
                    records,
                    new_records,
                    synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                )
            else:
                view = B30View(
                    ctx,
                    records,
                    record_slots,
                    show_reachable=False,
                    synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                )

            await view.start()

            return

        uncross_verse = self._random.random() <= 0.1

        if uncross_verse:
            await self.database.user_found_easter_egg(
                ctx.author.id, "chunithm-uncross-verse"
            )

        await self._prepare_jackets([*records, *(new_records or ())])
        async with AsyncTemporaryFile() as f:
            await asyncio.to_thread(
                render_b30,
                player_name=profile.username,
                output=f,
                records=records,
                record_slots=record_slots,
                new_records=new_records,
                new_record_slots=new_record_slots,
                current_rating=current_rating,
                user_config=user_config,
                uncross_verse=uncross_verse,
                theme=theme,
            )

            generation_timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")

            await ctx.respond_or_edit(
                files=[
                    discord.File(
                        f,
                        filename=f"chuni-penguin-b50-{profile.username}-{generation_timestamp}.png",
                    )
                ],
            )

    @flags.command("best50", aliases=["best30", "b30", "b50"])
    @flags.argument("-c", "--classic", action="store_true")
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument("-n", "--new-rating", action="store_true")
    @flags.argument(
        "-r",
        "--rating-system",
        choices=["naive", "ingame", "ongeki", "ongeki-naive"],
        default=None,
        required=False,
    )
    @flags.argument("user", nargs="?", default=None)
    @commands.cooldown(15, 600, commands.BucketType.member)
    @logged_prefix_command
    async def best50(
        self,
        ctx: PenguinContext,
        *,
        classic: bool = False,
        kamaitachi: bool = False,
        new_rating: bool = False,
        rating_system: Literal["naive", "ingame", "ongeki", "ongeki-naive"]
        | None = None,
        user: str | None = None,
    ):
        """View top 50 scores of you or another player.

        **Parameters**:
        `user`: The user to get scores for. Alternatively, a CHUNITHM International friend code is also accepted.
        `-c, --classic`: View your scores with Discord embeds instead of generating an image.
        `-k, --kamaitachi`: Get the best 50 scores from Kamaitachi, if the user has that linked.
        `-n, --new-rating`: Calculates best30 + new20 instead of best50.
        `-r, --rating-system`: Choose from: `ingame` (best30 + new20), `naive` (best50), `ongeki` (best50 + new10 + "platinum50" with ongeki formula), `ongeki-naive` (best60 + "platinum50" with ongeki formula). `-r ingame` is functionally equivalent to `-n`.
        """

        if not classic and not ctx.bot_permissions.attach_files:
            raise commands.BotMissingPermissions(["attach_files"])

        kwargs = {
            "classic": classic,
            "kamaitachi": kamaitachi,
            "new_rating": new_rating,
            "rating_system": rating_system,
        }

        if user is not None:
            try:
                discord_user = await MemberOrUserConverter().convert(ctx, user)

                await self._best50_inner(ctx, discord_user, **kwargs)
            except commands.UserNotFound:
                if kamaitachi or not user.isdigit():
                    raise

                await self._best50_from_friend_code(
                    ctx, user, classic=classic, rating_system=rating_system
                )
        else:
            await self._best50_inner(ctx, None, **kwargs)

    @app_commands.command(name="best50", description="View top plays")
    @app_commands.checks.cooldown(15, 600, key=lambda i: i.user.id)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(
        user="The user to get best50 for",
        friend_code="The friend code to get best50 for",
        classic="View your best 50 scores using Discord embeds instead of an image",
        kamaitachi="Get your best 50 from Kamaitachi if linked",
        new_rating="(Kamaitachi) Calculates best30+new20 instead of best50",
        rating_system="The rating system to view the best50 for",
    )
    @app_commands.rename(
        friend_code="friend-code",
        new_rating="new-rating",
        rating_system="rating-system",
    )
    @app_commands.choices(
        rating_system=[
            app_commands.Choice(name="In-game (Best 30 + New 20)", value="ingame"),
            app_commands.Choice(name="Naive (Best 50)", value="naive"),
            app_commands.Choice(
                name='O.N.G.E.K.I. (Best 50 + New 10 + "Platinum 50")', value="ongeki"
            ),
            app_commands.Choice(
                name='O.N.G.E.K.I. Naive (Best 60 + "Platinum 50")',
                value="ongeki-naive",
            ),
        ]
    )
    @logged_app_command
    async def best50_slash(
        self,
        interaction: Interaction["ChuniBot"],
        user: discord.User | discord.Member | None = None,
        *,
        friend_code: str | None = None,
        classic: bool = False,
        kamaitachi: bool = False,
        new_rating: bool = False,
        rating_system: Literal["naive", "ingame", "ongeki", "ongeki-naive"]
        | None = None,
    ):
        if friend_code is not None and user is not None:
            msg = "Cannot specify both a user and a friend code."
            raise commands.BadArgument(msg)

        if friend_code is not None and not friend_code.isdigit():
            msg = "Invalid friend code."
            raise commands.BadArgument(msg)

        ctx = await PenguinContext.from_interaction(interaction)

        if friend_code is not None:
            await self._best50_from_friend_code(
                ctx,
                friend_code,
                classic=classic,
                rating_system=rating_system,
            )
        else:
            await self._best50_inner(
                ctx,
                user,
                classic=classic,
                kamaitachi=kamaitachi,
                new_rating=new_rating,
                rating_system=rating_system,
            )

    @app_commands.command(name="top", description="View your best scores for a level.")
    @app_commands.describe(
        level="Level (from 1 to 15+) to search for.",
        difficulty="Difficulty to search for.",
        genre="Genre to search for.",
        rank="Rank to search for.",
        sort="Sort records by a criteria (default rating).",
        sort_order="Specify the order to sort records by.",
        version="Version to search for.",
        kamaitachi="Get scores from Kamaitachi, if the target user has a linked account",
    )
    @app_commands.choices(
        level=[
            *[app_commands.Choice(name=str(i), value=str(i)) for i in range(1, 7)],
            *itertools.chain.from_iterable(
                [
                    (
                        app_commands.Choice(name=f"{i}", value=f"{i}"),
                        app_commands.Choice(name=f"{i}+", value=f"{i}+"),
                    )
                    for i in range(7, 16)
                ]
            ),
        ],
        difficulty=[
            app_commands.Choice(name=str(x), value=x.value)
            for x in Difficulty.__members__.values()
        ],  # type: ignore[reportGeneralTypeIssues]
        genre=[
            app_commands.Choice(name=str(x), value=x.value)
            for x in Genre.__members__.values()
        ],  # type: ignore[reportGeneralTypeIssues]
        rank=[
            app_commands.Choice(name=str(x), value=x.value)
            for x in Rank.__members__.values()
        ],  # type: ignore[reportGeneralTypeIssues]
        version=[
            app_commands.Choice(name=x, value=x) for x in ChunithmVersion.__args__
        ],
    )
    @logged_app_command
    async def top_slash(
        self,
        interaction: "discord.Interaction[ChuniBot]",
        *,
        user: Optional[discord.User | discord.Member] = None,
        level: Optional[str] = None,
        difficulty: Optional[Difficulty] = None,
        genre: Optional[Genre] = None,
        rank: Optional[Rank] = None,
        sort: Literal[
            "rating",
            "score",
            "overpower",
            "overpower %",
            "note lamp",
            "clear lamp",
            "life",
        ] = "rating",
        sort_order: Literal["ascending", "descending"] = "descending",
        version: str | None = None,
        kamaitachi: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)
        target_user_id = interaction.user.id if user is None else user.id

        async with self.accounts.network(
            ctx, target_user_id, kamaitachi=kamaitachi
        ) as client:
            # legacy behavior
            if (
                isinstance(client, ChunithmNetAdapter)
                and level is None
                and difficulty is None
                and genre is None
                and rank is None
                and version is None
            ):
                await self._best50_inner(ctx, user)
                return

            await interaction.response.defer()

            try:
                records = await client.get_personal_bests(
                    level=level,
                    difficulty=difficulty,
                    genre=genre,
                    rank=rank,
                    version=version,  # pyright: ignore[reportArgumentType]
                )
            except ValueError as e:
                raise commands.BadArgument(str(e)) from None

            if len(records) == 0:
                await interaction.followup.send("No scores found.")
                return

        if sort == "rating":
            records.sort(
                reverse=sort_order != "ascending",
                key=lambda x: (
                    x.rating,
                    x.score,
                    x.overpower,
                    x.combo_lamp.value,
                    x.clear_lamp.value,
                ),
            )
        elif sort == "score":
            records.sort(
                reverse=sort_order != "ascending",
                key=lambda x: (
                    x.score,
                    x.rating,
                    x.overpower,
                    x.combo_lamp.value,
                    x.clear_lamp.value,
                ),
            )
        elif sort == "overpower":
            records.sort(
                reverse=sort_order != "ascending",
                key=lambda x: (
                    x.overpower,
                    x.rating,
                    x.score,
                    x.combo_lamp.value,
                    x.clear_lamp.value,
                ),
            )
        elif sort == "overpower %":
            records.sort(
                reverse=sort_order != "ascending",
                key=lambda x: (
                    (
                        Decimal(0)
                        if x.chart.max_overpower is None
                        else ((x.overpower or Decimal(0)) / x.chart.max_overpower)
                    ),
                    x.overpower,
                    x.rating,
                    x.score,
                    x.combo_lamp.value,
                    x.clear_lamp.value,
                ),
            )
        elif sort == "note lamp":
            records.sort(
                reverse=sort_order != "ascending",
                key=lambda x: (
                    x.combo_lamp.value,
                    x.rating,
                    x.score,
                    x.overpower,
                    x.clear_lamp.value,
                ),
            )
        elif sort == "clear lamp":
            records.sort(
                reverse=sort_order != "ascending",
                key=lambda x: (
                    x.clear_lamp.value,
                    x.rating,
                    x.score,
                    x.overpower,
                    x.combo_lamp.value,
                ),
            )
        elif sort == "life":
            records.sort(
                reverse=sort_order != "ascending",
                key=lambda x: (
                    Reversor(
                        (x.judgements.justice + x.judgements.attack + x.judgements.miss)
                        if x.judgements is not None and x.clear_lamp != ClearLamp.failed
                        else math.inf
                    ),
                    x.rating,
                    x.score,
                    x.overpower,
                    x.combo_lamp.value,
                    x.clear_lamp.value,
                ),
            )

        view = B30View(
            ctx,
            records,
            show_average=False,
            show_reachable=False,
            show_lamps=True,
            synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
        )
        await view.start()

        return

    @flags.command("top", aliases=["bottom"])
    @flags.argument("-d", "--difficulty", required=False, type=DifficultyConverter)
    @flags.argument("-g", "--genre", required=False, type=GenreConverter)
    @flags.argument("-r", "--rank", required=False, type=RankConverter)
    @flags.argument(
        "-s",
        "--sort",
        choices=[
            key + order
            for key in (
                "score",
                "rating",
                "op",
                "op_percent",
                "overpower",
                "overpower_percent",
                "note_lamp",
                "notelamp",
                "clear_lamp",
                "clearlamp",
                "hp",
                "life",
            )
            for order in ("", "-", "+")
        ],
        nargs="+",
        required=False,
    )
    @flags.argument("-v", "--version", required=False, type=VersionConverter)
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument(
        "user", nargs=flags.OPTIONAL_INVISIBLE, default=None, type=MemberOrUserConverter
    )
    @flags.argument("level", nargs="?", default=None)
    @logged_prefix_command
    async def top(
        self,
        ctx: PenguinContext,
        *,
        difficulty: Difficulty | None = None,
        genre: Genre | None = None,
        rank: Rank | None = None,
        sort: list[str] | None = None,
        version: ChunithmVersion | None = None,
        kamaitachi: bool = False,
        user: discord.User | discord.Member | None = None,
        level: str | None = None,
    ):
        """
        **View your best scores for a level.**

        **Parameters:**
        `user`: Discord username of the player. Yourself, if not provided.
        `level`: Level (from 1 to 15+) to search for.
        `-d`: Difficulty to search for. Must be one of `BASIC`, `ADVANCED`, `EXPERT`, `MASTER`, `ULTIMA`, or `WE` if specified.
        `-g`: Genre to search for.
        `-r`: Rank to search for.
        `-s`: Choose a metric to sort scores by. You can specify multiple metrics, e.g. `-s score clear_lamp`. You can optionally add `+` or `-` after a metric to sort in ascending or descending order, e.g. `score+`. The default is to sort by rating, then score, then overpower, then note lamp, then clear lamp, in descending order. Supported options are:
        - `score`: Sort by score.
        - `rating`: Sort by calculated play rating. Might not work if there are unknown songs.
        - `overpower`: Sort by calculated raw overpower value.
        - `overpower_percent`: Sort by calculated overpower percentage.
        - `note_lamp`: Sort by note lamp (also known as combo lamp, e.g. FULL COMBO/ALL JUSTICE).
        - `clear_lamp`: Sort by clear lamp (FAILED, CLEAR, HARD, BRAVE, ABSOLUTE, CATASTROPHY).
        - `life`: Sort by total number of JUSTICE/ATTACK/MISS, if data is available.
        `-k`: Get scores from Kamaitachi, if the target user has a linked account.

        On CHUNITHM-NET, at least level or difficulty must be set.

        If multiple parameters are set, they will be applied in order of level, difficulty, genre, rank.

        **Examples:**
        `c>top 14+`: View your best scores for level 14+
        `c>top -d mas`: View your best scores for MASTER difficulty
        `c>top -g original -d ultima`: View your best scores for ULTIMA difficulty in the ORIGINAL folder
        `c>top @player -r sss -d mas`: View @player's best scores for SSS rank on MASTER difficulty.
        """

        target_user_id = ctx.author.id if user is None else user.id

        async with (
            ctx.typing(),
            self.accounts.network(ctx, target_user_id, kamaitachi=kamaitachi) as client,
        ):
            if (
                isinstance(client, ChunithmNetAdapter)
                and level is None
                and difficulty is None
                and genre is None
                and rank is None
                and version is None
            ):
                if not ctx.bot_permissions.attach_files:
                    raise commands.BotMissingPermissions(["attach_files"])

                if ctx.invoked_with == "bottom":
                    raise commands.CommandNotFound

                await self._best50_inner(ctx, user)
                return

            level_folder: str | None = None
            internal_level: float | None = None

            if level is not None:
                level_data = await LevelConverter().convert(ctx, level)
                level_folder = level_data.level
                internal_level = level_data.const

            try:
                records = await client.get_personal_bests(
                    level=level_folder,
                    difficulty=difficulty,
                    genre=genre,
                    rank=rank,
                    version=version,
                )
            except ValueError as e:
                raise commands.BadArgument(str(e)) from None

            if internal_level is not None:
                records = [
                    r for r in records if r.chart.internal_level == internal_level
                ]

            if len(records) == 0:
                await ctx.reply("No scores found.", mention_author=False)
                return

            sort_fns: list[Callable[[Score], Any]] = []

            if sort is not None:
                for item in sort:
                    sort_fn: Callable[[Score], Any] = lambda score: None

                    if item.startswith("score"):
                        sort_fn = lambda score: score.score
                    elif item.startswith("rating"):
                        sort_fn = lambda score: score.rating or Decimal(0)
                    elif item.startswith(("op_percent", "overpower_percent")):
                        sort_fn = lambda score: (
                            score.overpower / score.chart.max_overpower * 100
                            if score.overpower is not None
                            and score.chart.max_overpower is not None
                            else Decimal(0)
                        )
                    elif item.startswith(("op", "overpower")):
                        sort_fn = lambda score: score.overpower or Decimal(0)
                    elif item.startswith(("note_lamp", "notelamp")):
                        sort_fn = lambda score: score.combo_lamp.value
                    elif item.startswith(("clear_lamp", "clearlamp")):
                        sort_fn = lambda score: score.clear_lamp.value
                    elif item.startswith(("hp", "life")):
                        sort_fn = (
                            lambda score: (
                                score.judgements.justice
                                + score.judgements.attack
                                + score.judgements.miss
                            )
                            if (
                                score.judgements is not None
                                and score.clear_lamp != ClearLamp.failed
                            )
                            else math.inf
                        )

                    # other metrics are default descending, but hp/life is default
                    # ascending, since lower number of mistakes is better
                    apply_reversor = True

                    if item.startswith(("hp", "life")):
                        apply_reversor = item.endswith("-")
                    else:
                        apply_reversor = not item.endswith("+")

                    if apply_reversor:
                        sort_fns.append(
                            lambda score, sort_fn=sort_fn: Reversor(sort_fn(score))
                        )
                    else:
                        sort_fns.append(sort_fn)

            # fallback metrics
            sort_fns.extend(
                [
                    lambda score: Reversor(score.rating),
                    lambda score: Reversor(score.score),
                    lambda score: (
                        score.judgements.justice
                        + score.judgements.attack
                        + score.judgements.miss
                    )
                    if (
                        score.judgements is not None
                        and score.clear_lamp != ClearLamp.failed
                    )
                    else math.inf,
                    lambda score: Reversor(score.overpower),
                    lambda score: Reversor(score.combo_lamp.value),
                    lambda score: Reversor(score.clear_lamp.value),
                ]
            )

            records.sort(
                key=lambda score: tuple([sort_fn(score) for sort_fn in sort_fns]),
                reverse=ctx.invoked_with == "bottom",
            )

        view = B30View(
            ctx,
            records,
            show_average=False,
            show_reachable=False,
            show_lamps=True,
            synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
        )
        await view.start()


async def setup(bot: "ChuniBot"):
    await bot.add_cog(BestPlaysCog(bot))
