"""Project B template menus adapted to shared services, independently of Cogs.

Derived from Ecryptic-exe/Poopenguin 3594787; MIT notice in LICENSES/Poopenguin-MIT.txt.
"""

# Preserve original bilingual messages and the HTTPException-only close handler.
# ruff: noqa: RUF001, SIM105
import math
from typing import TYPE_CHECKING

import discord

from chuni_penguin.services.copypasta import CopypastaError, extract_placeholders
from chuni_penguin.utils.localization import t

if TYPE_CHECKING:
    from chuni_penguin.services.copypasta_sessions import CopypastaService

COPYPASTA_PAGE_SIZE = 5
TEMPLATE_PAGE_SIZE = 5


class CopypastaJumpModal(discord.ui.Modal):
    """Popup asking for a page number, used by the Jump button on both
    CopypastaMenu and CopypastaInfoMenu. Only needs `menu.current_page`,
    `menu._total_pages()`, `menu.get_embed()` and `menu.language` on the
    menu it's attached to (plus an optional `template_select` to refresh
    on CopypastaInfoMenu), so one modal class covers both."""

    def __init__(self, menu, language: str):
        total = menu._total_pages()
        super().__init__(title=t(language, "Jump to Page", "跳至頁面"))
        self.menu = menu
        self.total = total
        self.page_input = discord.ui.TextInput(
            label=t(language, f"Page number (1-{total})", f"頁碼（1-{total}）"),
            placeholder="1",
            required=True,
            max_length=10,
        )
        self.add_item(self.page_input)
        menu.service.track_view(self)

    async def on_submit(self, interaction: discord.Interaction):
        language = self.menu.language
        raw = self.page_input.value.strip()
        if not raw.isdigit() or not (1 <= int(raw) <= self.total):
            await interaction.response.send_message(
                t(
                    language,
                    f"Please enter a page number between 1 and {self.total}.",
                    f"請輸入介於 1 到 {self.total} 之間的頁碼。",
                ),
                ephemeral=True,
            )
            return
        self.menu.current_page = int(raw) - 1
        if getattr(self.menu, "template_select", None):
            self.menu.template_select.refresh()
        await interaction.response.edit_message(
            embed=self.menu.get_embed(), view=self.menu
        )


class CopypastaSearchModal(discord.ui.Modal):
    """Popup text box used by the Search button on CopypastaMenu."""

    def __init__(self, menu: "CopypastaMenu", language: str):
        super().__init__(
            title=t(language, "Search Copypasta Types", "搜尋迷因文本類型")
        )
        self.menu = menu
        self.term = discord.ui.TextInput(
            label=t(language, "Type name", "類型名稱"),
            placeholder=t(language, "e.g. tag", "例如：tag"),
            required=True,
            max_length=100,
        )
        self.add_item(self.term)
        menu.service.track_view(self)

    async def on_submit(self, interaction: discord.Interaction):
        await self.menu.apply_search(interaction, self.term.value)


class CopypastaMenu(discord.ui.View):
    """Paginated, searchable browser for copypasta types, in the same
    style as KeywordMenu in keywords_cog.py (Previous/Next/Close
    buttons), plus a Search button that filters by type name. Needed
    because a plain embed with one field per type runs into Discord's
    25-field cap once there are enough types to matter."""

    def __init__(
        self,
        ctx,
        types: dict,
        language: str,
        page_size: int = COPYPASTA_PAGE_SIZE,
        timeout: int = 180,
        *,
        service: "CopypastaService",
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.service = service
        self.language = language
        self.page_size = page_size
        self.all_items = sorted(types.items())
        self.filtered_items = self.all_items
        self.search_term = None
        self.current_page = 0

        self.previous_button.label = t(language, "Previous", "上一頁")
        self.next_button.label = t(language, "Next", "下一頁")
        self.jump_button.label = t(language, "🔢 Jump", "🔢 跳頁")
        self.search_button.label = t(language, "🔍 Search", "🔍 搜尋")
        self.clear_button.label = t(language, "Clear Search", "清除搜尋")
        self.close_button.label = t(language, "Close", "關閉")
        self.service.track_view(self)

    def _total_pages(self):
        return max(1, math.ceil(len(self.filtered_items) / self.page_size))

    def get_embed(self):
        total_pages = self._total_pages()
        self.current_page = max(0, min(self.current_page, total_pages - 1))
        start = self.current_page * self.page_size
        page_items = self.filtered_items[start : start + self.page_size]

        title = t(self.language, "Copypasta Types", "迷因文本類型")
        if self.search_term:
            title += t(
                self.language,
                f' — search: "{self.search_term}"',
                f"－搜尋：「{self.search_term}」",
            )

        embed = discord.Embed(title=title, color=discord.Color.blue())

        if not page_items:
            embed.description = t(
                self.language,
                "No copypasta types match that search.",
                "沒有符合搜尋條件的迷因文本類型。",
            )
        else:
            for type_id, s in page_items:
                enabled = s.get("enabled", True)
                status = (
                    t(self.language, "✅ enabled", "✅ 已啟用")
                    if enabled
                    else t(self.language, "❌ disabled", "❌ 已停用")
                )
                count = len(s.get("templates", []))
                placeholders = s.get("placeholders") or ["text"]
                # This is the union of placeholders across every template
                # in the type - individual templates may only use some of
                # these (see !copypasta info <type> for specifics).
                needed = " ".join(f"{{{name}}}" for name in placeholders)
                embed.add_field(
                    name=type_id,
                    value=f"{status} | {count} template(s) | up to: {needed}",
                    inline=False,
                )

        embed.set_footer(
            text=t(
                self.language,
                f"Page {self.current_page + 1}/{total_pages} | {len(self.filtered_items)} type(s) | "
                f"Use `!copypasta info <type>` for full details.",
                f"第 {self.current_page + 1}/{total_pages} 頁 | 共 {len(self.filtered_items)} 個類型 | "
                f"使用 `!copypasta info <類型>` 查看完整詳情。",
            )
        )
        return embed

    async def apply_search(self, interaction: discord.Interaction, term: str):
        term = term.strip().lower()
        self.search_term = term
        self.filtered_items = [
            (type_id, s) for type_id, s in self.all_items if term in type_id.lower()
        ]
        self.current_page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(
                    self.language,
                    "This isn't your menu to control.",
                    "這不是您可以操作的選單。",
                ),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = (self.current_page - 1) % self._total_pages()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = (self.current_page + 1) % self._total_pages()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def jump_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(CopypastaJumpModal(self, self.language))

    @discord.ui.button(style=discord.ButtonStyle.blurple, row=1)
    async def search_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(CopypastaSearchModal(self, self.language))

    @discord.ui.button(style=discord.ButtonStyle.grey, row=1)
    async def clear_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.search_term = None
        self.filtered_items = self.all_items
        self.current_page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.red, row=1)
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content=t(self.language, "Copypasta menu closed.", "迷因文本選單已關閉。"),
            embed=None,
            view=None,
        )
        self.stop()


class CopypastaValuesModal(discord.ui.Modal):
    """Popup collecting the value(s) needed to fill in one specific
    template's placeholder(s), triggered by picking a template from the
    dropdown in CopypastaInfoMenu. One text field per placeholder when
    there's room - Discord modals cap out at 5 components, so a type
    with more than 5 placeholders (very unlikely) falls back to a
    single field split on whitespace, the same way `!copypasta <type>
    <values>` already handles multi-placeholder values."""

    def __init__(self, menu: "CopypastaInfoMenu", index: int, placeholders: list):
        language = menu.language
        type_id = menu.type_id
        super().__init__(
            title=t(
                language, f"Fill in {type_id} #{index}", f"填入 {type_id} #{index}"
            )[:45]
        )
        self.menu = menu
        self.type_id = type_id
        self.index = index
        self.language = language
        self.single_field = len(placeholders) > 5

        if self.single_field:
            label = t(
                language,
                f"Values ({' '.join(placeholders)}), space-separated",
                f"值（{' '.join(placeholders)}），以空格分隔",
            )
            self.combined = discord.ui.TextInput(
                label=label[:45], required=True, max_length=200
            )
            self.add_item(self.combined)
        else:
            self.fields = []
            for name in placeholders:
                field = discord.ui.TextInput(label=name, required=True, max_length=200)
                self.fields.append(field)
                self.add_item(field)
        menu.service.track_view(self)

    async def on_submit(self, interaction: discord.Interaction):
        values = (
            self.combined.value.split()
            if self.single_field
            else [f.value for f in self.fields]
        )

        try:
            rendered = self.menu.service.manager.render(
                self.type_id, self.index, values
            )
        except CopypastaError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        # Keep the "don't repeat the same template twice in a row"
        # tracking in sync with a manually-picked template too, so a
        # regular !copypasta right after doesn't immediately reuse it.
        self.menu.service.last_used[(self.menu.ctx.guild.id, self.type_id)] = self.index
        await interaction.response.send_message(rendered)
        # Close the menu afterwards so its Select/buttons go dead instead
        # of sitting there ready to spam out another generation from the
        # same message.
        await self.menu.close_menu()


class CopypastaRandomModal(discord.ui.Modal):
    """Popup used by the Random button on CopypastaInfoMenu - collects
    values the same way the plain `!copypasta <type> <values>` command
    does, against the type's whole placeholder pool (since which
    template ends up picked - and therefore which of those placeholders
    are actually used - isn't known until after this submits), then
    hands off to CopypastaManager.pick() for a random template from the
    pool. One field per placeholder when there's room, same 5-component
    cap/fallback as CopypastaValuesModal."""

    def __init__(self, menu: "CopypastaInfoMenu"):
        language = menu.language
        type_id = menu.type_id
        super().__init__(title=t(language, f"Random {type_id}", f"隨機 {type_id}")[:45])
        self.menu = menu
        self.type_id = type_id
        self.language = language

        placeholders = menu.s.get("placeholders") or ["text"]
        self.single_field = len(placeholders) > 5

        if self.single_field:
            needed = " ".join(f"{{{name}}}" for name in placeholders)
            label = t(
                language,
                f"Values ({needed}), space-separated",
                f"值（{needed}），以空格分隔",
            )
            self.combined = discord.ui.TextInput(
                label=label[:45],
                required=True,
                max_length=200,
                placeholder=t(
                    language,
                    "exact values needed depend on which template gets picked",
                    "實際所需的值視隨機抽中的模板而定",
                ),
            )
            self.add_item(self.combined)
        else:
            self.fields = []
            for name in placeholders:
                field = discord.ui.TextInput(label=name, required=True, max_length=200)
                self.fields.append(field)
                self.add_item(field)
        menu.service.track_view(self)

    async def on_submit(self, interaction: discord.Interaction):
        # Named dict when there's a field per placeholder, so pick() can
        # match values to whichever template gets picked BY NAME - it
        # may only use some of this type's whole placeholder pool, and
        # matching by name (not position) is what lets the unused ones
        # get correctly ignored instead of misaligning the rest. The
        # >5-placeholder single-field fallback has no names to key by,
        # so it stays positional, same limitation the text command has.
        values = (
            self.combined.value.split()
            if self.single_field
            else {f.label: f.value for f in self.fields}
        )
        key = (self.menu.ctx.guild.id, self.type_id)

        try:
            index, rendered = self.menu.service.manager.pick(
                self.type_id, values, avoid_index=self.menu.service.last_used.get(key)
            )
        except CopypastaError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.menu.service.last_used[key] = index
        await interaction.response.send_message(rendered)
        # Same reasoning as CopypastaValuesModal: close the menu once a
        # copypasta has actually been generated from it.
        await self.menu.close_menu()


class CopypastaTemplateSelect(discord.ui.Select):
    """Dropdown listing the templates on the current page of a
    CopypastaInfoMenu. Picking one opens CopypastaValuesModal to collect
    that template's placeholder value(s), then posts the rendered result."""

    def __init__(self, menu: "CopypastaInfoMenu"):
        self.menu = menu
        super().__init__(
            placeholder=t(
                menu.language, "Pick a template to generate...", "選擇一個模板來生成..."
            ),
            options=self._build_options(),
            row=2,
        )

    def _build_options(self):
        start = self.menu.current_page * TEMPLATE_PAGE_SIZE
        page_templates = self.menu.templates[start : start + TEMPLATE_PAGE_SIZE]
        options = []
        for i, tmpl in enumerate(page_templates):
            index = start + i
            preview = tmpl if len(tmpl) <= 100 else tmpl[:97] + "..."
            options.append(
                discord.SelectOption(
                    label=f"#{index}", description=preview, value=str(index)
                )
            )
        return options

    def refresh(self):
        """Called after Previous/Next flips the page, so the dropdown's
        options match the templates actually shown on the new page."""
        self.options = self._build_options()

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        # Ask only for the placeholders THIS template actually contains,
        # not the type's full placeholder pool - a template that doesn't
        # use every placeholder the type has ever seen shouldn't make the
        # user fill in ones it's going to ignore.
        template = self.menu.templates[index]
        placeholders = extract_placeholders(template) or ["text"]
        await interaction.response.send_modal(
            CopypastaValuesModal(self.menu, index, placeholders)
        )


class CopypastaInfoMenu(discord.ui.View):
    """Shows one copypasta type's full detail, with its templates
    paginated 5 at a time (flip buttons only appear if there's more
    than one page), the same way KeywordShowMenu paginates responses
    in keywords_cog.py. Needed because a type with a lot of templates
    can otherwise blow past an embed's practical size.

    Also includes a dropdown (CopypastaTemplateSelect) so a template can
    be picked and generated directly from here, instead of having to
    close the menu and type out a full !copypasta command by hand, plus
    a Random button (CopypastaRandomModal) for when any template from
    the pool is fine, mirroring plain `!copypasta <type> <values>`.
    Either path posts the generated copypasta and then closes this menu
    (see close_menu()), so the same message's Select/buttons can't be
    reused to keep spamming out more generations."""

    def __init__(
        self,
        ctx,
        service: "CopypastaService",
        type_id: str,
        s: dict,
        language: str,
        timeout: int = 180,
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.service = service
        self.type_id = type_id
        self.s = s
        self.language = language
        self.templates = s.get("templates", [])
        self.current_page = 0
        # Set by the command handler right after ctx.send(...) returns, so
        # close_menu() can edit this exact message from inside a modal's
        # on_submit (a different interaction than the one that opened the
        # modal, so it can't just re-use interaction.response for that).
        self.message: discord.Message | None = None

        self.previous_button.label = t(language, "Previous", "上一頁")
        self.next_button.label = t(language, "Next", "下一頁")
        self.jump_button.label = t(language, "🔢 Jump", "🔢 跳頁")
        self.random_button.label = t(language, "🎲 Random", "🎲 隨機")
        self.close_button.label = t(language, "Close", "關閉")

        if self._total_pages() <= 1:
            self.remove_item(self.previous_button)
            self.remove_item(self.next_button)
            self.remove_item(self.jump_button)

        if not self.templates:
            self.remove_item(self.random_button)

        self.template_select = None
        if self.templates:
            self.template_select = CopypastaTemplateSelect(self)
            self.add_item(self.template_select)
        self.service.track_view(self)

    def _total_pages(self):
        return max(1, math.ceil(len(self.templates) / TEMPLATE_PAGE_SIZE))

    def get_embed(self):
        total_pages = self._total_pages()
        self.current_page = max(0, min(self.current_page, total_pages - 1))
        start = self.current_page * TEMPLATE_PAGE_SIZE
        page_templates = self.templates[start : start + TEMPLATE_PAGE_SIZE]

        placeholders = self.s.get("placeholders") or ["text"]
        needed = " ".join(f"{{{name}}}" for name in placeholders)

        embed = discord.Embed(
            title=t(
                self.language,
                f"Copypasta Type: {self.type_id}",
                f"迷因文本類型：{self.type_id}",
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name=t(self.language, "Status", "狀態"),
            value=t(self.language, "Enabled", "已啟用")
            if self.s.get("enabled", True)
            else t(self.language, "Disabled", "已停用"),
            inline=False,
        )
        if page_templates:
            # Discord hard-caps every embed field's `value` at 1024
            # characters. Some templates (long copypasta, e.g. otoge/
            # yuanshen entries) are close to or over that limit on
            # their own, so joining several into one shared field (as
            # this used to do) could blow past 1024 and make the whole
            # `!copypasta info` request fail with a 400 Bad Request.
            # Giving each template its own field means only a single
            # oversized template ever needs truncating, instead of one
            # long template dragging the whole page down with it.
            FIELD_VALUE_LIMIT = 1024
            ELLIPSIS = "…"
            for i, tmpl in enumerate(page_templates):
                value = tmpl
                if len(value) > FIELD_VALUE_LIMIT:
                    value = value[: FIELD_VALUE_LIMIT - len(ELLIPSIS)] + ELLIPSIS
                embed.add_field(name=f"#{start + i}", value=value, inline=False)
        else:
            embed.add_field(
                name=t(self.language, "Templates", "模板"),
                value=t(self.language, "(no templates)", "（無模板）"),
                inline=False,
            )
        embed.set_footer(
            text=t(
                self.language,
                f"Template page {self.current_page + 1}/{total_pages} | {len(self.templates)} template(s) total | "
                f"Pick one below to generate it (only asks for what that template needs), or hit Random for a "
                f"random one, same as: !copypasta {self.type_id} <value for {needed}> (values needed vary by "
                f"which template gets picked). Generating either way closes this menu.",
                f"模板第 {self.current_page + 1}/{total_pages} 頁 | 共 {len(self.templates)} 個模板 | "
                f"從下方選擇一個來生成（只會詢問該模板實際需要的值），或按「隨機」隨機生成一個，等同於："
                f"!copypasta {self.type_id} <對應 {needed} 的值>（實際所需的值視隨機抽中的模板而定）。"
                f"無論哪種方式生成後，此選單都會自動關閉。",
            )
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(
                    self.language,
                    "This isn't your menu to control.",
                    "這不是您可以操作的選單。",
                ),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = (self.current_page - 1) % self._total_pages()
        if self.template_select:
            self.template_select.refresh()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = (self.current_page + 1) % self._total_pages()
        if self.template_select:
            self.template_select.refresh()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def jump_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(CopypastaJumpModal(self, self.language))

    @discord.ui.button(style=discord.ButtonStyle.green, row=1)
    async def random_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Opens CopypastaRandomModal instead of picking a specific
        # template's own placeholders - this asks for the type's whole
        # placeholder pool up front, same as `!copypasta <type> <values>`,
        # since which template actually gets picked (and therefore which
        # of those placeholders are used) is only decided on submit.
        await interaction.response.send_modal(CopypastaRandomModal(self))

    @discord.ui.button(style=discord.ButtonStyle.red, row=0)
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content=t(self.language, "Copypasta menu closed.", "迷因文本選單已關閉。"),
            embed=None,
            view=None,
        )
        self.stop()

    async def close_menu(self):
        """Closes this menu's message (drops the embed/Select/buttons)
        after a copypasta has actually been generated from it via a
        modal, so the same message can't be used to spam out more
        generations - the user has to re-run !copypasta info for another
        one. Called from CopypastaValuesModal/CopypastaRandomModal, whose
        on_submit interaction is separate from the one that opened the
        modal, so it edits the stored message directly instead of going
        through interaction.response like close_button does."""
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(
                    content=t(
                        self.language, "Copypasta menu closed.", "迷因文本選單已關閉。"
                    ),
                    embed=None,
                    view=None,
                )
            except discord.HTTPException:
                pass
