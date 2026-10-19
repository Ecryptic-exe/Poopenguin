from typing import TYPE_CHECKING, cast

from discord.ext import commands

from chuni_penguin.application.registration import preview_registration
from chuni_penguin.command_tree import PenguinCommandTree
from chuni_penguin.context import PenguinContext

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class ExtensionsCog(commands.Cog, name="Extensions", command_attrs={"hidden": True}):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot

    @commands.group("cog", invoke_without_command=True)
    @commands.is_owner()
    async def extension_group(self, ctx: PenguinContext) -> None:
        """Manage extensions by their configured keys."""
        await self.extension_status(ctx)

    @extension_group.command("status")
    @commands.is_owner()
    async def extension_status(self, ctx: PenguinContext):
        lines = [
            f"{status.spec.key}: {status.state.value}"
            + (f" ({status.reason})" if status.reason else "")
            for status in self.bot.startup_report.extensions.values()
        ]
        lines[:0] = [
            f"service:{name}: {status['state']}"
            + (f" ({status['reason']})" if status["reason"] else "")
            for name, status in self.bot.services.statuses.items()
        ]
        # Keep management output bounded without losing rows.
        for start in range(0, len(lines), 12):
            await ctx.send("\n".join(lines[start : start + 12]))

    @extension_group.command("load")
    @commands.is_owner()
    async def extension_load(self, ctx: PenguinContext, key: str):
        try:
            status = await self.bot.extension_manager.load(key)
        except ValueError as exc:
            raise commands.BadArgument(str(exc)) from None
        await ctx.send(f"{status.spec.key}: {status.state.value} {status.reason}")

    @extension_group.command("unload")
    @commands.is_owner()
    async def extension_unload(self, ctx: PenguinContext, key: str):
        try:
            status = await self.bot.extension_manager.unload(key)
        except ValueError as exc:
            raise commands.BadArgument(str(exc)) from None
        await ctx.send(f"{status.spec.key}: {status.state.value}")

    @extension_group.command("reload")
    @commands.is_owner()
    async def extension_reload(self, ctx: PenguinContext, key: str):
        try:
            status = await self.bot.extension_manager.reload(key)
        except ValueError as exc:
            raise commands.BadArgument(str(exc)) from None
        await ctx.send(f"{status.spec.key}: {status.state.value} {status.reason}")

    @commands.command("treediff")
    @commands.is_owner()
    async def tree_diff(self, ctx: PenguinContext):
        """Compare the local and remote global tree; never writes to Discord."""
        current_hash = await cast(PenguinCommandTree, self.bot.tree).get_hash()
        try:
            self.bot.extension_manager.assert_sync_safe()
        except ValueError as exc:
            readiness = str(exc)
        else:
            readiness = "Ready for explicit treesync."
        await ctx.send(
            f"Local command tree: {current_hash}\n{readiness}\nNo remote changes made."
        )
        if self.bot.offline:
            return
        changes = await preview_registration(cast(PenguinCommandTree, self.bot.tree))
        lines = [f"{kind}: {name}" for kind, names in changes.items() for name in names]
        if not lines:
            await ctx.send("No command payload differences.")
        for start in range(0, len(lines), 12):
            await ctx.send("\n".join(lines[start : start + 12]))


async def setup(bot: "ChuniBot"):
    await bot.add_cog(ExtensionsCog(bot))
