"""Alias management domain; existing policies and shared cache remain intact."""

from typing import TYPE_CHECKING, Annotated, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from discord.utils import escape_markdown as emd

from chuni_penguin.context import PenguinContext
from chuni_penguin.converters import AliasNameConverter
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.ui.autocomplete import song_title_choices

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class AliasesCog(commands.Cog, name="Aliases"):
    def __init__(self, bot: "ChuniBot") -> None:
        self.bot = bot
        self.settings = bot.settings
        self.autocomplete = bot.services.autocomplete
        self.aliases = bot.services.utils.aliases
        self._hoisted_commands: list[commands.Command] = []

    @override
    async def cog_load(self) -> None:
        hoist_commands = [
            (self.addalias, ("addalias",)),
            (self.removealias, ("removealias",)),
            (self.listalias, ("listalias", "listaliases", "aliases")),
            (self.reloadalias, ("reloadalias",)),
        ]

        try:
            for command, name_and_aliases in hoist_commands:
                name, *aliases = name_and_aliases

                new_command = command.copy()
                new_command.name = name
                new_command.parent = None
                new_command.cog = self
                new_command.hidden = True

                if len(aliases) > 0:
                    new_command.aliases = aliases

                new_command.app_command = None

                self.bot.add_command(new_command)
                self._hoisted_commands.append(new_command)
        except BaseException:
            # discord.py calls cog_load before its own registration rollback.
            await self.cog_unload()
            raise

    @override
    async def cog_unload(self) -> None:
        # Native remove_cog also needs to remove copies outside the alias group.
        for command in self._hoisted_commands:
            if self.bot.get_command(command.name) is command:
                self.bot.remove_command(command.name)
        self._hoisted_commands.clear()

    async def _song_title_autocomplete(
        self,
        interaction: "discord.Interaction[ChuniBot]",
        current: str,
    ):
        return song_title_choices(self.autocomplete, current, interaction.guild_id)

    @commands.hybrid_group("alias")
    @logged_prefix_command
    async def alias(self, ctx: Context):
        await ctx.send_help(ctx.command)

    @alias.command("add")
    @app_commands.autocomplete(song_title_or_alias=_song_title_autocomplete)
    @logged_prefix_command
    async def addalias(
        self,
        ctx: Context,
        song_title_or_alias: Annotated[str, AliasNameConverter],
        added_alias: Annotated[str, AliasNameConverter],
        *,
        global_alias: bool = False,
    ):
        """Manually add a song alias for this server.

        Aliases are case-insensitive.

        Parameters
        ----------
        song_title_or_alias: str
            The title (or an existing alias) of the song.
        added_alias: str
            The alias to add.
        global_alias: bool
            Whether to apply the alias globally. Only a few users can do this.

        Examples
        --------
        addalias Titania tritania
        addalias "祈 -我ら神祖と共に歩む者なり-" prayer
        """

        # this command is guild-only
        if not global_alias and ctx.guild is None:
            raise commands.NoPrivateMessage

        is_alias_manager = (
            ctx.author.id in self.settings.bot.alias_managers
            or ctx.author.id == self.bot.owner_id
        )

        if global_alias and not is_alias_manager:
            msg = "You are not allowed to add global aliases."
            raise commands.CheckFailure(msg)

        added_alias_lower = added_alias.lower()

        if global_alias:
            guild_id = 0
        elif ctx.guild is not None:
            guild_id = ctx.guild.id
        else:
            msg = "ctx.guild == None and global_alias == True"
            raise RuntimeError(msg)

        async with ctx.typing():
            song = await self.aliases.resolve_add_source(
                song_title_or_alias,
                added_alias,
                guild_id=guild_id,
                global_alias=global_alias,
            )

            if global_alias:
                aliases = await self.aliases.global_conflicts(added_alias_lower)

                if len(aliases) > 0 and aliases[0].guild_id == 0:
                    msg = f"**{emd(added_alias)}** already exists (global alias for **{emd(aliases[0].song.title)}**)."
                    raise commands.BadArgument(msg)

                if len(aliases) > 0 and aliases[0].guild_id != 0:
                    await self.aliases.promote_to_global(aliases, song.id)
                    return await ctx.reply(
                        f"**{emd(added_alias)}** already exists as a guild-only alias. Promoting to global alias.",
                        mention_author=False,
                    )
                await self.aliases.create_alias(
                    added_alias,
                    guild_id=guild_id,
                    song_id=song.id,
                    owner_id=None,
                )
            else:
                from chuni_penguin.services.alias_learning import AliasLearningService

                try:
                    result = await AliasLearningService(
                        self.bot.database, self.aliases
                    ).confirm(
                        added_alias,
                        guild_id=guild_id,
                        song_id=song.id,
                        owner_id=ctx.author.id,
                    )
                except ValueError as error:
                    raise commands.BadArgument(
                        "別名格式不合適，請使用 1–100 字的一般文字。"
                    ) from error
                if result.status != "added":
                    titles = "、".join(
                        dict.fromkeys(emd(c.title) for c in result.conflicts)
                    )
                    raise commands.BadArgument(
                        f"這個名稱已存在或與歌曲名稱衝突：{titles[:1000]}。請換個別名。"
                    )

        alias_unit = "an alias" if not global_alias else "a global alias"

        await ctx.reply(
            f"Added **{emd(added_alias)}** as {alias_unit} for **{emd(song_title_or_alias)}**.",
            mention_author=False,
        )
        return None

    @alias.command("remove", aliases=["delete"])
    @logged_prefix_command
    async def removealias(
        self,
        ctx: Context,
        *,
        removed_alias: Annotated[str, AliasNameConverter],
    ):
        """Remove an alias for this server.

        The alias owner can always delete their own aliases. If someone
        has the Manage Server permissions then they can also delete it.

        Parameters
        ----------
        alias: str
            The alias to remove.
        """

        is_alias_manager = (
            ctx.author.id in self.settings.bot.alias_managers
            or ctx.author.id == self.bot.owner_id
        )

        if not is_alias_manager and ctx.guild is None:
            raise commands.NoPrivateMessage

        # If the person is not an alias manager, we already know that this
        # command must be run in a guild.
        bypass_ownership_check: bool = (
            is_alias_manager or ctx.author.guild_permissions.manage_guild  # pyright: ignore[reportAttributeAccessIssue]
        )

        async with ctx.typing():
            alias = await self.aliases.removable_alias(
                removed_alias.lower(),
                is_alias_manager=is_alias_manager,
                guild_id=ctx.guild.id if ctx.guild is not None else None,
                author_id=ctx.author.id,
                bypass_ownership_check=bypass_ownership_check,
            )

            if alias is None:
                msg = f"**{emd(removed_alias)}** does not exist"

                if not bypass_ownership_check:
                    msg += " or you don't have permissions to remove it"

                msg += "."

                raise commands.CommandError(msg)

            # if there is a suitable alias, then we can definitely remove it, since we
            # have already matched all of the conditions above.
            await self.aliases.delete_alias(alias)

        await ctx.reply(
            f"Removed {'global ' if alias.guild_id == 0 else ''}alias **{emd(removed_alias)}**.",
            mention_author=False,
        )

    @alias.command("list")
    @logged_prefix_command
    async def listalias(
        self,
        ctx: PenguinContext,
        *,
        query: Annotated[str, AliasNameConverter(lower=True)],
    ):
        """List aliases for a given song

        Parameters
        ----------
        query: str
            The song to get aliases for. You don't have to be exact; this works
            the same way as `c>info`.
        """
        song, _, _ = await ctx.find_song(query)

        if song is None:
            return

        aliases = await self.aliases.for_song(song.id)

        embed = discord.Embed(
            title=f"Aliases for {song.title}",
            color=discord.Color.yellow(),
        )
        embed.description = ""
        global_aliases = [x.alias for x in aliases if x.guild_id == 0]

        if len(global_aliases) > 0:
            embed.description += (
                "**Global aliases:**\n"
                f"{', '.join([x.alias for x in aliases if x.guild_id == 0])}"
            )

        if ctx.guild is not None:
            local_aliases = [x.alias for x in aliases if x.guild_id == ctx.guild.id]

            if len(local_aliases) > 0:
                embed.description += (
                    "\n\n"
                    "**Local aliases:**\n"
                    f"{', '.join([x.alias for x in aliases if x.guild_id == ctx.guild.id])}"
                )

        embed.description = embed.description.strip()

        await ctx.reply(embed=embed, mention_author=False)

    @alias.command("reload", with_app_command=False, hidden=True)
    @commands.is_owner()
    @logged_prefix_command
    async def reloadalias(self, ctx: Context):
        async with ctx.typing():
            await self.aliases.reload()

            alias_count = sum([len(x) for x in self.aliases.alias_cache.values()])

            await ctx.reply(
                content=f"Loaded {alias_count} aliases into memory.",
                mention_author=False,
            )


async def setup(bot: "ChuniBot"):
    await bot.add_cog(AliasesCog(bot))
