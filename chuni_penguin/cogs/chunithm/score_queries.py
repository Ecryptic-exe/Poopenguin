"""Song scores and message comparisons with explicit shared dependencies."""

import contextlib
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from chuni_penguin import flags
from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.errors import ChartNotFound, SongNotFound
from chuni_penguin.constants import INTERNATIONAL_JACKET_BASE, JACKET_BASE
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import (
    AliasNameConverter,
    AliasNameTransformer,
    MemberOrUserConverter,
)
from chuni_penguin.logging import logged_app_command, logged_prefix_command
from chuni_penguin.types import Difficulty
from chuni_penguin.ui import EmbedPaginationView, ScoreCardEmbed, SelectToCompareView
from chuni_penguin.ui.autocomplete import song_title_choices
from chuni_penguin.utils.score_messages import (
    extract_images_from_component,
    extract_images_from_message,
)

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ScoreQueriesCog(commands.Cog, name="ScoreQueries"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.settings = bot.settings
        self.accounts = bot.services.accounts
        self.autocomplete = bot.services.autocomplete
        self.score_queries = bot.services.utils.score_queries
        self.compare_context_menu = app_commands.ContextMenu(
            name="View your score", callback=self.compare_context_menu_callback
        )

    async def cog_load(self):
        self.bot.tree.add_command(self.compare_context_menu)

    async def cog_unload(self):
        menu = self.compare_context_menu
        if self.bot.tree.get_command(menu.name, type=menu.type) is menu:
            self.bot.tree.remove_command(menu.name, type=menu.type)

    async def _compare_from_message(
        self,
        ctx: PenguinContext,
        message: discord.Message,
        message_images: list[str] | None = None,
        user: discord.User | discord.Member | None = None,
        *,
        kamaitachi: bool = False,
    ):
        if message_images is None:
            message_images = extract_images_from_message(message)

        if len(message_images) == 0:
            msg = "The message replied to does not contain any charts/scores."
            raise commands.BadArgument(msg)

        embeds = message.embeds.copy()
        containers = [
            component
            for component in message.components
            if isinstance(component, discord.components.Container)
        ]

        for snapshot in message.message_snapshots:
            embeds.extend(snapshot.embeds)
            containers.extend(
                [
                    component
                    for component in snapshot.components
                    if isinstance(component, discord.components.Container)
                ]
            )

        target_id = user.id if user is not None else ctx.author.id

        async with (
            ctx.typing(),
            self.accounts.network(ctx, target_id, kamaitachi=kamaitachi) as client,
        ):
            jackets = await self.score_queries.jackets_for_images(message_images)

            if len(jackets) == 0:
                msg = "No songs found."
                raise commands.CommandError(msg)

            if len(jackets) > 1:
                options = []

                for i, jacket in enumerate(jackets):
                    displayed_option = jacket.song.title

                    if jacket.song.id >= 8000:
                        displayed_option += f" [{jacket.song.charts[0].level}]"

                    options.append((displayed_option, i))

                view = SelectToCompareView(ctx, options)
                await ctx.respond_or_edit("Select a score to compare with:", view=view)

                await view.wait()

                if view.value is None:
                    await ctx.respond_or_edit(
                        content="Timed out before selecting a score.", view=None
                    )
                    return

                jacket = jackets[int(view.value)]
                song = jacket.song
            else:
                jacket = jackets[0]
                song = jacket.song

            if isinstance(client, ChunithmNetAdapter):
                song.raise_if_not_available()

            displayed_song = escape_markdown(song.title)

            if song.id >= 8000 and len(song.charts) > 0:
                displayed_song += f" [{escape_markdown(song.charts[0].level)}]"

            try:
                records = await client.get_personal_bests_on_song(song.id)
            except (SongNotFound, ChartNotFound):
                msg = (
                    f"The song **{displayed_song}** is not available on {client.NAME}."
                )
                raise commands.CommandError(msg) from None
            except NotImplementedError:
                msg = f"Network {client.NAME} does not support fetching scores for a specific song."
                raise commands.CommandError(msg) from None

            profile = await client.get_minimal_profile()

            if len(records) == 0:
                await ctx.respond_or_edit(
                    f"No records found for {profile.username} on **{displayed_song}**."
                )
                return

            records.sort(key=lambda r: r.chart.difficulty.value)

            page = 0
            embed_color = 0

            try:
                selected_embed = next(
                    x
                    for x in embeds
                    if jacket.jacket_url in {x.thumbnail.url, x.image.url}
                )
                embed_color = (
                    selected_embed.color.value
                    if selected_embed.color is not None
                    else 0
                )
            except StopIteration:
                for c in containers:
                    if jacket.jacket_url in extract_images_from_component(c):
                        embed_color = (
                            c.accent_color.value if c.accent_color is not None else 0
                        )
                        break

            with contextlib.suppress(ValueError):
                # embed_color may exist with invalid value
                difficulty = Difficulty.from_embed_color(embed_color)
                page = next(
                    (
                        i
                        for i, record in enumerate(records)
                        if record.chart.difficulty == difficulty
                    ),
                    0,
                )

            view = EmbedPaginationView(
                ctx,
                [
                    ScoreCardEmbed(
                        r,
                        synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                        detailed=True,
                    )
                    for r in records
                ],
            )
            view.current_page = page
            content = f"Top play for {profile.username} on {client.NAME}:"

        if ctx.response is not None:
            await view.start_from(ctx.response, content=content)
        else:
            await view.start(content=content)

    async def _compare_inner(
        self,
        ctx: PenguinContext,
        user: discord.User | discord.Member | None = None,
        *,
        kamaitachi: bool = False,
    ):
        url_whitelist = [JACKET_BASE, INTERNATIONAL_JACKET_BASE]
        image_urls_by_message: dict[int, list[str]] = {}

        if self.settings.web.serve_assets and self.settings.web.base_url:
            url_whitelist.append(self.settings.web.base_url)

        if (message := await ctx.resolve_message_reference()) is None:
            try:

                def check(m: discord.Message):
                    if m.author != self.bot.user:
                        return False

                    # Secondary check needed when searching from message cache
                    if m.channel.id != ctx.channel.id:
                        return False

                    image_urls = extract_images_from_message(m, url_whitelist)

                    if len(image_urls) <= 0:
                        return False

                    image_urls_by_message[m.id] = image_urls
                    return True

                # Perform lookup in message cache first, which is faster and also
                # avoids a potential Read Message History permission error
                # cached_messages is internally is a deque, newer messages are at the end,
                # so a reverse is needed
                message = discord.utils.find(check, reversed(ctx.bot.cached_messages))

                if message is None:
                    message = await discord.utils.find(
                        check, ctx.channel.history(limit=50)
                    )
            except discord.errors.Forbidden as e:
                msg = "Bot requires the Read Message History permission to fetch recent scores."

                if ctx.interaction is None:
                    msg += f" Alternatively, run `{ctx.clean_prefix}compare` while replying to the score you want to compare."

                raise commands.CheckFailure(msg) from e

            if message is None:
                msg = "No recent scores found."
                raise commands.CommandError(msg)

        await self._compare_from_message(
            ctx,
            message,
            image_urls_by_message.get(message.id),
            user,
            kamaitachi=kamaitachi,
        )

    @flags.command("compare", aliases=["c", "mog", "gap"])
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument("user", nargs="?", default=None, type=MemberOrUserConverter)
    @logged_prefix_command
    async def compare(
        self,
        ctx: PenguinContext,
        *,
        kamaitachi: bool = False,
        user: discord.Member | discord.User | None = None,
    ):
        """Compare your best score with another score.

        By default, it's the most recently posted score. You can reply to another
        user's score to compare with that instead. If there are multiple scores in
        said message, you will be prompted to select one.

        **Tip**: This command also works with some other bots (<@986651489529397279> and <@604641359416131585>
        to name a few). However, you will need to explicitly reply to those other bots' messages.
        If you don't reply, only recent scores *from this bot* will be checked.

        **Parameters**
        `user`: The user to compare with (defaults to you).
        `-k, --kamaitachi`: Get scores from Kamaitachi, if the target user has a linked account.
        """

        await self._compare_inner(ctx, user, kamaitachi=kamaitachi)

    @app_commands.command(
        name="compare", description="Compare your best score with another score."
    )
    @app_commands.describe(
        user="The user to compare with (defaults to you)",
        kamaitachi="Get scores from Kamaitachi, if the target user has a linked account",
    )
    @logged_app_command
    async def compare_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        user: discord.User | discord.Member | None = None,
        *,
        kamaitachi: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)

        await self._compare_inner(ctx, user, kamaitachi=kamaitachi)

    async def compare_context_menu_callback(
        self, interaction: discord.Interaction["ChuniBot"], message: discord.Message
    ):
        await self._compare_from_message(
            await PenguinContext.from_interaction(interaction), message
        )

    async def song_title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return song_title_choices(self.autocomplete, current, interaction.guild_id)

    async def _scores_inner(
        self,
        ctx: PenguinContext,
        query: str,
        user: discord.User | discord.Member | None = None,
        *,
        kamaitachi: bool = False,
    ):
        target_id = ctx.author.id if user is None else user.id

        async with (
            ctx.typing(),
            self.accounts.network(ctx, target_id, kamaitachi=kamaitachi) as client,
        ):
            result = await ctx.find_songs(query, load_charts=True)

            if result is None:
                return

            # if we're fetching scores from Kamaitachi, we don't need to care about whether
            # the song is available in CHUNITHM International.
            songs = [
                x
                for x in result.songs
                if not isinstance(client, ChunithmNetAdapter) or x.available
            ]

            if len(songs) > 1:
                options = []

                for i, x in enumerate(songs):
                    if x.genre == "WORLD'S END":
                        title = f"{x.title} [{x.charts[0].level}]"
                    else:
                        title = x.title

                    options.append((title, i))
                view = SelectToCompareView(
                    ctx, options=options, placeholder="Select a song..."
                )
                await ctx.respond_or_edit(
                    "Multiple songs were found. Select one:", view=view
                )

                await view.wait()

                if view.value is None:
                    await ctx.respond_or_edit(
                        content="Timed out before selecting a song.", view=None
                    )
                    return

                song = songs[int(view.value)]
            elif len(songs) > 0:
                song = songs[0]
            else:
                msg = f"No songs currently available in CHUNITHM International matches the query. Closest match was **{escape_markdown(result.songs[0].title)}**."
                raise commands.BadArgument(msg)

            displayed_song = escape_markdown(song.title)

            if song.id >= 8000 and len(song.charts) > 0:
                displayed_song += f" [{escape_markdown(song.charts[0].level)}]"

            profile = await client.get_minimal_profile()

            try:
                records = await client.get_personal_bests_on_song(song.id)
            except (SongNotFound, ChartNotFound):
                msg = f"The song **{escape_markdown(song.title)}** is not available on {client.NAME}."
                raise commands.CommandError(msg) from None

            if len(records) == 0:
                msg = f"No records found for {profile.username} on **{displayed_song}** on {client.NAME}."

                await ctx.respond_or_edit(msg)
                return

            records.sort(key=lambda r: r.chart.difficulty.value)

            view = EmbedPaginationView(
                ctx,
                [
                    ScoreCardEmbed(
                        r,
                        synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                        detailed=True,
                    )
                    for r in records
                ],
            )
            content = f"Top play for {profile.username} on {client.NAME}:"

        if ctx.response is not None:
            await view.start_from(ctx.response, content=content)
        else:
            await view.start(content=content)

    @flags.command("scores", aliases=["score"])
    @flags.argument("-k", "--kamaitachi", action="store_true")
    @flags.argument(
        "user",
        nargs=flags.OPTIONAL_INVISIBLE,
        default=None,
        type=MemberOrUserConverter,
    )
    @flags.argument("query", nargs="*")
    @logged_prefix_command
    async def scores(
        self,
        ctx: PenguinContext,
        *,
        kamaitachi: bool = False,
        user: discord.Member | discord.User | None = None,
        query: list[str] | None = None,
    ):
        """Get a player's scores for a specific song.

        **Parameters**:
        `user` (not required): The user to get scores for. Must go first if specified.
        `query` (required): The song to search for. You don't have to be exact; try things out!
        `-k, --kamaitachi`: Get scores from Kamaitachi, if the user has that linked.
        """

        if query is None or len(query) <= 0:
            await self._compare_inner(ctx, user=user, kamaitachi=kamaitachi)
            return

        _query = await AliasNameConverter(lower=True).convert(ctx, " ".join(query))

        await self._scores_inner(ctx, query=_query, user=user, kamaitachi=kamaitachi)

    @app_commands.command(
        name="scores",
        description="Get personal bests for a specific song",
    )
    @app_commands.describe(
        query="The song to search for. You don't have to be exact; try things out!",
        user="The user to get scores for.",
        kamaitachi="Get scores from Kamaitachi, if the user has that linked.",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.autocomplete(query=song_title_autocomplete)
    @logged_app_command
    async def scores_slash(
        self,
        interaction: discord.Interaction["ChuniBot"],
        query: app_commands.Transform[str, AliasNameTransformer(lower=True)],
        user: discord.User | discord.Member | None = None,
        *,
        kamaitachi: bool = False,
    ):
        ctx = await PenguinContext.from_interaction(interaction)

        await self._scores_inner(ctx, query, user, kamaitachi=kamaitachi)


async def setup(bot: "ChuniBot"):
    cog = ScoreQueriesCog(bot)
    try:
        await bot.add_cog(cog)
    except BaseException:
        await cog.cog_unload()
        raise
