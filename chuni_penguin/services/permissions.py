from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chuni_penguin.application.errors import ServiceUnavailable
from chuni_penguin.context import PenguinContext
from chuni_penguin.database import CommandPermission, Denylist
from chuni_penguin.database.guilds import (
    PrimaryPermissionTarget,
    SecondaryPermissionTarget,
)
from chuni_penguin.errors import Banned, CommandDisabled

# Preserve saved group rules when commands move to independently managed Cogs.
# Both the current group and its legacy group match in the original rule order.
LEGACY_PERMISSION_GROUPS = {
    "Diagnostics": "Miscellaneous",
    "ServerSettings": "Miscellaneous",
    "Calculators": "Tools",
    "Aliases": "Search",
    "Guides": "Tools",
    "ChartMedia": "Tools",
    "RatingGoals": "Tools",
    "Recommendations": "Tools",
    "Courses": "Search",
    "LoginBonus": "Profile",
    "ProfileSettings": "Profile",
    "PlayerActions": "Profile",
    "RecentScores": "Records",
    "SongArtwork": "Search",
    "Leaderboards": "Records",
    "ScoreQueries": "Records",
    "BestPlays": "Records",
    "ScoreSync": "Records",
    "PlayerStatistics": "Records",
}

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from chuni_penguin.services.database import DatabaseService


class PermissionService:
    def __init__(
        self,
        database: "DatabaseService | None",
        is_owner: "Callable[..., Awaitable[bool]]",
    ):
        self._database = database
        self.is_owner = is_owner
        self.permission_cache: dict[int, list[CommandPermission]] = {}
        self.denylist: dict[int, Denylist] = {}
        self.ready = False

    @property
    def database(self):
        if self._database is None:
            service = "database"
            raise ServiceUnavailable(service)
        return self._database

    def assert_ready(self):
        if not self.ready:
            service = "permissions"
            raise ServiceUnavailable(service)

    async def start(self):
        await self.load_denylist()
        await self.load_permissions()
        self.ready = True

    async def check(self, ctx: PenguinContext):
        if await self.is_owner(ctx.author):
            return True
        self.assert_ready()
        return await self.denylist_check(ctx) and await self.permissions_check(ctx)

    async def load_denylist(self):
        async with self.database.read_sessionmaker() as session:
            denylist = (await session.execute(select(Denylist))).scalars()

        self.denylist = {d.object_id: d for d in denylist}

    async def load_permissions(self, guild_id: int | None = None):
        if guild_id is not None:
            self.permission_cache[guild_id] = []
        else:
            self.permission_cache = {}

        async with self.database.read_sessionmaker() as session:
            query = select(CommandPermission).order_by(
                CommandPermission.guild_id, CommandPermission.index
            )

            if guild_id is not None:
                query = query.where(CommandPermission.guild_id == guild_id)

            perms = (await session.execute(query)).scalars().all()

        for perm in perms:
            self.permission_cache.setdefault(perm.guild_id, []).append(perm)

    async def add_permission(
        self,
        guild_id: int,
        primary_target_type: PrimaryPermissionTarget,
        primary_target_id: int,
        secondary_target: commands.Command | commands.Cog | None,
        *,
        is_allowed: bool,
    ):
        if secondary_target is None:
            secondary_target_type = SecondaryPermissionTarget.all
            secondary_target_name = ""
        elif isinstance(secondary_target, commands.Command):
            secondary_target_type = SecondaryPermissionTarget.command
            secondary_target_name = secondary_target.qualified_name
        else:
            secondary_target_type = SecondaryPermissionTarget.group
            secondary_target_name = secondary_target.qualified_name

        async def transaction(session: AsyncSession):
            query = select(func.max(CommandPermission.index)).where(
                CommandPermission.guild_id == guild_id
            )
            max_index = (await session.execute(query)).scalar_one_or_none()

            query = insert(CommandPermission).values(
                guild_id=guild_id,
                index=0 if max_index is None else max_index + 1,
                primary_target_type=primary_target_type,
                primary_target_id=primary_target_id,
                secondary_target_type=secondary_target_type,
                secondary_target_name=secondary_target_name,
                is_allowed=is_allowed,
            )
            query = query.on_conflict_do_update(
                index_elements=[
                    CommandPermission.primary_target_type,
                    CommandPermission.primary_target_id,
                    CommandPermission.secondary_target_type,
                    CommandPermission.secondary_target_name,
                ],
                set_={"is_allowed": query.excluded.is_allowed},
            )
            await session.execute(query)

        await self.database.writer.execute_fn(transaction)
        await self.load_permissions(guild_id)

    def get_denylist_entry(self, user_id: int, guild: discord.Guild | None = None):
        self.assert_ready()
        if user_id in self.denylist:
            return (self.denylist[user_id], None)

        if guild is not None and guild.id in self.denylist:
            return (self.denylist[guild.id], guild.name)

        return None, None

    def get_permission(
        self,
        group_name: str | None,
        command_name: str,
        guild_id: int,
        channel_id: int | None,
        author_id: int,
        role_ids: set[int],
    ) -> CommandPermission | None:
        self.assert_ready()
        permissions = self.permission_cache.get(guild_id, [])

        for permission in permissions:
            # If permission is for a command, but there's no command, or the permission
            # doesn't target the executed command
            if (
                permission.secondary_target_type == SecondaryPermissionTarget.command
                and permission.secondary_target_name != "*"
                and command_name != permission.secondary_target_name
            ):
                continue

            if (
                permission.secondary_target_type == SecondaryPermissionTarget.group
                and permission.secondary_target_name != "*"
                and group_name != permission.secondary_target_name
                and LEGACY_PERMISSION_GROUPS.get(group_name or "")
                != permission.secondary_target_name
            ):
                continue

            if (
                permission.primary_target_type == PrimaryPermissionTarget.user
                and author_id == permission.primary_target_id
            ):
                return permission

            if (
                permission.primary_target_type == PrimaryPermissionTarget.channel
                and channel_id == permission.primary_target_id
            ):
                return permission

            if (
                permission.primary_target_type == PrimaryPermissionTarget.role
                and permission.primary_target_id in role_ids
            ):
                return permission

            # Permission applies to the entire server
            if permission.primary_target_type == PrimaryPermissionTarget.guild:
                return permission

        return None

    async def denylist_check(self, ctx: PenguinContext):
        ban_entry, server_name = self.get_denylist_entry(ctx.author.id, ctx.guild)

        if ban_entry is not None:
            raise Banned(ban_entry, server_name)

        return True

    async def permissions_check(self, ctx: PenguinContext):
        if ctx.command is None:
            return True

        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return True

        # Prevent permission lockouts
        if ctx.author.guild_permissions.manage_guild:
            return True

        if isinstance(ctx.command, discord.app_commands.Command):
            cog_name = (
                ctx.command.binding.qualified_name
                if ctx.command.binding is not None
                else None
            )
        else:
            cog_name = ctx.command.cog_name

        permission = self.get_permission(
            cog_name,
            ctx.command.qualified_name,
            ctx.guild.id,
            ctx.channel.id,
            ctx.author.id,
            {role.id for role in ctx.author.roles},
        )

        if permission is None:
            return True

        if permission.is_allowed:
            return True

        raise CommandDisabled(permission)

    async def permissions_interaction_check(self, interaction: discord.Interaction):
        if interaction.command is None:
            return True

        if interaction.guild_id is None or not isinstance(
            interaction.user, discord.Member
        ):
            return True

        # Prevent permission lockouts
        if interaction.user.guild_permissions.manage_guild:
            return True

        if (
            isinstance(interaction.command, discord.app_commands.ContextMenu)
            and interaction.command.qualified_name == "View your score"
        ):
            # Manual group binding since context menu don't really have groups
            group_name = "Records"
        elif (
            isinstance(interaction.command, discord.app_commands.Command)
            and interaction.command.binding is not None
        ):
            group_name = interaction.command.binding.qualified_name
        else:
            group_name = None

        permission = self.get_permission(
            group_name,
            interaction.command.qualified_name,
            interaction.guild_id,
            interaction.channel_id,
            interaction.user.id,
            {role.id for role in interaction.user.roles},
        )

        if permission is None:
            return True

        if permission.is_allowed:
            return True

        await interaction.response.send_message(
            embed=discord.Embed(
                color=discord.Color.red(),
                title="Error",
                description=str(CommandDisabled(permission)),
            ),
            ephemeral=True,
        )
        return False
