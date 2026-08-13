"""
Free gacha pull system, all under one `!gacha`/`/gacha` command group
(commands.hybrid_group, same pattern keywords_cog.py uses for `!keyword`):

  !gacha              opens the interactive menu (see below) - prefix only,
                       via invoke_without_command=True (see note below)
  !gacha pull         same interactive menu, works as both "!" and "/"
  !gacha target       view/set/clear YOUR OWN personal pity target - pity works
                       toward whatever you set here, independent of every other user
  !gacha stats        your total pulls + every 2-star/3-star character you've obtained
                       (alias: !gacha results - prefix only, matching copypasta_cog.py's
                       info/show pattern since slash commands don't support aliases)
  !gacha reset        wipes your own pull history/collection (Confirm/Cancel gated)
  !gacha pool         shows the current rates + full character rosters
  !gacha setfeatured  admin only: sets the banner-wide default target, used as a
                       fallback for anyone who hasn't picked a personal one via
                       `!gacha target`
  !gacha reload       admin only: re-read data/gacha_pool.json after a hand edit

The interactive menu (GachaMenuView) covers pull/target/stats/reset
without typing any of those commands: Single Pull / 10x Pull buttons,
a 🎯 Change Target button that swaps in a roster dropdown
(GachaTargetSelect), a 📖 Record button showing the collection with
its own 🗑️ Delete Record -> Confirm/Cancel, and a Back button to
return to the main buttons. Every step edits the same message in
place rather than sending new ones. The commands above still work
standalone for anyone who prefers typing them - the menu is just
another way to reach the same GachaManager calls.

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
        self.menu.cog.manager.set_user_target(interaction.user.id, self.values[0])
        await self.menu._show_main(interaction)


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
    def _build_main(self):
        self.clear_items()
        single = discord.ui.Button(style=discord.ButtonStyle.blurple,
            label=t(self.language, "Single Pull", "單抽"), row=0)
        multi = discord.ui.Button(style=discord.ButtonStyle.green,
            label=t(self.language, "10x Pull", "十連抽"), row=0)
        change_target = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "🎯 Change Target", "🎯 更換目標"), row=1)
        record = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "📖 Record", "📖 紀錄"), row=1)
        single.callback = self._on_single
        multi.callback = self._on_multi
        change_target.callback = self._on_open_target
        record.callback = self._on_open_record
        for item in (single, multi, change_target, record):
            self.add_item(item)

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

    def _build_record(self):
        self.clear_items()
        delete = discord.ui.Button(style=discord.ButtonStyle.red,
            label=t(self.language, "🗑️ Delete Record", "🗑️ 刪除紀錄"), row=0)
        back = discord.ui.Button(style=discord.ButtonStyle.grey,
            label=t(self.language, "◀ Back", "◀ 返回"), row=0)
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
        await interaction.response.edit_message(embed=embed, view=self)

    async def _show_target(self, interaction: discord.Interaction):
        self._build_target()
        embed = self.cog._build_target_prompt_embed(self.language, self.ctx.author.id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _show_record(self, interaction: discord.Interaction):
        self._build_record()
        embed = self.cog._build_record_embed(self.language, self.ctx.author)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _show_delete_confirm(self, interaction: discord.Interaction):
        self._build_delete_confirm()
        embed = self.cog._build_delete_confirm_embed(self.language)
        await interaction.response.edit_message(embed=embed, view=self)

    # -- button/select callbacks -------------------------------------------
    async def _on_single(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self.cog.do_pull(interaction, self, count=1)

    async def _on_multi(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self.cog.do_pull(interaction, self, count=10)

    async def _on_open_target(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self._show_target(interaction)

    async def _on_clear_target(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.cog.manager.clear_user_target(interaction.user.id)
        await self._show_main(interaction)

    async def _on_open_record(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
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
    def _format_pull_line(self, result):
        star = STARS[result["rarity"]]
        character = result["character"] if result["character"] is not None else "?"
        tag = ""
        if result["rarity"] == "three_star" and result["character"] == result["target"]:
            tag = " 🌟" if not result["forced"] else " 🌟✅"
        return f"{star} {character}{tag}"

    def _build_result_embed(self, language, results, user_record, target):
        lines = [self._format_pull_line(r) for r in results]
        if len(lines) == 1:
            description = lines[0]
        else:
            description = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))

        embed = discord.Embed(
            title=t(language, "🎰 Gacha Results", "🎰 抽獎結果"),
            description=description,
            color=discord.Color.gold(),
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
        return embed

    async def do_pull(self, interaction: discord.Interaction, view: "GachaMenuView", count: int):
        results, user_record = self.manager.pull(interaction.user.id, count)
        target = self.manager.effective_target(interaction.user.id)
        embed = self._build_result_embed(view.language, results, user_record, target)
        await interaction.response.edit_message(embed=embed, view=view)

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

    def _build_record_embed(self, language, member: discord.Member):
        user = self.manager.get_user(member.id)
        target = self.manager.effective_target(member.id)

        embed = discord.Embed(
            title=t(language,
                f"{member.display_name}'s Gacha Collection",
                f"{member.display_name} 的抽獎收藏"),
            color=discord.Color.blue(),
        )
        embed.add_field(name=t(language, "Total Pulls", "總抽數"),
                         value=str(user["total_pulls"]), inline=True)
        embed.add_field(name=t(language, "Pity", "保底計數"),
                         value=f"{user['pity_counter']}/{PITY_LIMIT}", inline=True)
        embed.add_field(name=t(language, "Your Target", "你的目標"),
                         value=target or t(language, "None set", "未設定"), inline=True)

        three_star = user.get("three_star", {})
        three_star_value = "\n".join(
            f"★★★ {name} ×{count}" for name, count in sorted(three_star.items())
        ) or t(language, "None yet.", "尚未獲得。")
        embed.add_field(name=t(language, "3-Star Characters", "三星角色"),
                         value=three_star_value[:1024], inline=False)

        two_star = user.get("two_star", {})
        two_star_value = "\n".join(
            f"★★ {name} ×{count}" for name, count in sorted(two_star.items())
        ) or t(language, "None yet.", "尚未獲得。")
        embed.add_field(name=t(language, "2-Star Characters", "二星角色"),
                         value=two_star_value[:1024], inline=False)
        return embed

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

        try:
            self.manager.set_user_target(ctx.author.id, character)
        except GachaError:
            await ctx.send(t(language,
                f"'{character}' is not in the 3-star pool. Start typing to see suggestions.",
                f"'{character}' 不在三星名單中。開始輸入即可看到建議選項。"))
            return
        await ctx.send(t(language,
            f"Your pity target is now **{character}**. This is independent of everyone "
            "else's target, and your existing pity progress is unaffected.",
            f"你的保底目標現在是 **{character}**。這與其他使用者的目標互不影響，且不會影響你"
            "現有的保底計數。"))

    @gacha_target.autocomplete("character")
    async def gacha_target_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._character_autocomplete(interaction, current)

    @gacha.command(
        name="stats", aliases=["results"],
        description="Shows your total pulls and every 2-star/3-star character you've obtained.")
    async def gacha_stats(self, ctx):
        """Shows your total pulls and every 2-star/3-star character you've obtained."""
        language = self._lang(ctx)
        embed = self._build_record_embed(language, ctx.author)
        await ctx.send(embed=embed)

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
        name="reload",
        description="Reloads the gacha pool from data/gacha_pool.json (Admin only).")
    @commands.has_permissions(administrator=True)
    async def gacha_reload(self, ctx):
        """Reloads the gacha pool from data/gacha_pool.json (Admin only)."""
        language = self._lang(ctx)
        self.manager.reload_pool()
        await ctx.send(t(language, "Gacha pool reloaded from disk.", "已從磁碟重新載入轉蛋池。"))


async def setup(bot):
    await bot.add_cog(GachaCog(bot))