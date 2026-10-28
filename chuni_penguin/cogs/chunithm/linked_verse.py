from typing import TYPE_CHECKING, Annotated, cast

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from chuni_penguin.config import config
from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import LinkedGateConverter
from chuni_penguin.database import LinkedGateCondition
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.services.linked_verse import format_gate_condition
from chuni_penguin.types import (
    LinkedGate,
    LinkedGateStatus,
)
from chuni_penguin.ui import LinkedGateLeaderboardView, SongInfoEmbed
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


def format_condition(condition: LinkedGateCondition):
    """Compatibility entry; commands use explicit settings through the shared helper."""
    return format_gate_condition(condition, config.icons)


class LinkedVerse(commands.Cog, name="Linked VERSE"):
    def __init__(self, bot: "ChuniBot") -> None:
        self.bot = bot
        self.accounts = bot.services.accounts
        self.settings = bot.settings
        self.linked_verse_repository = bot.services.utils.linked_verse

    async def cog_load(self) -> None:
        results = await self.linked_verse_repository.choices()

        app_commands.choices(
            gate=[
                app_commands.Choice(name=name, value=str(id)) for id, name, _ in results
            ]
        )(self.linked_verse_gate.app_command)
        app_commands.choices(
            gate=[
                app_commands.Choice(name=name, value=str(id))
                for id, name, available in results
                if available
            ]
        )(self.linked_verse_leaderboard.app_command)

    async def get_linked_gates(self, whereclause):
        return await self.linked_verse_repository.gates(whereclause)

    async def get_linked_gate(self, gate: LinkedGate):
        db_gates = await self.linked_verse_repository.by_id(gate.value)

        if not db_gates:
            msg = f"{gate} has not been released yet."
            raise commands.CommandError(msg)

        return db_gates[0]

    @commands.hybrid_group("linked-verse", aliases=["linkedverse", "lv"])
    @logged_prefix_command
    async def linked_verse(self, ctx: PenguinContext):
        await ctx.send_help(ctx.command)

    @linked_verse.command("gate", aliases=["info"])
    @app_commands.describe(gate="The Linked GATE to view information for.")
    @logged_prefix_command
    async def linked_verse_gate(
        self, ctx: PenguinContext, gate: Annotated[LinkedGate, LinkedGateConverter]
    ):
        """Get information about a Linked GATE."""
        db_gate = await self.get_linked_gate(gate)
        info_embed = discord.Embed(
            color=discord.Color.from_str(db_gate.color), title=db_gate.name
        )

        if self.settings.web.is_accessible:
            info_embed.set_thumbnail(
                url=f"{self.settings.web.base_url}/assets/jackets/linked_gate_{LinkedGate(db_gate.id).name}.webp"
            )

        jp_condition = next((c for c in db_gate.conditions if c.region == "jp"), None)

        if jp_condition is not None:
            info_embed.add_field(
                name="Link LEVEL (JP)",
                value=format_gate_condition(jp_condition, self.settings.icons),
                inline=False,
            )

        if db_gate.available:
            intl_condition = next(
                (c for c in db_gate.conditions if c.region == "intl"), None
            )

            if intl_condition is not None:
                info_embed.add_field(
                    name="Link LEVEL (INTL)",
                    value=format_gate_condition(intl_condition, self.settings.icons),
                    inline=False,
                )
        else:
            info_embed.description = (
                "**This gate is not available in CHUNITHM International.**"
            )

        if db_gate.open_condition:
            info_embed.add_field(
                name="How to discover", value=db_gate.open_condition, inline=False
            )

        if db_gate.unlock_condition:
            info_embed.add_field(
                name="How to unlock", value=db_gate.unlock_condition, inline=False
            )

        view = discord.ui.View()

        if gate == LinkedGate.origin:
            view.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label="Song list",
                    url="https://docs.google.com/spreadsheets/d/1j7kmCR0-R5W3uivwkw-6A_eUCXttnJLnkTO0Qf7dya0/edit?gid=921326529#gid=921326529",
                )
            )

        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link,
                label="Story",
                url=f"https://www.chunithmstory.com/linked-verse/gate-{gate.name.replace('_', '-')}",
            )
        )

        await ctx.respond_or_edit(
            embeds=[
                info_embed,
                SongInfoEmbed(
                    db_gate.song,
                    db_gate.song.charts,
                    synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                    language=get_chunithm_language(ctx),
                ),
            ],
            view=view,
        )

    @linked_verse.command("progress", aliases=["status"])
    @app_commands.describe(
        user="The user to view the Linked GATE progress for. Yourself, if not specified."
    )
    @logged_prefix_command
    async def linked_verse_progress(
        self, ctx: PenguinContext, user: discord.User | discord.Member | None = None
    ):
        """Get a player's Linked GATE progress."""

        # TODO: Track ORIGIN/PARADISE completion based on last play date in the
        # personal_bests table

        target_id = ctx.author.id if user is None else user.id

        async with ctx.typing():
            async with self.accounts.network(ctx, target_id) as client:
                try:
                    progress = await client.get_linked_verse_progress()
                except NotImplementedError:
                    msg = f"Network {client.NAME} does not support retrieving Linked VERSE progress."
                    raise commands.CommandError(msg) from None

                profile = await client.get_minimal_profile()

            icons: list[str | None] = [
                self.settings.icons.icon(f"linked_gate_{gate.name}_{status.name}")
                if status in (LinkedGateStatus.linkable, LinkedGateStatus.clear)
                else self.settings.icons.icon(f"linked_gate_{status.name}")
                for gate, status in progress.items()
            ]

            embed = discord.Embed(
                color=discord.Color.blue(),
                title=f"{escape_markdown(profile.username)}'s Linked VERSE",
            )

            if all(
                status == LinkedGateStatus.not_found for status in progress.values()
            ):
                embed.description = "...hel..lo... can... you... hea...r me?..."
            elif any(icon is None for icon in icons):
                cleared = [
                    str(gate)
                    for gate, status in progress.items()
                    if status == LinkedGateStatus.clear
                ]
                linkable = [
                    str(gate)
                    for gate, status in progress.items()
                    if status == LinkedGateStatus.linkable
                ]
                under_analysis = [
                    str(gate)
                    for gate, status in progress.items()
                    if status == LinkedGateStatus.under_analysis
                ]
                embed.description = ""

                if len(cleared) > 0:
                    embed.description += f"- Clear: {', '.join(cleared)}\n"

                if len(linkable) > 0:
                    embed.description += f"- Linkable: {', '.join(linkable)}\n"

                if len(under_analysis) > 0:
                    embed.description += (
                        f"- Under analysis: {', '.join(under_analysis)}\n"
                    )
            else:
                embed.description = " ".join(cast(list[str], icons))

        await ctx.respond_or_edit(embed=embed)

    @linked_verse.command("leaderboard", aliases=["lb"])
    @app_commands.describe(gate="The Linked GATE to view the leaderboard for.")
    @logged_prefix_command
    async def linked_verse_leaderboard(
        self, ctx: PenguinContext, gate: Annotated[LinkedGate, LinkedGateConverter]
    ):
        """Get the clear leaderboard for a specific gate."""
        db_gate = await self.get_linked_gate(gate)

        if not db_gate.available:
            msg = (
                f"{LinkedGate(db_gate.id)} is not available in CHUNITHM International."
            )
            raise commands.CommandError(msg)

        async with ctx.typing():
            async with self.accounts.bot_network() as client:
                try:
                    leaderboard = await client.get_linked_gate_leaderboard(gate)
                except NotImplementedError:
                    msg = f"Network {client.NAME} does not support retrieving Linked GATE leaderboards."
                    raise commands.CommandError(msg) from None

            view = LinkedGateLeaderboardView(
                ctx,
                leaderboard,
                db_gate.song,
                discord.Color.from_str(db_gate.color),
                synthesis_alt_jacket=ctx.user_config.synthesis_alt_jacket,
                network=client.NAME,
            )
            await view.start()


async def setup(bot: "ChuniBot"):
    await bot.add_cog(LinkedVerse(bot))
