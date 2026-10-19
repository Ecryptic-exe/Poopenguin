import contextlib
from typing import TYPE_CHECKING, Annotated

import discord
from discord.ext import commands
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chuni_penguin.context import PenguinContext, PenguinGuildContext
from chuni_penguin.converters import CommandOrGroupConverter
from chuni_penguin.database import CommandPermission, Denylist
from chuni_penguin.database.guilds import (
    PrimaryPermissionTarget,
)
from chuni_penguin.ui.permissions import PermissionListView

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class PermissionsCog(commands.Cog, name="Permissions"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.service = bot.services.permissions
        self.service.assert_ready()

    @commands.hybrid_group("permissions", aliases=["perms", "permission", "perm"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions(self, ctx: PenguinContext):
        """Enable or disable text commands for the server, or a role, channel or member.

        This will also work with *most* slash commands, however for more precise control you should check out [dedicated slash command permissions](https://support-apps.discord.com/hc/en-us/articles/26501869403159-Command-Permissions), which also has the benefit that disallowed commands will not show up in users' command list.

        The permission system works by going down the permission list (see `$PREFIXpermissions list`) and returning on the first rule that matches the current invocation (based on server, role, channel or member). Therefore, it is important that your permissions are ordered correctly (see `$PREFIXpermissions move`).

        Members with the Manage Server permission can edit command permissions and are always able to run commands.
        """

        await self.permissions_list.invoke(ctx)

    @permissions.command("list", aliases=["ls"])  # pyright: ignore[reportFunctionMemberAccess]
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_list(self, ctx: PenguinGuildContext):
        """List the command permissions for this server."""

        perms = self.service.permission_cache.get(ctx.guild.id, [])

        if len(perms) <= 0:
            await ctx.respond_or_edit("This server has no command permissions set up.")
            return

        view = PermissionListView(ctx, perms)
        await view.start()

    @permissions.command("move", aliases=["mv"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_move(
        self, ctx: PenguinGuildContext, source: int, destination: int
    ):
        """Move a permission from one position to another in the list."""

        if source == destination:
            msg = "Cannot move a permission to the same position."
            raise commands.BadArgument(msg)

        if source < 1 or destination < 1:
            msg = "Source or destination cannot be smaller than 1."
            raise commands.BadArgument(msg)

        source_idx = source - 1
        destination_idx = destination - 1

        async def transaction(session: AsyncSession):
            query = select(CommandPermission).where(
                (CommandPermission.guild_id == ctx.guild.id)
                & (CommandPermission.index == source_idx)
            )
            perm = (await session.execute(query)).scalar_one_or_none()

            if perm is None:
                msg = f"There are no permissions at position {source}."
                raise commands.BadArgument(msg)

            query = select(func.max(CommandPermission.index)).where(
                CommandPermission.guild_id == ctx.guild.id
            )
            max_index = (await session.execute(query)).scalar_one()

            if destination_idx > max_index:
                msg = f"Cannot move permission to position {destination}, there are only {max_index + 1} permissions."
                raise commands.BadArgument(msg)

            # Remove permission at source_idx, so everything behind it is shifted up
            # 1 index
            query = (
                update(CommandPermission)
                .values(index=CommandPermission.index - 1)
                .where(
                    (CommandPermission.guild_id == ctx.guild.id)
                    & (CommandPermission.index > source_idx)
                    & (CommandPermission.id != perm.id)
                )
            )
            await session.execute(query)

            # Add it back at destination_idx, so everything behind it is shifted down
            # 1 index
            query = (
                update(CommandPermission)
                .values(index=CommandPermission.index + 1)
                .where(
                    (CommandPermission.guild_id == ctx.guild.id)
                    & (CommandPermission.index >= destination_idx)
                    & (CommandPermission.id != perm.id)
                )
            )
            await session.execute(query)

            # Move source position to new place
            query = (
                update(CommandPermission)
                .values(index=destination_idx)
                .where(CommandPermission.id == perm.id)
            )
            await session.execute(query)

        async with ctx.typing():
            await self.bot.database.writer.execute_fn(transaction)
            await self.service.load_permissions(ctx.guild.id)

        await ctx.respond_or_edit(
            f"Moved permission at position {source} to position {destination}."
        )

    @permissions.command("delete", aliases=["remove", "rm", "del"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_delete(self, ctx: PenguinGuildContext, position: int):
        """Deletes the permission at the given position."""

        if position < 1:
            msg = "Position cannot be smaller than 1."
            raise commands.BadArgument(msg)

        position_idx = position - 1

        async def transaction(session: AsyncSession):
            query = delete(CommandPermission).where(
                (CommandPermission.guild_id == ctx.guild.id)
                & (CommandPermission.index == position_idx)
            )
            await session.execute(query)

            # This should be a no-op if the perm at position_idx doesn't exist, since
            # index is contiguous and non-negaative.
            query = (
                update(CommandPermission)
                .values(index=CommandPermission.index - 1)
                .where(
                    (CommandPermission.guild_id == ctx.guild.id)
                    & (CommandPermission.index > position_idx)
                )
            )
            await session.execute(query)
            await session.commit()

        async with ctx.typing():
            await self.bot.database.writer.execute_fn(transaction)
            await self.service.load_permissions(ctx.guild.id)

        await ctx.respond_or_edit(f"Deleted permission at position {position}.")

    @permissions.command("reset")
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_reset(self, ctx: PenguinGuildContext):
        """Resets all permissions for this server."""

        query = delete(CommandPermission).where(
            CommandPermission.guild_id == ctx.guild.id
        )
        await self.bot.database.writer.execute(query)

        self.service.permission_cache[ctx.guild.id] = []

        await ctx.respond_or_edit("Permissions for this server have been reset.")

    @permissions.command("server", aliases=["guild", "s", "g"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_server(
        self,
        ctx: PenguinGuildContext,
        command_or_group: Annotated[
            commands.Command | commands.Cog, CommandOrGroupConverter
        ],
        *,
        is_allowed: bool,
    ):
        """Enable or disable a command or group at the server level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.guild,
                ctx.guild.id,
                command_or_group,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"
        command_group = (
            "command" if isinstance(command_or_group, commands.Command) else "group"
        )

        await ctx.respond_or_edit(
            f"{enable_disable} the {command_group} `{command_or_group.qualified_name}` for this server."
        )

    @permissions.command("serverall", aliases=["guildall", "sa", "ga"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_server_all(
        self, ctx: PenguinGuildContext, *, is_allowed: bool
    ):
        """Enable or disable all commands at the server level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.guild,
                ctx.guild.id,
                None,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"

        await ctx.respond_or_edit(f"{enable_disable} all commands for this server.")

    @permissions.command("role", aliases=["r"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_role(
        self,
        ctx: PenguinGuildContext,
        command_or_group: Annotated[
            commands.Command | commands.Cog, CommandOrGroupConverter
        ],
        role: discord.Role,
        *,
        is_allowed: bool,
    ):
        """Enable or disable a command or group at the role level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.role,
                role.id,
                command_or_group,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"
        command_group = (
            "command" if isinstance(command_or_group, commands.Command) else "group"
        )

        await ctx.respond_or_edit(
            f"{enable_disable} the {command_group} `{command_or_group.qualified_name}` for the role {role.mention}."
        )

    @permissions.command("roleall", aliases=["ra"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_role_all(
        self, ctx: PenguinGuildContext, role: discord.Role, *, is_allowed: bool
    ):
        """Enable or disable all commands at the role level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.role,
                role.id,
                None,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"

        await ctx.respond_or_edit(
            f"{enable_disable} all commands for the role {role.mention}."
        )

    @permissions.command("channel", aliases=["ch", "c"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_channel(
        self,
        ctx: PenguinGuildContext,
        command_or_group: Annotated[
            commands.Command | commands.Cog, CommandOrGroupConverter
        ],
        channel: discord.abc.GuildChannel,
        *,
        is_allowed: bool,
    ):
        """Enable or disable a command or group at the channel level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.channel,
                channel.id,
                command_or_group,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"
        command_group = (
            "command" if isinstance(command_or_group, commands.Command) else "group"
        )

        await ctx.respond_or_edit(
            f"{enable_disable} the {command_group} `{command_or_group.qualified_name}` for the channel {channel.mention}."
        )

    @permissions.command("channelall", aliases=["ca", "cha"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_channel_all(
        self,
        ctx: PenguinGuildContext,
        channel: discord.abc.GuildChannel,
        *,
        is_allowed: bool,
    ):
        """Enable or disable all commands at the channel level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.channel,
                channel.id,
                None,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"

        await ctx.respond_or_edit(
            f"{enable_disable} all commands for the channel {channel.mention}."
        )

    @permissions.command("member", aliases=["user", "m", "u"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_member(
        self,
        ctx: PenguinGuildContext,
        command_or_group: Annotated[
            commands.Command | commands.Cog, CommandOrGroupConverter
        ],
        member: discord.Member,
        *,
        is_allowed: bool,
    ):
        """Enable or disable a command or group at the member level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.user,
                member.id,
                command_or_group,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"
        command_group = (
            "command" if isinstance(command_or_group, commands.Command) else "group"
        )

        await ctx.respond_or_edit(
            f"{enable_disable} the {command_group} `{command_or_group.qualified_name}` for the member {member.mention}."
        )

    @permissions.command("memberall", aliases=["userall", "ma", "ua"])
    @commands.has_guild_permissions(manage_guild=True)
    async def permissions_member_all(
        self,
        ctx: PenguinGuildContext,
        member: discord.Member,
        *,
        is_allowed: bool,
    ):
        """Enable or disable all commands at the member level."""

        async with ctx.typing():
            await self.service.add_permission(
                ctx.guild.id,
                PrimaryPermissionTarget.user,
                member.id,
                None,
                is_allowed=is_allowed,
            )

        enable_disable = "Enabled" if is_allowed else "Disabled"

        await ctx.respond_or_edit(
            f"{enable_disable} all commands for the member {member.mention}."
        )

    @commands.command("block")
    @commands.is_owner()
    async def block(
        self, ctx: PenguinContext, object: discord.Object, *, reason: str | None = None
    ):
        """Blocks users or guilds from using the bot globally."""

        query = (
            insert(Denylist)
            .values(object_id=object.id, reason=reason)
            .on_conflict_do_update(
                index_elements=[Denylist.object_id], set_={"reason": reason}
            )
            .returning(Denylist)
        )
        result = await self.bot.database.writer.execute(query)
        self.service.denylist[object.id] = result.scalar_one()

        await ctx.message.add_reaction("✅")

    @commands.command("unblock")
    @commands.is_owner()
    async def unblock(
        self, ctx: PenguinContext, objects: commands.Greedy[discord.Object]
    ):
        """Unblocks users or guilds from using the bot globally."""

        query = delete(Denylist).where(
            Denylist.object_id.in_([object.id for object in objects])
        )
        await self.bot.database.writer.execute(query)

        for object in objects:
            with contextlib.suppress(KeyError):
                del self.service.denylist[object.id]

        await ctx.message.add_reaction("✅")


async def setup(bot: "ChuniBot"):
    await bot.add_cog(PermissionsCog(bot))
