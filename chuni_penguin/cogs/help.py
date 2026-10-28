import itertools
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Command, Group, GroupMixin
from discord.ext.commands.core import hooked_wrapped_callback
from rapidfuzz import fuzz, process

from chuni_penguin.constants import SIMILARITY_THRESHOLD
from chuni_penguin.context import PenguinContext

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class HelpCommand(commands.HelpCommand):
    COLOUR = discord.Colour.yellow()

    @property
    def prefix(self):
        return self.context.clean_prefix

    @override
    async def send_error_message(self, error: str, /) -> None:
        await self.context.reply(
            embed=discord.Embed(
                color=discord.Color.red(),
                title="Error",
                description=error,
            ),
            mention_author=False,
        )

    async def send_bot_help(
        self, mapping: Mapping[Optional[Cog], List[Command[Any, ..., Any]]], /
    ) -> None:
        from chuni_penguin.ui.help_manual import show_help_manual

        visible_commands = []
        for command_list in mapping.values():
            visible_commands.extend(
                await self.filter_commands(command_list, sort=True)
            )
        await show_help_manual(self.context, visible_commands)

    async def send_command_help(self, command: Command[Any, ..., Any], /) -> None:
        from chuni_penguin.ui.help_manual import (
            build_command_page,
            get_manual_language,
        )

        language = get_manual_language(self.context)
        children = None
        if isinstance(command, Group):
            children = await self.filter_commands(command.commands, sort=True)
        embed = build_command_page(
            self.context, command, page_number=1, page_count=1,
            language=language, children=children,
        )
        await self.context.reply(embed=embed, mention_author=False)

    @override
    async def send_group_help(self, group: Group[Any, ..., Any], /) -> None:
        from chuni_penguin.ui.help_manual import (
            build_command_page,
            get_manual_language,
        )

        language = get_manual_language(self.context)
        children = await self.filter_commands(group.commands, sort=True)
        embed = build_command_page(
            self.context, group, page_number=1, page_count=1,
            language=language, children=children,
        )
        await self.context.reply(embed=embed, mention_author=False)


async def help_command_autocomplete(
    interaction: discord.Interaction["ChuniBot"], current: str
) -> list[app_commands.Choice[str]]:
    commands = itertools.chain(
        *[cog.get_commands() for cog in interaction.client.cogs.values()]
    )
    command_names = [c.qualified_name for c in commands if not c.hidden]

    if len(current) < 2:
        return [app_commands.Choice(name=c, value=c) for c in command_names[:25]]

    if (command := interaction.client.get_command(current)) is not None:
        choices = [
            app_commands.Choice(
                name=command.qualified_name, value=command.qualified_name
            )
        ]

        if isinstance(command, GroupMixin):
            choices.extend(
                app_commands.Choice(name=c.qualified_name, value=c.qualified_name)
                for c in command.commands
            )

        if len(choices) > 25:
            choices = choices[:25]

        return choices

    results = process.extract(
        current,
        command_names,
        scorer=fuzz.QRatio,
        limit=50,
        score_cutoff=SIMILARITY_THRESHOLD,
    )

    return [app_commands.Choice(name=c, value=c) for c, _, _ in results[:25]]


@app_commands.command(
    name="help",
    description="Display the list of commands, or help for a specific command.",
)
@app_commands.describe(command="The command to show help for.")
@app_commands.autocomplete(command=help_command_autocomplete)
async def help_slash(
    interaction: discord.Interaction["ChuniBot"], command: str | None = None
):
    if command is None:
        from chuni_penguin.context import PenguinContext
        from chuni_penguin.ui.help_manual import show_help_manual

        await interaction.response.defer(thinking=True)
        ctx = await PenguinContext.from_interaction(interaction)
        await show_help_manual(ctx)
        return
    if (help_command := interaction.client.help_command) is None:
        await interaction.response.send_message(
            content="An internal error has occured.", ephemeral=True
        )
        return

    interaction._baton = ctx = await interaction.client.get_context(interaction)
    help_command = help_command.copy()
    help_command.context = ctx
    injected = hooked_wrapped_callback(
        help_command._command_impl, ctx, help_command.command_callback
    )

    try:
        await injected(ctx, command=command)
    except commands.errors.CommandError as e:
        await help_command._command_impl.dispatch_error(ctx, e)


@commands.hybrid_command(name="menu", description="Open the main menu.")
async def menu_command(ctx: PenguinContext):
    """Open the main menu."""
    from chuni_penguin.ui.navigation import show_main_menu

    await ctx.defer()
    await show_main_menu(ctx)


async def setup(bot: "ChuniBot"):
    bot.help_command = HelpCommand()
    bot.tree.add_command(help_slash)
    bot.add_command(menu_command.copy())


async def teardown(bot: "ChuniBot"):
    bot.remove_command("menu")
    bot.tree.remove_command("menu")
    bot.help_command = None
    bot.tree.remove_command("help")
