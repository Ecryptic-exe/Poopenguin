"""Small public slash surface; original commands remain callable by the UI."""

import discord
from discord import app_commands

from chuni_penguin.context import PenguinContext

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
    from .panel import show_panel
    from .processing import processing_response

    await interaction.response.defer(thinking=True)
    ctx = await PenguinContext.from_interaction(interaction)
    async with processing_response(ctx, "開啟介面"):
        await show_panel(ctx, section)


@app_commands.command(name="account", description="帳號狀態、連結、同步與個人紀錄")
async def account_entry(interaction: discord.Interaction):
    await open_entry(interaction, "account")


async def open_song_entry(interaction, query, *, section, tab):
    from .panel import session_for, show_song
    from .processing import processing_response

    if not query:
        await open_entry(interaction, section)
        return
    await interaction.response.defer(thinking=True)
    ctx = await PenguinContext.from_interaction(interaction)
    session_for(ctx).parameters.clear()
    async with processing_response(ctx, "搜尋歌曲"):
        await show_song(ctx, query, tab=tab)


@app_commands.command(name="chart", description="搜尋歌曲並開啟視譜")
async def chart_entry(interaction: discord.Interaction, query: str | None = None):
    await open_song_entry(interaction, query, section="chart", tab="view")


@app_commands.command(name="score", description="搜尋歌曲並開啟個人成績")
async def score_entry(interaction: discord.Interaction, query: str | None = None):
    await open_song_entry(interaction, query, section="score", tab="scores")


@chart_entry.autocomplete("query")
async def chart_query_complete(interaction: discord.Interaction, current: str):
    from .autocomplete import song_title_choices

    return song_title_choices(
        interaction.client.services.autocomplete, current, interaction.guild_id
    )


score_entry.autocomplete("query")(chart_query_complete)


@app_commands.command(name="tools", description="Rating、定數、容錯與餡蜜計算")
async def tools_entry(interaction: discord.Interaction):
    await open_entry(interaction, "tools")


@app_commands.command(
    name="compare", description="依原版方式，查看最近成績訊息對應的最佳成績"
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
