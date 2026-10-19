"""Independent opt-in Project B template commands.

Derived from Ecryptic-exe/Poopenguin 3594787; MIT notice in LICENSES/Poopenguin-MIT.txt.
"""

# Preserve Project B's Traditional Chinese messages.
# ruff: noqa: RUF001
import discord
from discord import app_commands
from discord.ext import commands

from chuni_penguin.services.copypasta import CopypastaError
from chuni_penguin.ui.copypasta import CopypastaInfoMenu, CopypastaMenu
from chuni_penguin.utils.localization import guild_language, t

TYPE_ALIASES = {
    "tag": "tag",
    "name": "tag",
    "person": "tag",
    "mention": "tag",
    "user": "tag",
    "activity": "activity",
    "thing": "activity",
    "action": "activity",
    "verb": "activity",
    "song": "song",
    "music": "song",
    "tune": "song",
}
RESERVED_NAMES = {
    "list",
    "info",
    "show",
    "create",
    "delete",
    "enable",
    "disable",
    "add",
    "remove",
}
AUTOCOMPLETE_LIMIT = 25


class CopypastaCog(commands.Cog, name="copypasta"):
    def __init__(self, bot):
        self.bot = bot
        self.settings = bot.settings
        self.service = bot.services.copypasta
        self.manager = self.service.manager
        # (guild_id, type_id) -> last template index used, so the same
        # line doesn't repeat twice in a row for that type on that server.
        self._last_used = self.service.last_used

    def _lang(self, ctx):
        return guild_language(self.settings.fun, ctx.guild.id)

    def _resolve_type(self, type_str: str) -> str:
        return TYPE_ALIASES.get(type_str.lower(), type_str.lower())

    async def cog_check(self, ctx):
        return ctx.guild is not None

    async def cog_command_error(self, ctx, error):
        language = self._lang(ctx) if ctx.guild else "english"
        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                t(
                    language,
                    "You need administrator permissions to manage copypasta types.",
                    "您需要管理員權限才能管理迷因文本類型。",
                )
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                t(
                    language,
                    f"Missing argument: `{error.param.name}`. Use `!help copypasta` for usage.",
                    f"缺少參數：`{error.param.name}`。使用 `!help copypasta` 查看用法。",
                )
            )
            return
        if isinstance(error.__cause__, CopypastaError) or isinstance(
            error, CopypastaError
        ):
            msg = str(error.__cause__ or error)
            await ctx.send(msg)
            return
        raise error

    # -- autocomplete -----------------------------------------------------
    async def type_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
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

    @commands.hybrid_group(
        name="copypasta",
        aliases=["cp"],
        description="Posts a random copypasta from a chosen type's template pool.",
    )
    async def copypasta(
        self,
        ctx: commands.Context,
        type: str | None = None,
        *,
        values: str | None = None,
    ):
        """Posts a random copypasta from a type's template pool."""
        language = self._lang(ctx)

        if type is None:
            types = self.manager.list_types()
            if types:
                names = ", ".join(f"`{n}`" for n in sorted(types))
                await ctx.send(
                    t(
                        language,
                        f"Usage: `!copypasta <type> <value1> [value2 ...]`. Available types: {names}.\n"
                        f"Example: `!copypasta tag @User`, `!copypasta activity digging`, `!copypasta song a song`.\n"
                        f"Types with more than one `{{placeholder}}` need one value per placeholder, in order "
                        f"(see `!copypasta info <type>`).",
                        f"用法：`!copypasta <類型> <值1> [值2 ...]`。可用類型：{names}。\n"
                        f"示例：`!copypasta tag @User`、`!copypasta activity digging`、`!copypasta song a song`。\n"
                        f"若該類型的模板有多於一個 `{{佔位符}}`，需依序提供對應數量的值"
                        f"（見 `!copypasta info <類型>`）。",
                    )
                )
            else:
                await ctx.send(
                    t(
                        language,
                        "No copypasta types exist yet. An admin can create one with `!copypasta create <type>`.",
                        "尚未建立任何迷因文本類型。管理員可使用 `!copypasta create <類型>` 建立。",
                    )
                )
            return

        type_id = self._resolve_type(type)
        value_list = values.split() if values else []

        if not value_list:
            await ctx.send(
                t(
                    language,
                    f"Missing argument: at least one value. Usage: `!copypasta {type} <value1> [value2 ...]`.",
                    f"缺少參數：至少需要一個值。用法：`!copypasta {type} <值1> [值2 ...]`。",
                )
            )
            return

        key = (ctx.guild.id, type_id)  # pyright: ignore[reportOptionalMemberAccess] - cog_check is guild-only
        index, rendered = self.manager.pick(
            type_id, value_list, avoid_index=self._last_used.get(key)
        )
        self._last_used[key] = index
        await ctx.send(rendered)

    @copypasta.command(
        name="list",
        description="Lists all copypasta types in a browsable, searchable menu.",
    )
    async def copypasta_list(self, ctx, *, search: str | None = None):
        """Lists all copypasta types in a browsable, searchable menu.

        Optionally pass a search term (e.g. `!copypasta list ta`) to jump
        straight into a filtered view; use the Search button in the menu
        itself to change or clear the filter afterwards."""
        language = self._lang(ctx)
        types = self.manager.list_types()
        if not types:
            await ctx.send(
                t(
                    language,
                    "No copypasta types exist yet.",
                    "尚未建立任何迷因文本類型。",
                )
            )
            return

        menu = CopypastaMenu(ctx, types, language, service=self.service)
        if search:
            term = search.strip().lower()
            menu.search_term = term
            menu.filtered_items = [
                (type_id, s) for type_id, s in menu.all_items if term in type_id.lower()
            ]
        await ctx.send(embed=menu.get_embed(), view=menu)

    @copypasta.command(
        name="info",
        aliases=["show"],
        description="Shows every template in one copypasta type.",
    )
    @app_commands.describe(type="Copypasta type to show")
    @app_commands.autocomplete(type=type_autocomplete)
    async def copypasta_info(self, ctx, type: str):
        """Shows every template in one copypasta type (alias: `!copypasta show <type>`).

        Templates are paginated 5 at a time, with Previous/Next buttons
        that only appear when there's more than one page."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        s = self.manager.get_type(type_id)
        menu = CopypastaInfoMenu(ctx, self.service, type_id, s, language)
        menu.message = await ctx.send(embed=menu.get_embed(), view=menu)

    @copypasta.command(
        name="create", description="Creates a new, empty copypasta type."
    )
    @app_commands.describe(type="Name for the new copypasta type")
    @commands.has_permissions(administrator=True)
    async def copypasta_create(self, ctx, type: str):
        """Creates a new, empty copypasta type."""
        language = self._lang(ctx)
        type_id = type.lower()
        self.manager.create_type(type_id)
        await ctx.send(
            t(
                language,
                f"Created copypasta type `{type_id}`. Add templates with "
                f"`!copypasta add {type_id} <template with {{text}} in it>`.",
                f"已建立迷因文本類型 `{type_id}`。使用 `!copypasta add {type_id} <包含 {{text}} 的模板>` 新增模板。",
            )
        )

    @copypasta.command(name="delete", description="Deletes a copypasta type entirely.")
    @app_commands.describe(type="Copypasta type to delete")
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_delete(self, ctx, type: str):
        """Deletes a copypasta type entirely."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.delete_type(type_id)
        await ctx.send(
            t(
                language,
                f"Deleted copypasta type `{type_id}`.",
                f"已刪除迷因文本類型 `{type_id}`。",
            )
        )

    @copypasta.command(
        name="enable",
        description="Enables a copypasta type so !copypasta can pick it again.",
    )
    @app_commands.describe(type="Copypasta type to enable")
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_enable(self, ctx, type: str):
        """Enables a copypasta type so !copypasta can pick it again."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.set_enabled(type_id, True)
        await ctx.send(
            t(
                language,
                f"Enabled copypasta type `{type_id}`.",
                f"已啟用迷因文本類型 `{type_id}`。",
            )
        )

    @copypasta.command(
        name="disable", description="Disables a copypasta type without deleting it."
    )
    @app_commands.describe(type="Copypasta type to disable")
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_disable(self, ctx, type: str):
        """Disables a copypasta type without deleting it."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.set_enabled(type_id, False)
        await ctx.send(
            t(
                language,
                f"Disabled copypasta type `{type_id}`.",
                f"已停用迷因文本類型 `{type_id}`。",
            )
        )

    @copypasta.command(
        name="add",
        description="Adds a template to a type. Must contain a {text} placeholder.",
    )
    @app_commands.describe(
        type="Copypasta type to add a template to",
        template="Template text, containing at least one {placeholder}, e.g. '{text} is handsome'",
    )
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_add(self, ctx, type: str, *, template: str):
        """Adds a template to a type. Must contain a {text} placeholder."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.add_template(type_id, template)
        s = self.manager.get_type(type_id)
        index = len(s["templates"]) - 1
        await ctx.send(
            t(
                language,
                f"Added template #{index} to `{type_id}`.",
                f"已將模板 #{index} 加入 `{type_id}`。",
            )
        )

    @copypasta.command(
        name="remove",
        description="Removes a template by its index (see `!copypasta info <type>`).",
    )
    @app_commands.describe(
        type="Copypasta type to remove a template from",
        index="Index of the template to remove (see /copypasta info)",
    )
    @app_commands.autocomplete(type=type_autocomplete)
    @commands.has_permissions(administrator=True)
    async def copypasta_remove(self, ctx, type: str, index: int):
        """Removes a template by its index (see `!copypasta info <type>`)."""
        language = self._lang(ctx)
        type_id = self._resolve_type(type)
        self.manager.remove_template(type_id, index)
        await ctx.send(
            t(
                language,
                f"Removed template #{index} from `{type_id}`.",
                f"已將模板 #{index} 從 `{type_id}` 移除。",
            )
        )


async def setup(bot):
    await bot.add_cog(CopypastaCog(bot))
