"""
!copypasta <type> <value1> [value2 ...] / /copypasta use <type> <values>
posts a random copypasta from a chosen type's template pool, with each
of that type's {placeholder} names swapped out for the values you pass
in, matched positionally in the order the type's placeholders were
first established (see copypasta_manager.py). Most types only have one
placeholder (usually {text}), so most of the time it's just
`!copypasta tag @User`. A type whose templates use more than one
placeholder, e.g. `{people}'s {act} is cool`, needs that many values:
`!copypasta thattype Alice baskeyball`.

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

--- Slash commands ---
This is a commands.hybrid_group with no fallback:
  - "!copypasta tag @User" keeps working exactly as before (the group
    callback itself fires, same as always, when no subcommand name
    matches).
  - "/copypasta" cannot be invoked directly on Discord - a slash
    command group can only ever run one of its named subcommands, it
    has no callback of its own. Previously fallback="use" published
    the group's own callback as a "/copypasta use" subcommand; that's
    been removed on purpose, so posting a copypasta is now text-only
    ("!copypasta tag @User" / "!cp tag @User"). See GAPS.md.
  - The management subcommands (list/show/create/delete/enable/
    disable/add/remove) are unaffected and show up as their own normal
    slash subcommands, e.g. "/copypasta list", "/copypasta show".
  - `type` now offers autocomplete (suggests existing type names only -
    aliases still work if typed by hand, but aren't suggested, so the
    dropdown doesn't show two entries for what's really one type).

`values` used to be `*values` (any number of positional arguments),
which slash commands can't do - Discord options are a fixed list, not
open-ended. It's now a single `values` string, split on whitespace,
exactly the number of times the type's placeholders need. This means
an individual value can no longer contain a space (e.g. a two-word
song title) - see GAPS.md for this trade-off and what filling that
gap would look like.
"""
from typing import List

import discord
from discord import app_commands
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

# Discord caps autocomplete results at 25 choices per field - see GAPS.md.
AUTOCOMPLETE_LIMIT = 25


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

    # -- autocomplete -----------------------------------------------------
    async def type_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Suggests existing copypasta type names that match what's typed
        so far. Aliases from TYPE_ALIASES still work if typed by hand
        (see _resolve_type), they're just not offered as suggestions,
        since showing e.g. both "tag" and "name" for the same underlying
        type is more confusing than helpful. Shared by every subcommand
        below that takes a `type` argument."""
        current = (current or "").lower()
        types = self.manager.list_types()

        matches = sorted(name for name in types if current in name.lower())
        return [
            app_commands.Choice(name=name, value=name)
            for name in matches[:AUTOCOMPLETE_LIMIT]
        ]

    @commands.hybrid_group(name="copypasta", aliases=["cp"],
                            invoke_without_command=True,
                            description="Posts a random copypasta from a chosen type's template pool.")
    async def copypasta(self, ctx: commands.Context, type: str = None, *, values: str = None):
        """Posts a random copypasta from a type's template pool."""
        language = self._lang(ctx)

        if type is None:
            types = self.manager.list_types()
            if types:
                names = ", ".join(f"`{n}`" for n in sorted(types))
                await ctx.send(t(language,
                    f"Usage: `!copypasta <type> <value1> [value2 ...]`. Available types: {names}.\n"
                    f"Example: `!copypasta tag @User`, `!copypasta activity digging`, `!copypasta song a song`.\n"
                    f"Types with more than one `{{placeholder}}` need one value per placeholder, in order "
                    f"(see `!copypasta show <type>`).",
                    f"用法：`!copypasta <類型> <值1> [值2 ...]`。可用類型：{names}。\n"
                    f"示例：`!copypasta tag @User`、`!copypasta activity digging`、`!copypasta song a song`。\n"
                    f"若該類型的模板有多於一個 `{{佔位符}}`，需依序提供對應數量的值"
                    f"（見 `!copypasta show <類型>`）。"))
            else:
                await ctx.send(t(language,
                    "No copypasta types exist yet. An admin can create one with `!copypasta create <type>`.",
                    "尚未建立任何迷因文本類型。管理員可使用 `!copypasta create <類型>` 建立。"))
            return

        type_id = self._resolve_type(type)
        value_list = values.split() if values else []

        if not value_list:
            await ctx.send(t(language,
                f"Missing argument: at least one value. Usage: `!copypasta {type} <value1> [value2 ...]`.",
                f"缺少參數：至少需要一個值。用法：`!copypasta {type} <值1> [值2 ...]`。"))
            return

        key = (ctx.guild.id, type_id)
        index, rendered = self.manager.pick(type_id, value_list, avoid_index=self._last_used.get(key))
        self._last_used[key] = index
        await ctx.send(rendered)

    @copypasta.command(name="list", description="Lists all copypasta types and how many templates each has.")
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
            placeholders = s.get("placeholders") or ["text"]
            needed = " ".join(f"{{{name}}}" for name in placeholders)
            embed.add_field(name=type_id, value=f"{status} | {count} template(s) | {needed}", inline=False)
        embed.set_footer(text=t(language,
            "Use `!copypasta show <type>` to see the templates in a type.",
            "使用 `!copypasta show <類型>` 查看該類型中的模板。"))
        await ctx.send(embed=embed)

    @copypasta.command(name="show", aliases=["info"], description="Shows every template in one copypasta type.")
    @app_commands.describe(type="Copypasta type to show")
    @app_commands.autocomplete(type=type_autocomplete)
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
        placeholders = s.get("placeholders") or ["text"]
        needed = " ".join(f"{{{name}}}" for name in placeholders)
        embed = discord.Embed(
            title=t(language, f"Copypasta Type: {type_id}", f"迷因文本類型：{type_id}"),
            description="\n".join(lines),
            color=discord.Color.blue()
        )
        embed.set_footer(text=t(language,
            f"Usage: !copypasta {type_id} <value for {needed}>",
            f"用法：!copypasta {type_id} <對應 {needed} 的值>"))
        await ctx.send(embed=embed)

    @copypasta.command(name="create", description="Creates a new, empty copypasta type.")
    @app_commands.describe(type="Name for the new copypasta type")
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

    @copypasta.command(name="delete", description="Deletes a copypasta type entirely.")
    @app_commands.describe(type="Copypasta type to delete")
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_delete(self, ctx, type: str):
        """Deletes a copypasta type entirely."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.delete_type(type_id)
        await ctx.send(t(language, f"Deleted copypasta type `{type_id}`.", f"已刪除迷因文本類型 `{type_id}`。"))

    @copypasta.command(name="enable", description="Enables a copypasta type so !copypasta can pick it again.")
    @app_commands.describe(type="Copypasta type to enable")
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_enable(self, ctx, type: str):
        """Enables a copypasta type so !copypasta can pick it again."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.set_enabled(type_id, True)
        await ctx.send(t(language, f"Enabled copypasta type `{type_id}`.", f"已啟用迷因文本類型 `{type_id}`。"))

    @copypasta.command(name="disable", description="Disables a copypasta type without deleting it.")
    @app_commands.describe(type="Copypasta type to disable")
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_disable(self, ctx, type: str):
        """Disables a copypasta type without deleting it."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.set_enabled(type_id, False)
        await ctx.send(t(language, f"Disabled copypasta type `{type_id}`.", f"已停用迷因文本類型 `{type_id}`。"))

    @copypasta.command(name="add", description="Adds a template to a type. Must contain a {text} placeholder.")
    @app_commands.describe(
        type="Copypasta type to add a template to",
        template="Template text, containing at least one {placeholder}, e.g. '{text} is handsome'")
    @app_commands.autocomplete(type=type_autocomplete)
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

    @copypasta.command(name="remove", description="Removes a template by its index (see `!copypasta show <type>`).")
    @app_commands.describe(
        type="Copypasta type to remove a template from",
        index="Index of the template to remove (see /copypasta show)")
    @app_commands.autocomplete(type=type_autocomplete)
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
