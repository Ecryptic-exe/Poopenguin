"""
!copypasta <type> <text> - posts a random copypasta from a chosen type's
template pool, with `{text}` in the template swapped out for whatever the
user passed in.

Three seed types ship in data/copypasta_sets.json:
  tag       - "<@user/name> is handsome" style lines
  activity  - "I think <activity> is stupid" style lines
  song      - '"<song>" is a very good song' style lines

Each type has its own pool of templates (see copypasta_manager.py), and a
template is only ever picked from the pool matching the requested type -
that's what stops a "tag" line accidentally getting used to answer a
"song" request or similar mismatches. On top of that, the cog remembers
the last template used per (server, type) so the exact same line doesn't
fire twice back-to-back.

Management subcommands (`!copypasta add/remove/create/delete/enable/
disable/list/show`) let admins grow each pool live, the same way
!keyword manages keyword sets - no redeploy needed to add more lines.
"""
import discord
from discord.ext import commands

from config import load_settings
from i18n import t, get_guild_language
from copypasta_manager import CopypastaManager, CopypastaError

# Common synonyms that map onto the same underlying pool, so
# "!copypasta name @User" and "!copypasta tag @User" both work.
TYPE_ALIASES = {
    "tag": "tag", "name": "tag", "person": "tag", "mention": "tag", "user": "tag",
    "activity": "activity", "thing": "activity", "action": "activity", "verb": "activity",
    "song": "song", "music": "song", "tune": "song",
}

# Subcommand names, reserved so they can never be shadowed by a type_id
# (invoke_without_command dispatch checks these before falling through
# to the "fire a copypasta" behaviour).
RESERVED_NAMES = {"list", "show", "create", "delete", "enable", "disable", "add", "remove"}


class CopypastaCog(commands.Cog, name="copypasta"):
    def __init__(self, bot):
        self.bot = bot
        self.manager = CopypastaManager()
        # (guild_id, type_id) -> last template index used, so the same
        # line doesn't repeat twice in a row for that type on that server.
        self._last_used = {}

    def _lang(self, ctx):
        return get_guild_language(load_settings(), ctx.guild.id)

    def _resolve_type(self, type_str: str) -> str:
        return TYPE_ALIASES.get(type_str.lower(), type_str.lower())

    async def cog_check(self, ctx):
        return ctx.guild is not None

    async def cog_command_error(self, ctx, error):
        language = self._lang(ctx) if ctx.guild else "english"
        if isinstance(error, commands.CheckFailure):
            await ctx.send(t(language,
                "You need administrator permissions to manage copypasta types.",
                "您需要管理員權限才能管理迷因文本類型。"))
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(t(language,
                f"Missing argument: `{error.param.name}`. Use `!help copypasta` for usage.",
                f"缺少參數：`{error.param.name}`。使用 `!help copypasta` 查看用法。"))
            return
        if isinstance(error.__cause__, CopypastaError) or isinstance(error, CopypastaError):
            msg = str(error.__cause__ or error)
            await ctx.send(msg)
            return
        raise error

    @commands.group(name="copypasta", aliases=["cp"], invoke_without_command=True)
    async def copypasta(self, ctx, type: str = None, *, text: str = None):
        """Posts a random copypasta from a type's template pool."""
        language = self._lang(ctx)

        if type is None:
            types = self.manager.list_types()
            if types:
                names = ", ".join(f"`{n}`" for n in sorted(types))
                await ctx.send(t(language,
                    f"Usage: `!copypasta <type> <text>`. Available types: {names}.\n"
                    f"Example: `!copypasta tag @User`, `!copypasta activity digging`, `!copypasta song a song`.",
                    f"用法：`!copypasta <類型> <文字>`。可用類型：{names}。\n"
                    f"示例：`!copypasta tag @User`、`!copypasta activity digging`、`!copypasta song a song`。"))
            else:
                await ctx.send(t(language,
                    "No copypasta types exist yet. An admin can create one with `!copypasta create <type>`.",
                    "尚未建立任何迷因文本類型。管理員可使用 `!copypasta create <類型>` 建立。"))
            return

        type_id = self._resolve_type(type)

        if text is None:
            await ctx.send(t(language,
                f"Missing argument: `text`. Usage: `!copypasta {type} <text>`.",
                f"缺少參數：`文字`。用法：`!copypasta {type} <文字>`。"))
            return

        key = (ctx.guild.id, type_id)
        index, rendered = self.manager.pick(type_id, text, avoid_index=self._last_used.get(key))
        self._last_used[key] = index
        await ctx.send(rendered)

    @copypasta.command(name="list")
    async def copypasta_list(self, ctx):
        """Lists all copypasta types and how many templates each has."""
        language = self._lang(ctx)
        types = self.manager.list_types()
        if not types:
            await ctx.send(t(language, "No copypasta types exist yet.", "尚未建立任何迷因文本類型。"))
            return

        embed = discord.Embed(title=t(language, "Copypasta Types", "迷因文本類型"), color=discord.Color.blue())
        for type_id, s in sorted(types.items()):
            enabled = s.get("enabled", True)
            status = t(language, "✅ enabled", "✅ 已啟用") if enabled else t(language, "❌ disabled", "❌ 已停用")
            count = len(s.get("templates", []))
            embed.add_field(name=type_id, value=f"{status} | {count} template(s)", inline=False)
        embed.set_footer(text=t(language,
            "Use `!copypasta show <type>` to see the templates in a type.",
            "使用 `!copypasta show <類型>` 查看該類型中的模板。"))
        await ctx.send(embed=embed)

    @copypasta.command(name="show", aliases=["info"])
    async def copypasta_show(self, ctx, type: str):
        """Shows every template in one copypasta type."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        s = self.manager.get_type(type_id)
        templates = s.get("templates", [])
        if not templates:
            await ctx.send(t(language, f"`{type_id}` has no templates yet.", f"`{type_id}` 尚無任何模板。"))
            return

        lines = [f"`{i}`: {tmpl}" for i, tmpl in enumerate(templates)]
        embed = discord.Embed(
            title=t(language, f"Copypasta Type: {type_id}", f"迷因文本類型：{type_id}"),
            description="\n".join(lines),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @copypasta.command(name="create")
    @commands.has_permissions(administrator=True)
    async def copypasta_create(self, ctx, type: str):
        """Creates a new, empty copypasta type."""
        language = self._lang(ctx)
        type_id = type.lower()
        self.manager.create_type(type_id)
        await ctx.send(t(language,
            f"Created copypasta type `{type_id}`. Add templates with "
            f"`!copypasta add {type_id} <template with {{text}} in it>`.",
            f"已建立迷因文本類型 `{type_id}`。使用 `!copypasta add {type_id} <包含 {{text}} 的模板>` 新增模板。"))

    @copypasta.command(name="delete")
    @commands.has_permissions(administrator=True)
    async def copypasta_delete(self, ctx, type: str):
        """Deletes a copypasta type entirely."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.delete_type(type_id)
        await ctx.send(t(language, f"Deleted copypasta type `{type_id}`.", f"已刪除迷因文本類型 `{type_id}`。"))

    @copypasta.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def copypasta_enable(self, ctx, type: str):
        """Enables a copypasta type so !copypasta can pick it again."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.set_enabled(type_id, True)
        await ctx.send(t(language, f"Enabled copypasta type `{type_id}`.", f"已啟用迷因文本類型 `{type_id}`。"))

    @copypasta.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def copypasta_disable(self, ctx, type: str):
        """Disables a copypasta type without deleting it."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.set_enabled(type_id, False)
        await ctx.send(t(language, f"Disabled copypasta type `{type_id}`.", f"已停用迷因文本類型 `{type_id}`。"))

    @copypasta.command(name="add")
    @commands.has_permissions(administrator=True)
    async def copypasta_add(self, ctx, type: str, *, template: str):
        """Adds a template to a type. Must contain a {text} placeholder."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.add_template(type_id, template)
        s = self.manager.get_type(type_id)
        index = len(s["templates"]) - 1
        await ctx.send(t(language,
            f"Added template #{index} to `{type_id}`.",
            f"已將模板 #{index} 加入 `{type_id}`。"))

    @copypasta.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def copypasta_remove(self, ctx, type: str, index: int):
        """Removes a template by its index (see `!copypasta show <type>`)."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.remove_template(type_id, index)
        await ctx.send(t(language,
            f"Removed template #{index} from `{type_id}`.",
            f"已將模板 #{index} 從 `{type_id}` 移除。"))


async def setup(bot):
    await bot.add_cog(CopypastaCog(bot))
