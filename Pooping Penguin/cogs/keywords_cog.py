"""
!keyword command group - runtime management of the global keyword sets
that the bot scans every message against (see keyword_manager.py and
messages_cog.py for the matching side).

All commands here require administrator permission in the guild they're
run from. The keyword sets themselves are global (shared across every
server the bot is in, see keyword_manager.py docstring), so this is a
deliberately coarse permission check - anyone who can !keyword edit on
one server is editing what every other server sees too.
"""
import math

import discord
from discord.ext import commands

from config import load_settings
from i18n import t, get_guild_language
from keyword_manager import KeywordManager, KeywordError

# How many keyword sets are shown per page in the `!keyword list` menu.
KEYWORD_PAGE_SIZE = 5


class KeywordSearchModal(discord.ui.Modal):
    """Popup text box used by the Search button on KeywordMenu."""

    def __init__(self, menu: "KeywordMenu", language: str):
        super().__init__(title=t(language, "Search Keyword Sets", "搜尋關鍵詞組"))
        self.menu = menu
        self.term = discord.ui.TextInput(
            label=t(language, "Set name or keyword", "組名或關鍵詞"),
            placeholder=t(language, "e.g. cry", "例如：cry"),
            required=True,
            max_length=100,
        )
        self.add_item(self.term)

    async def on_submit(self, interaction: discord.Interaction):
        await self.menu.apply_search(interaction, self.term.value)


class KeywordMenu(discord.ui.View):
    """Paginated, searchable browser for keyword sets, in the same style
    as HelpMenu in help_cog.py (Previous/Next/Close buttons), plus a
    Search button that filters by set name or keyword substring."""

    def __init__(self, ctx, sets: dict, language: str, page_size: int = KEYWORD_PAGE_SIZE, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.language = language
        self.page_size = page_size
        self.all_items = sorted(sets.items())
        self.filtered_items = self.all_items
        self.search_term = None
        self.current_page = 0

        self.previous_button.label = t(language, "Previous", "上一頁")
        self.next_button.label = t(language, "Next", "下一頁")
        self.search_button.label = t(language, "🔍 Search", "🔍 搜尋")
        self.clear_button.label = t(language, "Clear Search", "清除搜尋")
        self.close_button.label = t(language, "Close", "關閉")

    def _total_pages(self):
        return max(1, math.ceil(len(self.filtered_items) / self.page_size))

    def get_embed(self):
        total_pages = self._total_pages()
        self.current_page = max(0, min(self.current_page, total_pages - 1))
        start = self.current_page * self.page_size
        page_items = self.filtered_items[start:start + self.page_size]

        title = t(self.language, "Keyword Sets", "關鍵詞組")
        if self.search_term:
            title += t(self.language, f' — search: "{self.search_term}"', f'－搜尋：「{self.search_term}」')

        embed = discord.Embed(title=title, color=discord.Color.blue())

        if not page_items:
            embed.description = t(self.language,
                "No keyword sets match that search.", "沒有符合搜尋條件的關鍵詞組。")
        else:
            for set_id, s in page_items:
                enabled = s.get("enabled", True)
                status = t(self.language, "✅ enabled", "✅ 已啟用") if enabled else \
                    t(self.language, "❌ disabled", "❌ 已停用")
                keywords = s.get("keywords", [])
                kw_preview = ", ".join(f"`{k}`" for k in keywords[:8])
                if len(keywords) > 8:
                    kw_preview += t(self.language, f" (+{len(keywords) - 8} more)", f"（還有 {len(keywords) - 8} 個）")
                if not kw_preview:
                    kw_preview = t(self.language, "(no keywords)", "（無關鍵詞）")
                embed.add_field(
                    name=set_id,
                    value=t(self.language,
                        f"{status} | {len(keywords)} keywords, {len(s.get('responses', []))} responses\n{kw_preview}",
                        f"{status} | {len(keywords)} 個關鍵詞，{len(s.get('responses', []))} 個回應\n{kw_preview}"),
                    inline=False
                )

        embed.set_footer(text=t(self.language,
            f"Page {self.current_page + 1}/{total_pages} | {len(self.filtered_items)} set(s) | "
            f"Use `!keyword show <id>` for full details.",
            f"第 {self.current_page + 1}/{total_pages} 頁 | 共 {len(self.filtered_items)} 個關鍵詞組 | "
            f"使用 `!keyword show <id>` 查看完整詳情。"))
        return embed

    async def apply_search(self, interaction: discord.Interaction, term: str):
        term = term.strip().lower()
        self.search_term = term
        self.filtered_items = [
            (set_id, s) for set_id, s in self.all_items
            if term in set_id.lower() or any(term in kw.lower() for kw in s.get("keywords", []))
        ]
        self.current_page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(self.language, "This isn't your menu to control.", "這不是您可以操作的選單。"),
                ephemeral=True)
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page - 1) % self._total_pages()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page + 1) % self._total_pages()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, row=1)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(KeywordSearchModal(self, self.language))

    @discord.ui.button(style=discord.ButtonStyle.grey, row=1)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.search_term = None
        self.filtered_items = self.all_items
        self.current_page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.red, row=1)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=t(self.language, "Keyword menu closed.", "關鍵詞選單已關閉。"), embed=None, view=None)
        self.stop()


class KeywordShowMenu(discord.ui.View):
    """Shows one keyword set's full detail, with its responses paginated
    5 at a time (flip buttons only appear if there's more than one page,
    so a set with few responses doesn't show useless buttons)."""

    RESPONSE_PAGE_SIZE = 5

    def __init__(self, ctx, set_id: str, s: dict, language: str, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.set_id = set_id
        self.s = s
        self.language = language
        self.responses = s.get("responses", [])
        self.current_page = 0

        self.previous_button.label = t(language, "Previous", "上一頁")
        self.next_button.label = t(language, "Next", "下一頁")
        self.close_button.label = t(language, "Close", "關閉")

        if self._total_pages() <= 1:
            self.remove_item(self.previous_button)
            self.remove_item(self.next_button)

    def _total_pages(self):
        return max(1, math.ceil(len(self.responses) / self.RESPONSE_PAGE_SIZE))

    def get_embed(self):
        total_pages = self._total_pages()
        self.current_page = max(0, min(self.current_page, total_pages - 1))
        start = self.current_page * self.RESPONSE_PAGE_SIZE
        page_responses = self.responses[start:start + self.RESPONSE_PAGE_SIZE]

        embed = discord.Embed(
            title=t(self.language, f"Keyword Set: {self.set_id}", f"關鍵詞組：{self.set_id}"),
            color=discord.Color.blue()
        )
        embed.add_field(
            name=t(self.language, "Status", "狀態"),
            value=t(self.language, "Enabled", "已啟用") if self.s.get("enabled", True)
            else t(self.language, "Disabled", "已停用"),
            inline=False
        )
        keywords = self.s.get("keywords", [])
        embed.add_field(
            name=t(self.language, "Keywords", "關鍵詞"),
            value=", ".join(f"`{k}`" for k in keywords) if keywords else t(self.language, "(none)", "（無）"),
            inline=False
        )
        if page_responses:
            responses_value = "\n".join(
                f"[{start + i}] {r[:80]}{'…' if len(r) > 80 else ''}" for i, r in enumerate(page_responses)
            )
        else:
            responses_value = t(self.language, "(none)", "（無）")
        embed.add_field(
            name=t(self.language, "Responses", "回應"),
            value=responses_value,
            inline=False
        )
        embed.set_footer(text=t(self.language,
            f"Response page {self.current_page + 1}/{total_pages} | {len(self.responses)} response(s) total.",
            f"回應第 {self.current_page + 1}/{total_pages} 頁 | 共 {len(self.responses)} 個回應。"))
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(self.language, "This isn't your menu to control.", "這不是您可以操作的選單。"),
                ephemeral=True)
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page - 1) % self._total_pages()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page + 1) % self._total_pages()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.red, row=0)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=t(self.language, "Keyword menu closed.", "關鍵詞選單已關閉。"), embed=None, view=None)
        self.stop()


class KeywordsCog(commands.Cog, name="keywords"):
    def __init__(self, bot):
        self.bot = bot
        self.manager = KeywordManager()

    def _lang(self, ctx):
        return get_guild_language(load_settings(), ctx.guild.id)

    async def cog_check(self, ctx):
        # Usable by anyone, but only in a server (not DMs), since ctx.guild
        # is needed for language lookup. Admin permission is now enforced
        # per-command below, only on subcommands that change data - `list`
        # and `show` are read-only and open to everyone.
        return ctx.guild is not None

    async def cog_command_error(self, ctx, error):
        language = self._lang(ctx) if ctx.guild else "english"
        if isinstance(error, commands.CheckFailure):
            await ctx.send(t(language,
                "You need administrator permissions to manage keyword sets.",
                "您需要管理員權限才能管理關鍵詞組。"))
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(t(language,
                f"Missing argument: `{error.param.name}`. Use `!help keyword` for usage.",
                f"缺少參數：`{error.param.name}`。使用 `!help keyword` 查看用法。"))
            return
        if isinstance(error.__cause__, KeywordError) or isinstance(error, KeywordError):
            msg = str(error.__cause__ or error)
            await ctx.send(msg)
            return
        raise error

    @commands.group(name="keyword", invoke_without_command=True)
    async def keyword(self, ctx):
        """Manage global keyword-triggered response sets."""
        language = self._lang(ctx)
        await ctx.send(t(language,
            "Keyword set management. Subcommands: `list`, `show <id>`, `create <id>`, "
            "`delete <id>`, `enable <id>`, `disable <id>`, `addkeyword <id> <kw>`, "
            "`removekeyword <id> <kw>`, `addresponse <id> <text>`, `removeresponse <id> <index>`.\n"
            "Use `!help keyword` for full details.",
            "關鍵詞組管理。子命令：`list`、`show <id>`、`create <id>`、`delete <id>`、"
            "`enable <id>`、`disable <id>`、`addkeyword <id> <關鍵詞>`、"
            "`removekeyword <id> <關鍵詞>`、`addresponse <id> <回應內容>`、`removeresponse <id> <索引>`。\n"
            "使用 `!help keyword` 查看完整說明。"))

    @keyword.command(name="list")
    async def keyword_list(self, ctx, *, search: str = None):
        """Lists all keyword sets in a browsable, searchable menu.

        Optionally pass a search term (e.g. `!keyword list cry`) to jump
        straight into a filtered view; use the Search button in the menu
        itself to change or clear the filter afterwards."""
        language = self._lang(ctx)
        sets = self.manager.list_sets()
        if not sets:
            await ctx.send(t(language, "No keyword sets exist yet.", "尚未建立任何關鍵詞組。"))
            return

        menu = KeywordMenu(ctx, sets, language)
        if search:
            term = search.strip().lower()
            menu.search_term = term
            menu.filtered_items = [
                (set_id, s) for set_id, s in menu.all_items
                if term in set_id.lower() or any(term in kw.lower() for kw in s.get("keywords", []))
            ]
        await ctx.send(embed=menu.get_embed(), view=menu)

    @keyword.command(name="show", aliases=["info"])
    async def keyword_show(self, ctx, set_id: str):
        """Shows the keywords and responses for one set (alias: `!keyword info <id>`).

        Responses are paginated 5 at a time, with Previous/Next buttons
        that only appear when there's more than one page."""
        language = self._lang(ctx)
        s = self.manager.get_set(set_id)
        menu = KeywordShowMenu(ctx, set_id, s, language)
        await ctx.send(embed=menu.get_embed(), view=menu)

    @keyword.command(name="create")
    @commands.has_permissions(administrator=True)
    async def keyword_create(self, ctx, set_id: str):
        """Creates a new, empty keyword set."""
        language = self._lang(ctx)
        self.manager.create_set(set_id)
        await ctx.send(t(language,
            f"Created keyword set `{set_id}`. Add keywords with `!keyword addkeyword {set_id} <word>` "
            f"and responses with `!keyword addresponse {set_id} <text>`.",
            f"已建立關鍵詞組 `{set_id}`。使用 `!keyword addkeyword {set_id} <關鍵詞>` 新增關鍵詞，"
            f"使用 `!keyword addresponse {set_id} <內容>` 新增回應。"))

    @keyword.command(name="delete")
    @commands.has_permissions(administrator=True)
    async def keyword_delete(self, ctx, set_id: str):
        """Deletes a keyword set entirely."""
        language = self._lang(ctx)
        self.manager.delete_set(set_id)
        await ctx.send(t(language, f"Deleted keyword set `{set_id}`.", f"已刪除關鍵詞組 `{set_id}`。"))

    @keyword.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def keyword_enable(self, ctx, set_id: str):
        """Enables a keyword set so it starts matching messages again."""
        language = self._lang(ctx)
        self.manager.set_enabled(set_id, True)
        await ctx.send(t(language, f"Enabled keyword set `{set_id}`.", f"已啟用關鍵詞組 `{set_id}`。"))

    @keyword.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def keyword_disable(self, ctx, set_id: str):
        """Disables a keyword set without deleting it."""
        language = self._lang(ctx)
        self.manager.set_enabled(set_id, False)
        await ctx.send(t(language, f"Disabled keyword set `{set_id}`.", f"已停用關鍵詞組 `{set_id}`。"))

    @keyword.command(name="addkeyword")
    @commands.has_permissions(administrator=True)
    async def keyword_addkeyword(self, ctx, set_id: str, *, keyword: str):
        """Adds a trigger keyword to a set."""
        language = self._lang(ctx)
        self.manager.add_keyword(set_id, keyword)
        await ctx.send(t(language,
            f"Added trigger `{keyword}` to `{set_id}`.",
            f"已將觸發詞 `{keyword}` 加入 `{set_id}`。"))

    @keyword.command(name="removekeyword")
    @commands.has_permissions(administrator=True)
    async def keyword_removekeyword(self, ctx, set_id: str, *, keyword: str):
        """Removes a trigger keyword from a set."""
        language = self._lang(ctx)
        self.manager.remove_keyword(set_id, keyword)
        await ctx.send(t(language,
            f"Removed trigger `{keyword}` from `{set_id}`.",
            f"已將觸發詞 `{keyword}` 從 `{set_id}` 移除。"))

    @keyword.command(name="addresponse")
    @commands.has_permissions(administrator=True)
    async def keyword_addresponse(self, ctx, set_id: str, *, response: str):
        """Adds a candidate response text to a set."""
        language = self._lang(ctx)
        self.manager.add_response(set_id, response)
        s = self.manager.get_set(set_id)
        index = len(s["responses"]) - 1
        await ctx.send(t(language,
            f"Added response #{index} to `{set_id}`.",
            f"已將回應 #{index} 加入 `{set_id}`。"))

    @keyword.command(name="removeresponse")
    @commands.has_permissions(administrator=True)
    async def keyword_removeresponse(self, ctx, set_id: str, index: int):
        """Removes a response by its index (see `!keyword show <id>`)."""
        language = self._lang(ctx)
        self.manager.remove_response(set_id, index)
        await ctx.send(t(language,
            f"Removed response #{index} from `{set_id}`.",
            f"已將回應 #{index} 從 `{set_id}` 移除。"))


async def setup(bot):
    await bot.add_cog(KeywordsCog(bot))
