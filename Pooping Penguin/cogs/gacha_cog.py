"""
Free gacha pull system, all under one `!gacha`/`/gacha` command group
(commands.hybrid_group, same pattern keywords_cog.py uses for `!keyword`):

  !gacha              opens the interactive menu (see below) - prefix only,
                       via invoke_without_command=True (see note below)
  !gacha pull         same interactive menu, works as both "!" and "/"
  !gacha target       view/set/clear YOUR OWN personal pity target - pity works
                       toward whatever you set here, independent of every other user.
                       Setting one always goes through a Confirm/Cancel step so a
                       typo or misclick can't silently swap your target.
  !gacha browse       flip through the 3-star roster one character at a time with
                       artwork (if set), then jump straight into the same
                       Confirm/Cancel step to lock one in as your target
  !gacha stats        your total pulls + every 2-star/3-star character you've obtained
                       (alias: !gacha results - prefix only, matching copypasta_cog.py's
                       info/show pattern since slash commands don't support aliases)
  !gacha reset        wipes your own pull history/collection (Confirm/Cancel gated)
  !gacha pool         shows the current rates + full character rosters
  !gacha setfeatured  admin only: sets the banner-wide default target, used as a
                       fallback for anyone who hasn't picked a personal one via
                       `!gacha target`
  !gacha setimage     admin only: points a character at its full artwork URL
                       (data/gacha_images.json) so target-confirm/browse show
                       it, and pulls/record fall back to it as a thumbnail
                       unless setthumbnail below overrides that
  !gacha removeimage  admin only: clears a character's artwork (and its
                       thumbnail override, if any)
  !gacha setthumbnail admin only: points a character at a separate, smaller
                       thumbnail URL shown on pulls/record instead of the
                       full artwork; doesn't touch the full artwork
  !gacha removethumbnail admin only: clears a character's separate thumbnail,
                       falling back to its full artwork again
  !gacha reload       admin only: re-read data/gacha_pool.json AND
                       data/gacha_images.json after a hand edit

The interactive menu (GachaMenuView) covers pull/target/stats/reset
without typing any of those commands: Single Pull / 10x Pull buttons,
a 🎯 Change Target button that swaps in a roster dropdown
(GachaTargetSelect), a 📖 Record button showing the collection with
its own 🗑️ Delete Record -> Confirm/Cancel, and a 🖼️ Browse button
that flips through the 3-star roster one character at a time with
artwork. Picking a target - whether from the dropdown or from
Browse's 🎯 Set as Target button - always lands on a shared
Confirm/Cancel step (pending_target + _show_target_confirm) before
GachaManager.set_user_target() is actually called, so a stray click
can't silently swap someone's target. A Back button everywhere
returns to the main buttons. Every step edits the same message in
place rather than sending new ones. The commands above still work
standalone for anyone who prefers typing them (the plain `!gacha
target <name>` command gets its own equivalent Confirm/Cancel via
GachaTargetConfirmView) - the menu is just another way to reach the
same GachaManager calls.

`setfeatured`'s and `target`'s `character` arguments both have
slash-command autocomplete (GachaCog._character_autocomplete) that
suggests names from the live 3-star roster as you type, so nobody has
to type an exact Japanese string from memory or copy-paste it from
`!gacha pool`.

Note on the bare `!gacha` invocation: invoke_without_command=True makes
"!gacha" (no subcommand) open the menu directly for prefix users,
matching the flat `!gacha` command this used to be. Discord's slash
UI, however, doesn't allow invoking a command *group* directly - only
its subcommands - so slash users need `/gacha pull` for the same
result (same restriction noted in keywords_cog.py/help_cog.py for
`/keyword`). `gacha_pull()` below and the bare group callback share
one `_send_pull_menu()` implementation so they can never drift apart.

The actual rolling/pity/persistence logic lives in gacha_manager.py
(GachaManager) - this file is just commands + Discord UI (embeds,
buttons, dropdowns, autocomplete) on top of it. See gacha_manager.py's
docstring for the pity rule in detail.
"""
import discord
from discord.ext import commands

from config import load_settings
from i18n import t, get_guild_language
from gacha_manager import GachaManager, GachaError, PITY_LIMIT, STARS

# Discord caps a single message at 10 embeds total. Both the Record view
# (one embed per owned character) and multi-pull results (one embed per
# pull) use this to paginate whenever they'd otherwise exceed that cap -
# see GachaCog._build_record_content_and_embeds / _build_multi_result_embeds
# and the Prev/Next controls on GachaMenuView and the standalone
# GachaRecordView.
MAX_EMBEDS_PER_MESSAGE = 10


class GachaTargetSelect(discord.ui.Select):
    """Dropdown listing the live 3-star roster so a target can be picked
    by clicking instead of typing out a Japanese string by hand. Lives
    inside GachaMenuView's target state; the option matching the user's
    current target (if any) comes pre-selected."""

    def __init__(self, menu: "GachaMenuView"):
        self.menu = menu
        roster = menu.cog.manager.get_pool().get("three_star", [])
        current = menu.cog.manager.get_user_target(menu.ctx.author.id)
        options = [discord.SelectOption(label=c[:100], value=c, default=(c == current))
                   for c in roster[:25]]  # Discord caps a select at 25 options
        super().__init__(
            placeholder=t(menu.language, "Choose your target character...", "選擇你的目標角色..."),
            options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if not await self.menu._check_owner(interaction):
            return
        # Don't apply yet - stage it and let the Confirm/Cancel step
        # (GachaMenuView._show_target_confirm) actually call set_user_target.
        self.menu.pending_target = self.values[0]
        await self.menu._show_target_confirm(interaction)


class GachaMenuView(discord.ui.View):
    """One message that covers pulling, picking a personal pity target,
    and viewing/clearing a collection - all through buttons and a
    dropdown instead of typing separate commands. Every action edits
    this same message in place rather than sending a new one, so
    mashing buttons never spams the channel.

    States, each with its own set of items built by a _build_* method
    below: MAIN (Single/10x Pull, Change Target, Record) -> TARGET (the
    roster dropdown) or RECORD (collection + Delete Record) ->
    DELETE_CONFIRM (Confirm/Cancel gate in front of
    GachaManager.reset_user(), same idea as GachaResetConfirmView but
    inline). Back/Cancel always return to the state that opened them.

    This is a second way to reach the same GachaManager calls the
    `!gacha target` / `!gacha stats` / `!gacha reset` commands already
    make - those commands keep working standalone for anyone who
    prefers typing them."""

    def __init__(self, cog: "GachaCog", ctx, language: str, timeout=180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.language = language
        self.message = None
        self.pending_target = None
        self.browse_index = 0
        self.record_page = 0
        self.pull_results = None
        self.pull_user_record = None
        self.pull_target = None
        self.pull_page = 0
        self._build_main()

    async def on_timeout(self):
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(self.language,
                  "This isn't your gacha menu - use `!gacha` to start your own.",
                  "這不是你的抽獎選單 - 使用 `!gacha` 開始你自己的。"),
                ephemeral=True)
            return False
        return True

    # -- item builders: clear_items() first every time so a previous
    # state's buttons/dropdown can never linger onto the next one -------
    def _build_main(self, pull_total_pages: int = 1):
        self.clear_items()
        single = discord.ui.Button(style=discord.ButtonStyle.blurple,
            label=t(self.language, "Single Pull", "單抽"), row=0)
        multi = discord.ui.Button(style=discord.ButtonStyle.green,
            label=t(self.language, "10x Pull", "十連抽"), row=0)
        change_target = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "🎯 Change Target", "🎯 更換目標"), row=1)
        record = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "📖 Record", "📖 紀錄"), row=1)
        browse = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "🖼️ Browse", "🖼️ 瀏覽"), row=1)
        single.callback = self._on_single
        multi.callback = self._on_multi
        change_target.callback = self._on_open_target
        record.callback = self._on_open_record
        browse.callback = self._on_open_browse
        for item in (single, multi, change_target, record, browse):
            self.add_item(item)
        # Only relevant right after a multi-pull whose result count
        # exceeds Discord's 10-embed-per-message cap - a plain 10x pull
        # never needs this, it's headroom for larger pull sizes.
        if pull_total_pages > 1:
            prev_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
                label=t(self.language, "◀ Prev Page", "◀ 上一頁"), row=2)
            next_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
                label=t(self.language, "Next Page ▶", "下一頁 ▶"), row=2)
            prev_btn.callback = self._on_pull_prev
            next_btn.callback = self._on_pull_next
            self.add_item(prev_btn)
            self.add_item(next_btn)

    def _build_target(self):
        self.clear_items()
        self.add_item(GachaTargetSelect(self))
        clear = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "Clear Target", "取消設定"), row=1)
        back = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "◀ Back", "◀ 返回"), row=1)
        clear.callback = self._on_clear_target
        back.callback = self._on_back_to_main
        self.add_item(clear)
        self.add_item(back)

    def _build_target_confirm(self):
        self.clear_items()
        confirm = discord.ui.Button(style=discord.ButtonStyle.green,
            label=t(self.language, "✅ Confirm", "✅ 確認"), row=0)
        cancel = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "Cancel", "取消"), row=0)
        confirm.callback = self._on_confirm_target
        cancel.callback = self._on_cancel_target
        self.add_item(confirm)
        self.add_item(cancel)

    def _build_browse(self):
        self.clear_items()
        prev_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "◀ Prev", "◀ 上一個"), row=0)
        next_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "Next ▶", "下一個 ▶"), row=0)
        set_target = discord.ui.Button(style=discord.ButtonStyle.blurple,
            label=t(self.language, "🎯 Set as Target", "🎯 設為目標"), row=1)
        back = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "◀ Back", "◀ 返回"), row=1)
        prev_btn.callback = self._on_browse_prev
        next_btn.callback = self._on_browse_next
        set_target.callback = self._on_browse_set_target
        back.callback = self._on_back_to_main
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(set_target)
        self.add_item(back)

    def _build_record(self, total_pages: int = 1):
        self.clear_items()
        if total_pages > 1:
            prev_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
                label=t(self.language, "◀ Prev", "◀ 上一頁"), row=0)
            next_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
                label=t(self.language, "Next ▶", "下一頁 ▶"), row=0)
            prev_btn.callback = self._on_record_prev
            next_btn.callback = self._on_record_next
            self.add_item(prev_btn)
            self.add_item(next_btn)
        delete = discord.ui.Button(style=discord.ButtonStyle.red,
            label=t(self.language, "🗑️ Delete Record", "🗑️ 刪除紀錄"), row=1)
        back = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "◀ Back", "◀ 返回"), row=1)
        delete.callback = self._on_open_delete_confirm
        back.callback = self._on_back_to_main
        self.add_item(delete)
        self.add_item(back)

    def _build_delete_confirm(self):
        self.clear_items()
        confirm = discord.ui.Button(style=discord.ButtonStyle.red,
            label=t(self.language, "Confirm Delete", "確認刪除"), row=0)
        cancel = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "Cancel", "取消"), row=0)
        confirm.callback = self._on_confirm_delete
        cancel.callback = self._on_open_record
        self.add_item(confirm)
        self.add_item(cancel)

    # -- state transitions: build the matching items, then edit the
    # message with both the matching embed and this view -----------------
    async def _show_main(self, interaction: discord.Interaction):
        self._build_main()
        embed = self.cog._build_pull_menu_embed(self.language, self.ctx.author.id)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def _show_target(self, interaction: discord.Interaction):
        self._build_target()
        embed = self.cog._build_target_prompt_embed(self.language, self.ctx.author.id)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def _show_target_confirm(self, interaction: discord.Interaction):
        self._build_target_confirm()
        embed = self.cog._build_target_confirm_embed(self.language, self.pending_target)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def _show_browse(self, interaction: discord.Interaction):
        self._build_browse()
        roster = self.cog.manager.get_pool().get("three_star", [])
        embed = self.cog._build_browse_embed(self.language, roster, self.browse_index)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def _show_record(self, interaction: discord.Interaction):
        # Multi-embed-with-thumbnail style: one small embed per owned
        # character (like a pull result card) instead of one big embed
        # with a text list. The header (name/pulls/pity/target) goes in
        # the message content since it no longer fits as embed fields.
        content, embeds, total_pages = self.cog._build_record_content_and_embeds(
            self.language, self.ctx.author, self.record_page)
        self.record_page = max(0, min(self.record_page, total_pages - 1))
        self._build_record(total_pages)
        await interaction.response.edit_message(content=content, embeds=embeds, view=self)

    async def _show_delete_confirm(self, interaction: discord.Interaction):
        self._build_delete_confirm()
        embed = self.cog._build_delete_confirm_embed(self.language)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    # -- button/select callbacks -------------------------------------------
    async def _on_single(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self.cog.do_pull(interaction, self, count=1)

    async def _on_multi(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self.cog.do_pull(interaction, self, count=10)

    async def _on_pull_prev(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        total_pages = max(1, -(-len(self.pull_results) // MAX_EMBEDS_PER_MESSAGE))
        self.pull_page = (self.pull_page - 1) % total_pages
        await self.cog._show_pull_page(interaction, self)

    async def _on_pull_next(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        total_pages = max(1, -(-len(self.pull_results) // MAX_EMBEDS_PER_MESSAGE))
        self.pull_page = (self.pull_page + 1) % total_pages
        await self.cog._show_pull_page(interaction, self)

    async def _on_open_target(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self._show_target(interaction)

    async def _on_confirm_target(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.cog.manager.set_user_target(interaction.user.id, self.pending_target)
        self.pending_target = None
        await self._show_main(interaction)

    async def _on_cancel_target(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.pending_target = None
        await self._show_main(interaction)

    async def _on_clear_target(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.cog.manager.clear_user_target(interaction.user.id)
        await self._show_main(interaction)

    async def _on_open_browse(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.browse_index = 0
        await self._show_browse(interaction)

    async def _on_browse_prev(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        roster = self.cog.manager.get_pool().get("three_star", [])
        if roster:
            self.browse_index = (self.browse_index - 1) % len(roster)
        await self._show_browse(interaction)

    async def _on_browse_next(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        roster = self.cog.manager.get_pool().get("three_star", [])
        if roster:
            self.browse_index = (self.browse_index + 1) % len(roster)
        await self._show_browse(interaction)

    async def _on_browse_set_target(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        roster = self.cog.manager.get_pool().get("three_star", [])
        if not roster:
            return
        self.pending_target = roster[self.browse_index]
        await self._show_target_confirm(interaction)

    async def _on_open_record(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.record_page = 0
        await self._show_record(interaction)

    async def _on_record_prev(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        total_pages = self.cog._record_total_pages(self.ctx.author)
        self.record_page = (self.record_page - 1) % total_pages
        await self._show_record(interaction)

    async def _on_record_next(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        total_pages = self.cog._record_total_pages(self.ctx.author)
        self.record_page = (self.record_page + 1) % total_pages
        await self._show_record(interaction)

    async def _on_open_delete_confirm(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self._show_delete_confirm(interaction)

    async def _on_confirm_delete(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.cog.manager.reset_user(interaction.user.id)
        await self._show_main(interaction)

    async def _on_back_to_main(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self._show_main(interaction)


class GachaResetConfirmView(discord.ui.View):
    """Confirm/Cancel gate in front of GachaManager.reset_user(), so a
    stray command invocation can't silently wipe someone's collection."""

    def __init__(self, cog: "GachaCog", ctx, language: str, timeout=30):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.language = language

        self.confirm_button.label = t(language, "Confirm Reset", "確認重置")
        self.cancel_button.label = t(language, "Cancel", "取消")

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(self.language, "This confirmation isn't for you.", "這不是給你的確認訊息。"),
                ephemeral=True)
            return False
        return True

    async def _lock(self, interaction, content):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=content, embed=None, view=self)
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.red)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        self.cog.manager.reset_user(interaction.user.id)
        await self._lock(interaction, t(self.language,
            "Your gacha record has been reset.", "你的抽獎紀錄已重置。"))

    @discord.ui.button(style=discord.ButtonStyle.grey)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        await self._lock(interaction, t(self.language, "Reset cancelled.", "已取消重置。"))


class GachaTargetConfirmView(discord.ui.View):
    """Confirm/Cancel gate in front of GachaManager.set_user_target(), used
    by the standalone `!gacha target <name>` command (the interactive menu
    has its own equivalent step inside GachaMenuView)."""

    def __init__(self, cog: "GachaCog", ctx, language: str, character: str, timeout=30):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.language = language
        self.character = character

        self.confirm_button.label = t(language, "✅ Confirm", "✅ 確認")
        self.cancel_button.label = t(language, "Cancel", "取消")

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(self.language, "This confirmation isn't for you.", "這不是給你的確認訊息。"),
                ephemeral=True)
            return False
        return True

    async def _lock(self, interaction, content):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=content, embed=None, view=self)
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        self.cog.manager.set_user_target(interaction.user.id, self.character)
        await self._lock(interaction, t(self.language,
            f"Your pity target is now **{self.character}**.",
            f"你的保底目標現在是 **{self.character}**。"))

    @discord.ui.button(style=discord.ButtonStyle.grey)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        await self._lock(interaction, t(self.language, "Target change cancelled.", "已取消更換目標。"))


class GachaRecordView(discord.ui.View):
    """Prev/Next pagination for the standalone `!gacha stats` command's
    multi-embed collection list, for the same reason GachaMenuView's
    record state has its own Prev/Next - a collection can span more
    owned characters than Discord's 10-embed-per-message limit."""

    def __init__(self, cog: "GachaCog", ctx, language: str, member: discord.Member, timeout=180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.language = language
        self.member = member
        self.page = 0
        self.message = None
        self._build()

    def _build(self):
        self.clear_items()
        prev_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "◀ Prev", "◀ 上一頁"), row=0)
        next_btn = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "Next ▶", "下一頁 ▶"), row=0)
        prev_btn.callback = self._on_prev
        next_btn.callback = self._on_next
        self.add_item(prev_btn)
        self.add_item(next_btn)

    async def on_timeout(self):
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                t(self.language, "This isn't your gacha menu - use `!gacha stats` to view your own.",
                  "這不是你的抽獎選單 - 使用 `!gacha stats` 查看你自己的紀錄。"),
                ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction):
        content, embeds, total_pages = self.cog._build_record_content_and_embeds(
            self.language, self.member, self.page)
        self.page = max(0, min(self.page, total_pages - 1))
        await interaction.response.edit_message(content=content, embeds=embeds, view=self)

    async def _on_prev(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        total_pages = self.cog._record_total_pages(self.member)
        self.page = (self.page - 1) % total_pages
        await self._refresh(interaction)

    async def _on_next(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        total_pages = self.cog._record_total_pages(self.member)
        self.page = (self.page + 1) % total_pages
        await self._refresh(interaction)


class GachaCog(commands.Cog, name="gacha"):
    def __init__(self, bot):
        self.bot = bot
        self.manager = GachaManager()

    def _lang(self, ctx):
        return get_guild_language(load_settings(), ctx.guild.id)

    # -- autocomplete -----------------------------------------------------
    async def _character_autocomplete(self, interaction: discord.Interaction, current: str):
        """Suggests 3-star characters from the live pool as the admin
        types into `!gacha setfeatured`'s character argument, so they
        don't need to hand-type/copy-paste the exact roster string."""
        roster = self.manager.get_pool().get("three_star", [])
        current_lower = current.lower()
        matches = [c for c in roster if current_lower in c.lower()] if current else roster
        return [discord.app_commands.Choice(name=c[:100], value=c) for c in matches[:25]]

    # -- shared result rendering -------------------------------------------
    # Embed accent color per rarity, used on both the single-pull embed
    # and each per-card embed in a multi-pull.
    _RARITY_COLOR = {
        "three_star": discord.Color.gold(),
        "two_star": discord.Color.purple(),
        "one_star": discord.Color.light_grey(),
    }

    def _format_pull_line(self, result):
        star = STARS[result["rarity"]]
        character = result["character"] if result["character"] is not None else "?"
        tag = ""
        if result["rarity"] == "three_star" and result["character"] == result["target"]:
            tag = " 🌟" if not result["forced"] else " 🌟✅"
        return f"{star} {character}{tag}"

    def _build_single_result_embed(self, language, result, user_record, target):
        """Full-detail embed for a single pull: rarity/name line, total
        pulls / pity / target fields, and a thumbnail of the character if
        art is on file for them."""
        embed = discord.Embed(
            title=t(language, "🎰 Gacha Results", "🎰 抽獎結果"),
            description=self._format_pull_line(result),
            color=self._RARITY_COLOR[result["rarity"]],
        )
        embed.add_field(name=t(language, "Total Pulls", "總抽數"),
                         value=str(user_record["total_pulls"]), inline=True)
        embed.add_field(name=t(language, "Pity", "保底計數"),
                         value=f"{user_record['pity_counter']}/{PITY_LIMIT}", inline=True)
        embed.add_field(name=t(language, "Your Target", "你的目標"),
                         value=target or t(language, "None set", "未設定"), inline=True)
        embed.set_footer(text=t(language,
            "🌟 = your target character. Set your own with `!gacha target`.",
            "🌟 = 你的目標角色。使用 `!gacha target` 設定屬於你自己的目標。"))
        if result["character"]:
            thumb = self.manager.get_thumbnail(result["character"])
            if thumb:
                embed.set_thumbnail(url=thumb)
        return embed

    def _build_multi_result_embeds(self, language, results, user_record, target, page: int = 0):
        """One small embed per pull instead of one big embed with a text
        list - each gets its own thumbnail, same layout other gacha bots
        use for pull results. Discord caps a message at
        MAX_EMBEDS_PER_MESSAGE embeds, which conveniently matches a 10x
        pull exactly - but this paginates so a larger pull size still
        works. Returns (header_text, [embeds], total_pages) - the header
        carries the summary stats (and page indicator, if paginated)
        since they no longer fit as fields on any single per-card embed."""
        total_pages = max(1, -(-len(results) // MAX_EMBEDS_PER_MESSAGE))
        page = max(0, min(page, total_pages - 1))
        page_results = results[page * MAX_EMBEDS_PER_MESSAGE:(page + 1) * MAX_EMBEDS_PER_MESSAGE]
        start_index = page * MAX_EMBEDS_PER_MESSAGE

        page_note = t(language, f" (Page {page + 1}/{total_pages})", f"（第 {page + 1}/{total_pages} 頁）") \
            if total_pages > 1 else ""
        header = t(language,
            f"🎰 **Gacha Results** - {len(results)} pulls{page_note}\n"
            f"Total Pulls: **{user_record['total_pulls']}** ｜ "
            f"Pity: **{user_record['pity_counter']}/{PITY_LIMIT}** ｜ "
            f"Target: **{target or 'None set'}**\n"
            f"-# 🌟 = your target character. Set your own with `!gacha target`.",
            f"🎰 **抽獎結果** - {len(results)} 抽{page_note}\n"
            f"總抽數：**{user_record['total_pulls']}** ｜ "
            f"保底：**{user_record['pity_counter']}/{PITY_LIMIT}** ｜ "
            f"目標：**{target or '未設定'}**\n"
            f"-# 🌟 = 你的目標角色。使用 `!gacha target` 設定屬於你自己的目標。")
        embeds = []
        for i, r in enumerate(page_results, start=start_index + 1):
            embed = discord.Embed(
                description=f"**{i}.** {self._format_pull_line(r)}",
                color=self._RARITY_COLOR[r["rarity"]],
            )
            if r["character"]:
                thumb = self.manager.get_thumbnail(r["character"])
                if thumb:
                    embed.set_thumbnail(url=thumb)
            embeds.append(embed)
        return header, embeds, total_pages

    async def do_pull(self, interaction: discord.Interaction, view: "GachaMenuView", count: int):
        results, user_record = self.manager.pull(interaction.user.id, count)
        target = self.manager.effective_target(interaction.user.id)
        if count == 1:
            embed = self._build_single_result_embed(view.language, results[0], user_record, target)
            view.pull_results = None
            view._build_main()
            await interaction.response.edit_message(content=None, embeds=[embed], view=view)
        else:
            view.pull_results = results
            view.pull_user_record = user_record
            view.pull_target = target
            view.pull_page = 0
            await self._show_pull_page(interaction, view)

    async def _show_pull_page(self, interaction: discord.Interaction, view: "GachaMenuView"):
        """Renders view.pull_page of view.pull_results and rebuilds the
        main buttons with a Prev/Next row added on top if the results
        span more than one page (see MAX_EMBEDS_PER_MESSAGE)."""
        content, embeds, total_pages = self._build_multi_result_embeds(
            view.language, view.pull_results, view.pull_user_record, view.pull_target, page=view.pull_page)
        view.pull_page = max(0, min(view.pull_page, total_pages - 1))
        view._build_main(pull_total_pages=total_pages)
        await interaction.response.edit_message(content=content, embeds=embeds, view=view)

    def _build_pull_menu_embed(self, language, user_id):
        pool = self.manager.get_pool()
        rates = pool["rates"]
        target = self.manager.effective_target(user_id)
        has_own_target = bool(self.manager.get_user_target(user_id))

        embed = discord.Embed(
            title=t(language, "🎰 Penguin Gacha", "🎰 企鵝轉蛋"),
            description=t(language,
                "Pulling is free! Choose Single Pull or 10x Pull below.",
                "抽獎完全免費！在下方選擇單抽或十連抽。"),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name=t(language, "Rates", "機率"),
            value=f"★★★ {rates['three_star']}%\n★★ {rates['two_star']}%\n★ {rates['one_star']}%",
            inline=True)
        target_label = t(language, "Your Target", "你的目標") if has_own_target else \
            t(language, "Your Target (default)", "你的目標（預設）")
        embed.add_field(
            name=target_label,
            value=target or t(language, "None set", "未設定"),
            inline=True)
        embed.add_field(
            name=t(language, "Pity", "保底"),
            value=t(language,
                f"Guaranteed YOUR target within {PITY_LIMIT} pulls. Use 🎯 Change Target "
                "below to pick your own. Changing a target doesn't reset your progress.",
                f"{PITY_LIMIT} 抽內保底必得你自己的目標角色。使用下方的 🎯 更換目標 選擇專屬於"
                "你的目標。更換目標不會重置你的保底計數。"),
            inline=False)
        return embed

    def _build_target_prompt_embed(self, language, user_id):
        current = self.manager.get_user_target(user_id)
        fallback = self.manager.get_featured()
        embed = discord.Embed(
            title=t(language, "🎯 Choose Your Target", "🎯 選擇你的目標"),
            description=t(language,
                "Pick a 3-star character from the dropdown below - pity works toward "
                "whatever you set here, independent of everyone else.",
                "從下方的下拉選單選擇一位三星角色 - 保底將以你在此設定的角色為目標，與其他"
                "使用者互不影響。"),
            color=discord.Color.purple(),
        )
        embed.add_field(name=t(language, "Current Target", "目前的目標"),
                         value=current or t(language, "None set", "未設定"), inline=True)
        if not current and fallback:
            embed.add_field(name=t(language, "Currently Defaulting To", "目前預設為"),
                             value=fallback, inline=True)
        return embed

    def _build_target_confirm_embed(self, language, character):
        embed = discord.Embed(
            title=t(language, "🎯 Confirm Your Target", "🎯 確認你的目標"),
            description=t(language,
                f"Set your pity target to **{character}**?",
                f"確定將保底目標設定為 **{character}** 嗎？"),
            color=discord.Color.purple(),
        )
        image = self.manager.get_image(character)
        if image:
            embed.set_image(url=image)
        else:
            embed.set_footer(text=t(language,
                "No artwork on file for this character yet.",
                "此角色尚未設定圖片。"))
        return embed

    def _build_browse_embed(self, language, roster, index):
        if not roster:
            return discord.Embed(
                title=t(language, "🖼️ Browse Characters", "🖼️ 瀏覽角色"),
                description=t(language, "The 3-star pool is empty.", "三星名單目前是空的。"),
                color=discord.Color.purple())

        character = roster[index]
        embed = discord.Embed(
            title=f"★★★ {character}",
            description=t(language,
                f"Character {index + 1} of {len(roster)}",
                f"第 {index + 1} 個，共 {len(roster)} 個"),
            color=discord.Color.purple(),
        )
        image = self.manager.get_image(character)
        if image:
            embed.set_image(url=image)
        else:
            embed.set_footer(text=t(language,
                "No artwork on file for this character yet.",
                "此角色尚未設定圖片。"))
        return embed

    def _record_entries(self, member: discord.Member):
        """Every character `member` owns as a flat, ordered list of
        (rarity, name, count) - 3-star first, then 2-star - so
        pagination can just slice straight through it.

        Within each rarity, characters are ordered to match their
        position in that rarity's roster array in data/gacha_pool.json
        (the same order `!gacha pool`/setfeatured autocomplete use),
        not alphabetically - so re-ordering the roster in the JSON and
        running `!gacha reload` re-orders the collection view too. Any
        obtained character no longer present in the current roster
        (e.g. removed in a hand edit) is appended at the end,
        alphabetically, so it doesn't just disappear from the record."""
        pool = self.manager.get_pool()

        def ordered(rarity, counts):
            roster = pool.get(rarity, [])
            entries, seen = [], set()
            for name in roster:
                if name in counts:
                    entries.append((rarity, name, counts[name]))
                    seen.add(name)
            for name in sorted(counts):
                if name not in seen:
                    entries.append((rarity, name, counts[name]))
            return entries

        user = self.manager.get_user(member.id)
        entries = ordered("three_star", user.get("three_star", {}))
        entries += ordered("two_star", user.get("two_star", {}))
        return entries

    def _record_total_pages(self, member: discord.Member) -> int:
        return max(1, -(-len(self._record_entries(member)) // MAX_EMBEDS_PER_MESSAGE))

    def _build_record_content_and_embeds(self, language, member: discord.Member, page: int = 0):
        """Multi-embed-with-thumbnail collection view: the message
        content carries the header (name/pulls/pity/target, matching
        _build_multi_result_embeds's header pattern), and one small
        embed per owned character follows - rarity, name, count, and a
        thumbnail if artwork is on file. Discord caps a message at 10
        embeds, so this paginates MAX_EMBEDS_PER_MESSAGE entries at a time.
        Returns (content, embeds, total_pages)."""
        user = self.manager.get_user(member.id)
        target = self.manager.effective_target(member.id)
        entries = self._record_entries(member)
        total_pages = max(1, -(-len(entries) // MAX_EMBEDS_PER_MESSAGE))
        page = max(0, min(page, total_pages - 1))
        page_entries = entries[page * MAX_EMBEDS_PER_MESSAGE:(page + 1) * MAX_EMBEDS_PER_MESSAGE]

        page_note = t(language, f" (Page {page + 1}/{total_pages})", f"（第 {page + 1}/{total_pages} 頁）") \
            if total_pages > 1 else ""
        content = t(language,
            f"📖 **{member.display_name}'s Gacha Collection**{page_note}\n"
            f"Total Pulls: **{user['total_pulls']}** ｜ "
            f"Pity: **{user['pity_counter']}/{PITY_LIMIT}** ｜ "
            f"Target: **{target or 'None set'}**",
            f"📖 **{member.display_name} 的抽獎收藏**{page_note}\n"
            f"總抽數：**{user['total_pulls']}** ｜ "
            f"保底：**{user['pity_counter']}/{PITY_LIMIT}** ｜ "
            f"目標：**{target or '未設定'}**")

        if not entries:
            embed = discord.Embed(
                description=t(language, "No characters obtained yet.", "尚未獲得任何角色。"),
                color=discord.Color.blue())
            return content, [embed], total_pages

        embeds = []
        for rarity, name, count in page_entries:
            embed = discord.Embed(
                description=f"{STARS[rarity]} {name} ×{count}",
                color=self._RARITY_COLOR[rarity],
            )
            thumb = self.manager.get_thumbnail(name)
            if thumb:
                embed.set_thumbnail(url=thumb)
            embeds.append(embed)
        return content, embeds, total_pages

    def _build_delete_confirm_embed(self, language):
        return discord.Embed(
            title=t(language, "⚠️ Delete Your Record?", "⚠️ 刪除你的紀錄？"),
            description=t(language,
                "This clears your total pulls, pity counter, target, and collection - "
                "it cannot be undone.",
                "這將清除你的總抽數、保底計數、目標與收藏 - 且無法復原。"),
            color=discord.Color.red(),
        )

    async def _send_pull_menu(self, ctx):
        language = self._lang(ctx)
        embed = self._build_pull_menu_embed(language, ctx.author.id)
        view = GachaMenuView(self, ctx, language)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    # -- command group -------------------------------------------------
    @commands.hybrid_group(
        name="gacha", invoke_without_command=True,
        description="Pulls from the gacha banner - choose single or 10x pull.")
    async def gacha(self, ctx):
        """Opens the gacha menu - pull for free with Single Pull or 10x Pull."""
        await self._send_pull_menu(ctx)

    @gacha.command(
        name="pull",
        description="Opens the gacha menu - pull for free with Single Pull or 10x Pull.")
    async def gacha_pull(self, ctx):
        """Opens the gacha menu - pull for free with Single Pull or 10x Pull."""
        await self._send_pull_menu(ctx)

    @gacha.command(
        name="target",
        description="View, set, or clear YOUR OWN personal pity target (independent of other users).")
    @discord.app_commands.describe(character="A 3-star character to pity toward - leave blank to view, or use \"clear\" to unset")
    async def gacha_target(self, ctx, *, character: str = None):
        """View, set, or clear your own personal pity target."""
        language = self._lang(ctx)

        if character is None:
            current = self.manager.get_user_target(ctx.author.id)
            if current:
                await ctx.send(t(language,
                    f"Your pity target is **{current}**. Use `!gacha target <name>` to "
                    "change it, or `!gacha target clear` to unset.",
                    f"你的保底目標是 **{current}**。使用 `!gacha target <角色>` 更改，或使用 "
                    "`!gacha target clear` 取消設定。"))
            else:
                fallback = self.manager.get_featured()
                await ctx.send(t(language,
                    "You haven't set a personal target yet"
                    + (f" - currently defaulting to the banner's **{fallback}**."
                       if fallback else ".")
                    + " Use `!gacha target <name>` to pick your own.",
                    "你尚未設定個人目標"
                    + (f" - 目前預設為轉蛋池的 **{fallback}**。" if fallback else "。")
                    + " 使用 `!gacha target <角色>` 選擇屬於你自己的目標。"))
            return

        if character.strip().lower() == "clear":
            self.manager.clear_user_target(ctx.author.id)
            await ctx.send(t(language,
                "Your personal target has been cleared.",
                "你的個人目標已取消設定。"))
            return

        if character not in self.manager.get_pool().get("three_star", []):
            await ctx.send(t(language,
                f"'{character}' is not in the 3-star pool. Start typing to see suggestions.",
                f"'{character}' 不在三星名單中。開始輸入即可看到建議選項。"))
            return

        image = self.manager.get_image(character)
        embed = discord.Embed(
            title=t(language, "🎯 Confirm Your Target", "🎯 確認你的目標"),
            description=t(language,
                f"Set your pity target to **{character}**? This is independent of "
                "everyone else's target, and your existing pity progress is unaffected.",
                f"確定將保底目標設定為 **{character}** 嗎？這與其他使用者的目標互不影響，"
                "且不會影響你現有的保底計數。"),
            color=discord.Color.purple(),
        )
        if image:
            embed.set_image(url=image)
        view = GachaTargetConfirmView(self, ctx, language, character)
        await ctx.send(embed=embed, view=view)

    @gacha_target.autocomplete("character")
    async def gacha_target_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._character_autocomplete(interaction, current)

    @gacha.command(
        name="stats", aliases=["results"],
        description="Shows your total pulls and every 2-star/3-star character you've obtained.")
    async def gacha_stats(self, ctx):
        """Shows your total pulls and every 2-star/3-star character you've obtained."""
        language = self._lang(ctx)
        content, embeds, total_pages = self._build_record_content_and_embeds(language, ctx.author, page=0)
        if total_pages > 1:
            view = GachaRecordView(self, ctx, language, ctx.author)
            message = await ctx.send(content=content, embeds=embeds, view=view)
            view.message = message
        else:
            await ctx.send(content=content, embeds=embeds)

    @gacha.command(
        name="reset",
        description="Clears your own gacha pull history and collection.")
    async def gacha_reset(self, ctx):
        """Clears your own gacha pull history and collection."""
        language = self._lang(ctx)
        view = GachaResetConfirmView(self, ctx, language)
        await ctx.send(t(language,
            "Are you sure you want to reset your gacha record? This clears your total "
            "pulls, pity counter, and collection - it cannot be undone.",
            "你確定要重置你的抽獎紀錄嗎？這將清除你的總抽數、保底計數與收藏 - 且無法復原。"),
            view=view)

    @gacha.command(
        name="browse",
        description="Flip through the 3-star roster with artwork before picking a target.")
    async def gacha_browse(self, ctx):
        """Flip through the 3-star roster with artwork before picking a target."""
        language = self._lang(ctx)
        view = GachaMenuView(self, ctx, language)
        view._build_browse()
        roster = self.manager.get_pool().get("three_star", [])
        embed = self._build_browse_embed(language, roster, 0)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @gacha.command(
        name="pool",
        description="Shows the current gacha rates and character rosters.")
    async def gacha_pool(self, ctx):
        """Shows the current gacha rates and character rosters."""
        language = self._lang(ctx)
        pool = self.manager.get_pool()
        rates = pool["rates"]

        embed = discord.Embed(title=t(language, "🎰 Gacha Pool", "🎰 轉蛋池"), color=discord.Color.purple())
        embed.add_field(
            name=t(language, "Rates", "機率"),
            value=f"★★★ {rates['three_star']}%\n★★ {rates['two_star']}%\n★ {rates['one_star']}%",
            inline=False)
        embed.add_field(name=t(language, "Default Target (定軌)", "預設目標（定軌）"),
                         value=t(language,
                             (pool.get("featured") or "-") +
                             " - used only for users who haven't set `!gacha target` themselves",
                             (pool.get("featured") or "-") +
                             " - 僅適用於尚未使用 `!gacha target` 設定個人目標的使用者"),
                         inline=False)
        embed.add_field(name=t(language, "3-Star Roster", "三星名單"),
                         value=("\n".join(pool["three_star"]) or "-")[:1024], inline=False)
        embed.add_field(name=t(language, "2-Star Roster", "二星名單"),
                         value=("\n".join(pool["two_star"]) or "-")[:1024], inline=False)
        embed.add_field(name=t(language, "1-Star Roster", "一星名單"),
                         value=("\n".join(pool["one_star"]) or "-")[:1024], inline=False)
        embed.set_footer(text=t(language,
            "Admins can hand-edit data/gacha_pool.json then run !gacha reload to apply it live.",
            "管理員可直接編輯 data/gacha_pool.json，再執行 !gacha reload 即可即時套用變更。"))
        await ctx.send(embed=embed)

    @gacha.command(
        name="setfeatured",
        description="Sets the banner's default target for users without a personal one (Admin only).")
    @discord.app_commands.describe(character="A 3-star character already in the pool")
    @commands.has_permissions(administrator=True)
    async def gacha_setfeatured(self, ctx, *, character: str):
        """Sets the banner's default target for users who haven't picked their own (Admin only)."""
        language = self._lang(ctx)
        try:
            self.manager.set_featured(character)
        except GachaError:
            await ctx.send(t(language,
                f"'{character}' is not in the 3-star pool. Start typing to see suggestions.",
                f"'{character}' 不在三星名單中。開始輸入即可看到建議選項。"))
            return
        await ctx.send(t(language,
            f"Default target set to **{character}**. This only affects users who haven't "
            "set their own with `!gacha target`; existing pity progress is unaffected.",
            f"預設目標已設為 **{character}**。這僅影響尚未使用 `!gacha target` 設定個人目標的"
            "使用者；現有的保底計數不受影響。"))

    @gacha_setfeatured.autocomplete("character")
    async def gacha_setfeatured_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._character_autocomplete(interaction, current)

    @gacha.command(
        name="setimage",
        description="Sets the artwork shown for a character (Admin only).")
    @discord.app_commands.describe(
        character="A character name - doesn't have to be in the 3-star pool",
        url="Direct image URL (must end up rendering as an image, e.g. .png/.jpg/.gif/.webp)")
    @commands.has_permissions(administrator=True)
    async def gacha_setimage(self, ctx, character: str, url: str):
        """Sets the artwork shown for a character (Admin only)."""
        language = self._lang(ctx)
        self.manager.set_image(character, url)
        embed = discord.Embed(
            title=t(language, "Image Set", "圖片已設定"),
            description=t(language,
                f"**{character}** will now show this as its full artwork. It's also used "
                "as the thumbnail on pull results and the collection view unless a "
                "separate thumbnail is set with `!gacha setthumbnail`.",
                f"**{character}** 之後將以此作為完整圖片。除非使用 `!gacha setthumbnail` "
                "另外設定縮圖，否則抽卡結果與收藏頁面也會使用此圖作為縮圖。"),
            color=discord.Color.green(),
        )
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @gacha_setimage.autocomplete("character")
    async def gacha_setimage_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._character_autocomplete(interaction, current)

    @gacha.command(
        name="removeimage",
        description="Removes a character's artwork (Admin only).")
    @discord.app_commands.describe(character="The character to remove artwork for")
    @commands.has_permissions(administrator=True)
    async def gacha_removeimage(self, ctx, *, character: str):
        """Removes a character's artwork (Admin only)."""
        language = self._lang(ctx)
        self.manager.remove_image(character)
        await ctx.send(t(language,
            f"Removed the image (if any) for **{character}**.",
            f"已移除 **{character}** 的圖片（如果有的話）。"))

    @gacha_removeimage.autocomplete("character")
    async def gacha_removeimage_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._character_autocomplete(interaction, current)

    @gacha.command(
        name="setthumbnail",
        description="Sets a separate, smaller thumbnail for a character (Admin only).")
    @discord.app_commands.describe(
        character="A character name - doesn't have to be in the 3-star pool",
        url="Direct image URL for the small thumbnail (e.g. .png/.jpg/.gif/.webp)")
    @commands.has_permissions(administrator=True)
    async def gacha_setthumbnail(self, ctx, character: str, url: str):
        """Sets a separate, smaller thumbnail for a character (Admin only)."""
        language = self._lang(ctx)
        self.manager.set_thumbnail(character, url)
        embed = discord.Embed(
            title=t(language, "Thumbnail Set", "縮圖已設定"),
            description=t(language,
                f"**{character}** will now show this smaller image on pull results and "
                "the collection view instead of its full artwork. The full artwork "
                "(browse/target-confirm) is unaffected.",
                f"**{character}** 之後在抽卡結果與收藏頁面將顯示此較小的圖片，取代完整圖片。"
                "瀏覽角色／確認目標時顯示的完整圖片不受影響。"),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=url)
        await ctx.send(embed=embed)

    @gacha_setthumbnail.autocomplete("character")
    async def gacha_setthumbnail_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._character_autocomplete(interaction, current)

    @gacha.command(
        name="removethumbnail",
        description="Removes a character's separate thumbnail, falling back to full artwork (Admin only).")
    @discord.app_commands.describe(character="The character to remove the thumbnail for")
    @commands.has_permissions(administrator=True)
    async def gacha_removethumbnail(self, ctx, *, character: str):
        """Removes a character's separate thumbnail (Admin only)."""
        language = self._lang(ctx)
        self.manager.remove_thumbnail(character)
        await ctx.send(t(language,
            f"Removed the separate thumbnail (if any) for **{character}**. "
            "Pull results and the collection view will fall back to its full artwork.",
            f"已移除 **{character}** 的獨立縮圖（如果有的話）。抽卡結果與收藏頁面將改用完整圖片。"))

    @gacha_removethumbnail.autocomplete("character")
    async def gacha_removethumbnail_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._character_autocomplete(interaction, current)


    @gacha.command(
        name="reload",
        description="Reloads the gacha pool from data/gacha_pool.json (Admin only).")
    @commands.has_permissions(administrator=True)
    async def gacha_reload(self, ctx):
        """Reloads the gacha pool from data/gacha_pool.json (Admin only)."""
        language = self._lang(ctx)
        self.manager.reload_pool()
        await ctx.send(t(language,
            "Gacha pool and character artwork reloaded from disk.",
            "已從磁碟重新載入轉蛋池與角色圖片。"))


async def setup(bot):
    await bot.add_cog(GachaCog(bot))