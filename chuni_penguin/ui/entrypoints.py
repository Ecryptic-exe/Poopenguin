"""Small public slash surface; original commands remain callable by the UI."""

import discord
from discord import app_commands
from discord.ext import commands

from chuni_penguin.context import PenguinContext
from chuni_penguin.utils.chunithm_i18n import ui_text

MOVED_ROOTS = frozenset(
    {
        "account",
        "alias",
        "anmitsu",
        "b50",
        "border",
        "calculate",
        "chart",
        "chartview",
        "compare",
        "const",
        "find",
        "login",
        "logout",
        "privacy",
        "rating",
        "recent",
        "score",
        "sync",
        "tags",
        "token",
    }
)


def panel_command(bot, name):
    return (
        getattr(bot, "panel_commands", {}).get(name)
        or bot.get_command(name)
        or bot.tree.get_command(name)
    )


async def open_entry(interaction, section):
    await interaction.response.defer(thinking=True)
    ctx = await PenguinContext.from_interaction(interaction)
    return await show_entry(ctx, section)


async def show_entry(ctx, section):
    from .panel import show_panel
    from .processing import processing_response

    async with processing_response(ctx, ui_text(ctx, "開啟介面")):
        return await show_panel(ctx, section)


@app_commands.command(
    name="account", description="Account status, links, syncing and personal records"
)
async def account_entry(interaction: discord.Interaction):
    await open_entry(interaction, "account")


async def open_song_entry(interaction, query, *, section, tab):
    await interaction.response.defer(thinking=True)
    ctx = await PenguinContext.from_interaction(interaction)
    return await show_song_entry(ctx, query, section=section, tab=tab)


async def show_song_entry(ctx, query, *, section, tab):
    from .panel import session_for, show_song
    from .processing import processing_response

    if not query:
        return await show_entry(ctx, section)
    session_for(ctx).parameters.clear()
    async with processing_response(ctx, ui_text(ctx, "搜尋歌曲")):
        return await show_song(ctx, query, tab=tab)


@app_commands.command(
    name="chart", description="Search for a song and open its chart view"
)
async def chart_entry(interaction: discord.Interaction, query: str | None = None):
    await open_song_entry(interaction, query, section="chart", tab="view")


@app_commands.command(
    name="score", description="Search for a song and view your scores"
)
async def score_entry(interaction: discord.Interaction, query: str | None = None):
    await open_song_entry(interaction, query, section="score", tab="scores")


@chart_entry.autocomplete("query")
async def chart_query_complete(interaction: discord.Interaction, current: str):
    from .autocomplete import song_title_choices

    return song_title_choices(
        interaction.client.services.autocomplete, current, interaction.guild_id
    )


score_entry.autocomplete("query")(chart_query_complete)


@app_commands.command(
    name="tools", description="Rating, chart constant, border and anmitsu calculators"
)
async def tools_entry(interaction: discord.Interaction):
    await open_entry(interaction, "tools")


@commands.command(name="tools")
async def tools_prefix(ctx: PenguinContext):
    """Open the same calculator panel as /tools."""
    await show_entry(ctx, "tools")


@commands.command(name="account")
async def account_prefix(ctx: PenguinContext):
    """Open the same account panel as /account."""
    await show_entry(ctx, "account")


@app_commands.command(
    name="compare", description="Compare personal bests with recent score messages"
)
async def compare_entry(
    interaction: discord.Interaction,
    user: discord.User | None = None,
    kamaitachi: bool = False,
):
    from chuni_penguin.cogs.chunithm.score_queries import ScoreQueriesCog

    ctx = await PenguinContext.from_interaction(interaction)
    native = getattr(
        interaction.client, "panel_score_queries", None
    ) or ScoreQueriesCog(interaction.client)
    await native._compare_inner(ctx, user, kamaitachi=kamaitachi)


def configure_entrypoints(bot):
    """Only alters the local tree. Calling Discord sync is a separate action."""
    cached = getattr(bot, "panel_commands", {})
    for command in list(bot.tree.get_commands()):
        if command.name not in MOVED_ROOTS:
            continue
        if isinstance(command, app_commands.Command) and command.callback in (
            account_entry.callback,
            chart_entry.callback,
            score_entry.callback,
            compare_entry.callback,
        ):
            continue
        cached[command.name] = command
        if isinstance(command, app_commands.Group):
            for child in command.walk_commands():
                cached[child.qualified_name] = child
        bot.tree.remove_command(command.name)
    bot.panel_commands = cached
    for command in (tools_prefix, account_prefix):
        if bot.get_command(command.name) is None:
            bot.add_command(command.copy())
    for command in (
        account_entry,
        chart_entry,
        score_entry,
        tools_entry,
        compare_entry,
    ):
        bot.tree.add_command(command, override=True)
    if bot.get_cog("V2Players") is not None:
        from chuni_penguin.cogs.chunithm.score_queries import ScoreQueriesCog

        native = bot.get_cog("ScoreQueries") or ScoreQueriesCog(bot)
        bot.panel_score_queries = native
        if bot.get_command("compare") is None:
            prefix = native.compare.copy()
            prefix.cog = native
            bot.add_command(prefix)
        bot.tree.add_command(native.compare_context_menu, override=True)
