"""Paginated command manual styled after the original Poopenguin help UI."""

from collections.abc import Iterable

import discord
from discord.ext import commands

from chuni_penguin.context import PenguinContext
from chuni_penguin.cogs.poopenguin.config import load_settings
from chuni_penguin.cogs.poopenguin.i18n import get_guild_language, t

from ._base import PenguinView
from .dismiss import dismiss_message


_COMMAND_DESCRIPTIONS_ZH = {
    "ask": "提出問題，並按隨機成功率給出回答。",
    "pick": "從提供的選項中隨機選擇一個。",
    "rng": "在指定的最小值和最大值之間產生隨機數。",
    "rcg": "產生隨機顏色，並顯示十六進位色碼預覽。",
    "vto": "發起投票，決定是否暫時停權伺服器成員。",
    "setvote": "設定限時停權投票功能（僅管理員）。",
    "setperms": "授予指定角色在指定頻道中的權限（僅管理員）。",
    "autoreact": "設定或停用此頻道訊息的自動表情反應。",
    "autoban": "設定或停用此頻道的自動封鎖（僅管理員）。",
    "lang": "切換機械人說明頁的語言。",
    "keyword": "管理由關鍵字觸發的自動回覆組。",
    "keyword list": "瀏覽及搜尋所有關鍵字回覆組。",
    "keyword info": "查看一組關鍵字及其回覆。",
    "keyword create": "建立空白的關鍵字回覆組。",
    "keyword delete": "刪除整組關鍵字回覆。",
    "keyword enable": "啟用關鍵字回覆組。",
    "keyword disable": "停用關鍵字回覆組，但保留資料。",
    "keyword addkeyword": "在回覆組中新增觸發關鍵字。",
    "keyword removekeyword": "從回覆組移除觸發關鍵字。",
    "keyword addresponse": "在回覆組中新增候選回覆。",
    "keyword removeresponse": "按編號移除候選回覆。",
    "keyword rate": "設定回覆組各自的觸發機率。",
    "copypasta": "從指定類型的範本中隨機發送迷因文字。",
    "copypasta list": "瀏覽及搜尋所有迷因文字類型。",
    "copypasta info": "查看某類型中的所有範本。",
    "copypasta create": "建立空白的迷因文字類型。",
    "copypasta delete": "刪除迷因文字類型。",
    "copypasta enable": "啟用迷因文字類型。",
    "copypasta disable": "停用迷因文字類型，但保留範本。",
    "copypasta add": "在類型中新增迷因文字範本。",
    "copypasta remove": "按編號移除迷因文字範本。",
    "feed": "管理社交媒體帳號追蹤。",
    "feed add": "開始在指定頻道追蹤帳號。",
    "feed remove": "停止追蹤帳號。",
    "feed list": "列出正在追蹤的帳號。",
    "feed check": "立即檢查是否有新貼文。",
    "chat": "向本機 AI 模型提問。",
    "chatreset": "清除此頻道共用的 AI 對話。",
    "gacha": "開啟抽卡介面，進行單抽或十連抽。",
    "gacha pull": "開啟抽卡選單，進行單抽或十連抽。",
    "gacha target": "查看、設定或清除自己的保底目標。",
    "gacha stats": "查看抽卡次數及已獲得的二星、三星角色。",
    "gacha reset": "清除自己的抽卡記錄及收藏。",
    "gacha browse": "瀏覽三星角色名單及插圖。",
    "gacha pool": "查看目前抽卡機率及角色名單。",
    "gacha setfeatured": "設定沒有個人目標的使用者所使用的預設角色。",
    "gacha setimage": "設定角色插圖（僅管理員）。",
    "gacha removeimage": "移除角色插圖（僅管理員）。",
    "gacha setthumbnail": "設定角色縮圖（僅管理員）。",
    "gacha removethumbnail": "移除角色縮圖（僅管理員）。",
    "gacha reload": "從資料檔重新載入抽卡名單（僅管理員）。",
    "chart": "搜尋歌曲並查看譜面資料。",
    "scores": "查看指定歌曲的玩家成績。",
    "score": "查看玩家的 CHUNITHM 成績。",
    "compare": "比較玩家之間的 CHUNITHM 成績。",
    "find": "搜尋 CHUNITHM 歌曲及譜面。",
    "anmitsu": "計算 CHUNITHM 譜面的簡化打法。",
    "rating": "計算或查看 CHUNITHM Rating。",
    "border": "計算 CHUNITHM 譜面分數門檻。",
    "calculate": "計算 CHUNITHM 譜面相關數值。",
    "const": "查詢 CHUNITHM 譜面定數。",
    "recent": "查看最近遊玩的 CHUNITHM 成績。",
    "sync": "同步 CHUNITHM 成績。",
    "statistics": "查看 CHUNITHM 遊玩統計。",
    "courses": "查看 CHUNITHM 課題曲資訊。",
    "jacket": "查看 CHUNITHM 歌曲封面。",
    "best50": "查看 Rating 前 50 名成績。",
    "top": "查看指定等級的最佳成績。",
    "login": "連結 CHUNITHM-NET 帳號。",
    "logout": "登出 CHUNITHM-NET 帳號。",
    "token": "查看目前的登入權杖。",
    "team": "查看 CHUNITHM 隊伍資訊。",
    "ranking": "查看 CHUNITHM 排行榜。",
    "random": "隨機選擇一首 CHUNITHM 歌曲。",
    "recommend": "根據條件推薦 CHUNITHM 歌曲。",
    "avatar": "查看 CHUNITHM 玩家頭像。",
    "chunithm": "查看 CHUNITHM 玩家資料。",
    "config": "設定 CHUNITHM 玩家資料選項。",
    "alias": "管理 CHUNITHM 歌曲別名。",
    "catalog": "查詢 CHUNITHM 歌曲與譜面目錄。",
    "loginbonus": "查看 CHUNITHM 每日登入獎勵。",
    "codex": "查看 CHUNITHM 遊戲指南。",
    "how-to-improve": "查看提升 CHUNITHM 成績的建議。",
    "whatif": "試算不同成績對 Rating 的影響。",
    "reach": "查看達成指定 Rating 的目標成績。",
    "chartview": "查看 CHUNITHM 譜面圖表。",
    "ranking team": "查看 CHUNITHM 隊伍排名。",
    "ranking rating": "查看玩家 Rating 排名。",
    "ranking score": "查看玩家成績排名。",
    "linked-verse": "查看 CHUNITHM LINKED VERSE 活動進度。",
}

_PARAMETER_DESCRIPTIONS_ZH = {
    "question": "要詢問的問題。",
    "choices": "以空格分隔的選項；多字選項請使用引號。",
    "min_val": "最小值。",
    "max_val": "最大值。",
    "type": "數值類型：整數或小數。",
    "member": "要進行操作的成員。",
    "time_str": "停權時間。",
    "arg": "要設定的值。",
    "emoji": "要使用的表情符號。",
    "user": "要套用設定的使用者。",
    "reason": "操作原因。",
    "delete_days": "封鎖時刪除訊息的天數。",
    "channel_id": "頻道 ID。",
    "role_id": "身份組 ID。",
    "account": "要追蹤或管理的帳號。",
    "channel": "要發布內容的頻道。",
    "platform": "帳號所屬平台。",
    "query": "歌曲名稱或搜尋文字。",
    "difficulty": "譜面難度。",
    "detailed": "是否顯示詳細資料。",
    "command": "要查看說明的指令。",
}

_COMMAND_NOTES = {
    "vto": (
        "React with 🖕 to vote yes. Voting stays open for three minutes. The bot needs the Moderate Members permission; configure the vote threshold with `$PREFIXsetvote`.",
        "使用 🖕 表示贊成。投票會持續三分鐘。機械人需要「管理成員」權限；使用 `$PREFIXsetvote` 設定所需票數。",
    ),
    "setvote": (
        "Requires administrator permission. Use a positive number for the vote threshold or `admin` to limit votes to administrators. The setting is saved persistently.",
        "需要管理員權限。輸入正整數設定所需票數，或輸入 `admin` 限制只有管理員可投票。設定會持續儲存。",
    ),
    "lang": (
        "Requires administrator permission. The language choice is saved per server and applies to localized command responses and help pages.",
        "需要管理員權限。語言設定會按伺服器儲存，並套用至已支援翻譯的指令回覆及說明頁。",
    ),
    "ask": (
        "The answer is selected using a random success probability. The question is included in the response.",
        "回答會按隨機成功機率選出，回覆中會包含你提出的問題。",
    ),
    "pick": (
        'Separate choices with spaces. Put quotation marks around choices that contain spaces, for example `$PREFIXpick "New York" Tokyo`.',
        '選項以空格分隔。含空格的選項請加上引號，例如 `$PREFIXpick "New York" Tokyo`。',
    ),
    "rng": (
        "The defaults are 1, 100, and `int`. Choose `float` for decimal output. The minimum must not exceed the maximum.",
        "預設值為 1、100 及 `int`。如需小數請選擇 `float`。最小值不可大於最大值。",
    ),
    "rcg": (
        "Returns a hexadecimal color code and a color preview.",
        "會顯示十六進位色碼及顏色預覽。",
    ),
    "keyword": (
        "Keyword sets are shared across servers. Viewing is available to users; editing commands are restricted to the bot owner. Use the group command to see its current subcommands.",
        "關鍵字組由所有伺服器共用。使用者可以查看；修改指令僅限機械人擁有者使用。使用群組指令可查看目前的子指令。",
    ),
    "copypasta": (
        "Each type has its own template pool. Manage pools with the listed subcommands; templates use `{text}` as the insertion point.",
        "每種類型都有獨立的範本池。使用下列子指令管理範本；範本以 `{text}` 作為文字插入位置。",
    ),
    "feed": (
        "Currently supports Bluesky. Requires Manage Server permission. New posts are checked periodically; posts from before tracking began are not sent.",
        "目前支援 Bluesky，並需要「管理伺服器」權限。機械人會定時檢查新貼文；開始追蹤前的貼文不會發送。",
    ),
    "gacha": (
        "Pull results and personal targets are stored per user. Administrative setup commands may require bot-owner permissions.",
        "抽卡結果及個人目標會按使用者儲存。管理設定指令可能需要機械人擁有者權限。",
    ),
    "chat": (
        "Requires the local AI service to be configured and enabled. Mention the bot with a message to start a conversation; a bare mention opens this manual.",
        "需要先設定並啟用本機 AI 服務。提及機械人並附上文字即可開始對話；單獨提及機械人會開啟此手冊。",
    ),
    "chart": (
        "Song and chart availability depends on the configured CHUNITHM catalog and data sources.",
        "可搜尋的歌曲及譜面取決於已設定的 CHUNITHM 曲庫和資料來源。",
    ),
    "scores": (
        "Score visibility follows each player's privacy settings. The requested song must match the available chart data.",
        "成績是否可見取決於玩家的私隱設定；歌曲亦須符合目前曲庫資料。",
    ),
    "score": (
        "Score visibility follows each player's privacy settings. Available sources depend on linked accounts and imported data.",
        "成績是否可見取決於玩家的私隱設定；可用資料來源取決於已連結的帳號及匯入資料。",
    ),
    "compare": (
        "Both players need matching score data for the selected song or charts. Private player data is not shown without permission.",
        "兩位玩家都需要有可配對的歌曲或譜面成績。未經允許不會顯示私隱成績。",
    ),
    "login": (
        "Authentication instructions and sensitive account steps are delivered privately. Never share passwords, tokens, or session cookies in a public channel.",
        "登入說明及敏感帳號操作會以私人方式處理。請勿在公開頻道分享密碼、權杖或工作階段 Cookie。",
    ),
    "token": (
        "Treat the token as a password. Its command is restricted to private use; do not share the result.",
        "請把權杖視為密碼。此指令只限私人使用，請勿分享權杖內容。",
    ),
}


def get_manual_language(ctx: PenguinContext) -> str:
    """Read the same per-guild language preference used by Poopenguin cogs."""
    guild = getattr(ctx, "guild", None)
    guild_id = getattr(guild, "id", None) or getattr(ctx, "guild_id", None)
    if guild_id is None:
        return "english"
    try:
        return get_guild_language(load_settings(), guild_id)
    except (OSError, RuntimeError, ValueError, TypeError):
        return "english"


def localize_command_description(name: str, description: str | None, language: str) -> str:
    if language != "chinese":
        return description or "Command details."
    return _COMMAND_DESCRIPTIONS_ZH.get(
        name,
        _COMMAND_DESCRIPTIONS_ZH.get(name.split(" ", 1)[0], "此指令提供相關功能。"),
    )


def localize_parameter_description(name: str, description: str | None, language: str) -> str:
    if language != "chinese":
        return description or ""
    return _PARAMETER_DESCRIPTIONS_ZH.get(name, "請填寫此參數。")


def command_notes(name: str, language: str, prefix: str) -> str:
    notes = _COMMAND_NOTES.get(name)
    if notes is None:
        notes = _COMMAND_NOTES.get(name.split(" ", 1)[0])
    if notes is None:
        return t(language, "No additional notes.", "沒有其他注意事項。")
    return t(language, notes[0], notes[1]).replace("$PREFIX", prefix)


def manual_prefix(ctx: PenguinContext) -> str:
    """Return the configured prefix even when help was opened by slash command."""
    prefix = getattr(ctx, "clean_prefix", "")
    if prefix:
        return prefix

    guild_id = getattr(getattr(ctx, "guild", None), "id", None)
    prefixes = getattr(ctx.bot, "prefixes", {})
    if guild_id is not None and (prefix := prefixes.get(guild_id)):
        return prefix

    settings = getattr(ctx.bot, "settings", None)
    bot_settings = getattr(settings, "bot", None)
    return getattr(bot_settings, "default_prefix", "!")


def _add_field(embed: discord.Embed, name: str, value: str, limit: int = 1024):
    """Split long help fields on line boundaries to preserve their content."""
    chunks = []
    current = ""
    for line in value.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = line if not current else current + "\n" + line
    chunks.append(current)
    for index, chunk in enumerate(chunks):
        embed.add_field(
            name=name if index == 0 else "\u200b",
            value=chunk or "\u200b",
            inline=False,
        )


class ManualCloseButton(discord.ui.Button):
    def __init__(self, ctx: PenguinContext, language: str):
        super().__init__(
            label=t(language, "Close", "關閉"),
            style=discord.ButtonStyle.danger,
            row=0,
        )
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await dismiss_message(interaction, self.view, self.ctx.author.id, ctx=self.ctx)


class ManualPageButton(discord.ui.Button):
    def __init__(self, view: "HelpManualView", direction: int, language: str):
        label = t(language, "Previous", "上一頁") if direction < 0 else t(
            language, "Next", "下一頁"
        )
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=0)
        self.manual_view = view
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        old_index = self.manual_view.page_index
        self.manual_view.page_index = max(
            0,
            min(
                len(self.manual_view.pages) - 1,
                self.manual_view.page_index + self.direction,
            ),
        )
        self.manual_view.update_buttons()
        try:
            kwargs = {
                "embed": self.manual_view.pages[self.manual_view.page_index],
                "view": self.manual_view,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if interaction.response.is_done():
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)
            if interaction.message is not None:
                self.manual_view.message = interaction.message
        except Exception:
            self.manual_view.page_index = old_index
            self.manual_view.update_buttons()
            raise


class HelpManualView(PenguinView):
    def __init__(
        self, ctx: PenguinContext, pages: list[discord.Embed], language: str
    ):
        super().__init__(ctx, timeout=600)
        self.pages = pages
        self.page_index = 0
        self.previous_button = ManualPageButton(self, -1, language)
        self.next_button = ManualPageButton(self, 1, language)
        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self.add_item(ManualCloseButton(ctx, language))
        self.update_buttons()

    async def _before_start(self, *, content: str | None = None):
        return {
            "content": content,
            "embed": self.pages[self.page_index],
            "allowed_mentions": discord.AllowedMentions.none(),
        }

    def update_buttons(self):
        self.previous_button.disabled = self.page_index == 0
        self.next_button.disabled = self.page_index == len(self.pages) - 1


def _clip(value: str, limit: int = 1024) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _command_usage(
    ctx: PenguinContext, command: commands.Command, language: str
) -> str:
    prefix = manual_prefix(ctx)
    arguments = command.signature.strip()
    prefix_usage = f"{prefix}{command.qualified_name}"
    if arguments:
        prefix_usage += f" {arguments}"

    usage = [f"`{prefix_usage}`"]
    slash_usage = _slash_usage(command)
    if slash_usage is not None:
        usage.append(f"`{slash_usage}`")
    return t(language, " or ", " 或 ").join(usage)


def _slash_usage(command: commands.Command) -> str | None:
    app_command = getattr(command, "app_command", None)
    if app_command is None:
        return None
    arguments = []
    for parameter in getattr(app_command, "parameters", ()):
        name = parameter.display_name
        arguments.append(f"<{name}>" if parameter.required else f"[{name}]")
    suffix = f" {' '.join(arguments)}" if arguments else ""
    return f"/{command.qualified_name}{suffix}"


def _parameter_lines(command: commands.Command, language: str) -> str:
    lines = []
    for parameter in command.clean_params.values():
        detail = getattr(parameter, "description", None) or ""
        optional = parameter.default is not parameter.empty
        required = t(language, "optional", "選填") if optional else t(
            language, "required", "必填"
        )
        line = f"**{parameter.name}** ({required})"
        if parameter.default is not parameter.empty:
            line += t(
                language,
                f" · default: `{parameter.default}`",
                f" · 預設值：`{parameter.default}`",
            )
        if language == "chinese":
            detail = localize_parameter_description(parameter.name, detail, language)
        if detail:
            line += f" — {detail}"
        lines.append(line)
    return "\n".join(lines)


def _group_lines(
    ctx: PenguinContext,
    children: Iterable[commands.Command],
    language: str,
) -> str:
    prefix = manual_prefix(ctx)
    lines = []
    for child in children:
        usage = f"{prefix}{child.qualified_name}"
        if child.signature:
            usage += f" {child.signature}"
        if (slash_usage := _slash_usage(child)) is not None:
            usage += t(language, f" or {slash_usage}", f" 或 {slash_usage}")
        detail = localize_command_description(
            child.qualified_name, child.help or child.short_doc, language
        )
        lines.append(f"`{usage}`" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)


def build_command_page(
    ctx: PenguinContext,
    command: commands.Command,
    page_number: int,
    page_count: int,
    language: str,
    children: list[commands.Command] | None = None,
) -> discord.Embed:
    prefix = manual_prefix(ctx)
    prefix_usage = f"{prefix}{command.qualified_name}"
    description = localize_command_description(
        command.qualified_name,
        (command.help or command.short_doc or "Command details.").replace(
            "$PREFIX", prefix
        ),
        language,
    )
    embed = discord.Embed(
        title=t(language, f"Command: {prefix_usage}", f"指令：{prefix_usage}"),
        description=_clip(description, 4096),
        color=0x3498DB,
    )
    _add_field(
        embed,
        t(language, "🔹 Usage", "🔹 使用方式"),
        _command_usage(ctx, command, language),
    )

    if isinstance(command, commands.Group) and children:
        _add_field(
            embed,
            t(language, "🔹 Subcommands", "🔹 子指令"),
            _group_lines(ctx, children, language),
        )

    parameters = _parameter_lines(command, language)
    if parameters:
        _add_field(embed, t(language, "🔹 Arguments", "🔹 參數"), parameters)
    else:
        _add_field(
            embed,
            t(language, "🔹 Arguments", "🔹 參數"),
            t(language, "No arguments required.", "無需參數。"),
        )

    aliases = [alias for alias in command.aliases if alias != command.name]
    if aliases:
        _add_field(
            embed,
            t(language, "🔹 Aliases", "🔹 別名"),
            ", ".join(f"`{alias}`" for alias in aliases),
        )

    _add_field(
        embed,
        t(language, "🔹 Notes", "🔹 注意事項"),
        command_notes(command.qualified_name, language, prefix),
    )

    embed.set_footer(
        text=t(
            language,
            f"Page {page_number}/{page_count} | Use {prefix}help for the full user manual.",
            f"第 {page_number}/{page_count} 頁 | 使用 {prefix}help 開啟完整使用手冊。",
        )
    )
    return embed


def _app_command_page(
    ctx: PenguinContext, command, page_number: int, page_count: int, language: str
) -> discord.Embed:
    prefix = manual_prefix(ctx)
    name = getattr(command, "qualified_name", command.name)
    description = localize_command_description(
        name, getattr(command, "description", None), language
    )
    parameters = getattr(command, "parameters", ())
    usage_arguments = [
        f"<{parameter.display_name}>"
        if parameter.required
        else f"[{parameter.display_name}]"
        for parameter in parameters
    ]
    usage = f"/{name}"
    if usage_arguments:
        usage += f" {' '.join(usage_arguments)}"
    embed = discord.Embed(
        title=t(language, f"Command: /{name}", f"指令：/{name}"),
        description=description,
        color=0x3498DB,
    )
    _add_field(embed, t(language, "🔹 Usage", "🔹 使用方式"), f"`{usage}`")
    options = []
    for parameter in parameters:
        detail = f"**{parameter.display_name}**"
        detail += (
            t(language, " (required)", "（必填）")
            if parameter.required
            else t(language, " (optional)", "（選填）")
        )
        parameter_description = localize_parameter_description(
            parameter.name, parameter.description, language
        )
        if parameter_description:
            detail += f" — {parameter_description}"
        options.append(detail)
    if options:
        _add_field(embed, t(language, "🔹 Arguments", "🔹 參數"), "\n".join(options))
    else:
        _add_field(
            embed,
            t(language, "🔹 Arguments", "🔹 參數"),
            t(language, "No arguments required.", "無需參數。"),
        )

    children = getattr(command, "commands", ())
    if children:
        _add_field(
            embed,
            t(language, "🔹 Subcommands", "🔹 子指令"),
            "\n".join(f"`/{child.qualified_name}`" for child in children),
        )
    _add_field(
        embed,
        t(language, "🔹 Notes", "🔹 注意事項"),
        command_notes(name, language, prefix),
    )
    embed.set_footer(
        text=t(
            language,
            f"Page {page_number}/{page_count} | Use {prefix}help for the full user manual.",
            f"第 {page_number}/{page_count} 頁 | 使用 {prefix}help 開啟完整使用手冊。",
        )
    )
    return embed


def _home_page(
    ctx: PenguinContext,
    commands_list: list[commands.Command],
    page_count: int,
    language: str,
) -> discord.Embed:
    prefix = manual_prefix(ctx)
    names = {command.name for command in commands_list}
    bot = ctx.bot
    features = []

    if bot.get_cog("keywords") is not None or "keyword" in names:
        features.append(
            t(
                language,
                f"**Keyword Responses:** Configure phrase-triggered replies with `{prefix}keyword`.",
                f"**關鍵字回覆：** 使用 `{prefix}keyword` 設定關鍵字自動回覆。",
            )
        )
    if bot.get_cog("messages") is not None:
        features.append(
            t(
                language,
                "**Repeat Detection:** Echoes a message repeated by three different users.",
                "**重複訊息偵測：** 當三位不同使用者重複傳送相同訊息時，機械人會回應該訊息。",
            )
        )
    if "vto" in names:
        features.append(
            t(
                language,
                f"**Timeout Voting:** Use `{prefix}vto` to vote on timing out a member.",
                f"**限時停權投票：** 使用 `{prefix}vto` 投票暫時停權成員。",
            )
        )

    random_commands = [
        ("ask", "Random Response", "隨機回答"),
        ("pick", "Random Choice", "隨機選擇"),
        ("rng", "Random Number", "隨機數字"),
        ("rcg", "Random Color", "隨機顏色"),
    ]
    for name, english_label, chinese_label in random_commands:
        if name in names:
            features.append(
                f"**{t(language, english_label, chinese_label)}:** `{prefix}{name}`"
            )

    if "autoreact" in names or "setperms" in names:
        details = []
        if "setperms" in names:
            details.append(f"`{prefix}setperms`")
        if "autoreact" in names:
            details.append(f"`{prefix}autoreact`")
        features.append(
            t(language, "**Server Settings:** ", "**伺服器設定：** ")
            + ", ".join(details)
        )

    chuni_commands = [
        name for name in ("chart", "scores", "score", "compare") if name in names
    ]
    if chuni_commands:
        features.append(
            t(language, "**CHUNITHM:** ", "**CHUNITHM：** ")
            + ", ".join(f"`{prefix}{name}`" for name in chuni_commands)
            + t(language, " and related account and chart tools.", "及其他帳號與譜面工具。")
        )
    if "feed" in names:
        features.append(
            t(
                language,
                f"**Feed Tracker:** `{prefix}feed` tracks supported social accounts.",
                f"**動態追蹤：** 使用 `{prefix}feed` 追蹤支援的社交帳號。",
            )
        )
    if bot.get_cog("AICog") is not None or "chat" in names:
        chat = t(
            language,
            "**AI Chat:** Mention me with a message to chat; a bare mention opens this manual.",
            "**AI 對話：** 提及機械人並附上訊息即可聊天；單獨提及機械人會開啟此手冊。",
        )
        if "chatreset" in names:
            chat += t(
                language,
                f" Use `{prefix}chatreset` to clear the conversation.",
                f" 使用 `{prefix}chatreset` 清除對話。",
            )
        features.append(chat)

    if not features:
        features.append(
            t(
                language,
                "Use the buttons below to browse available commands.",
                "使用下方按鈕瀏覽可用指令。",
            )
        )

    description = [
        t(
            language,
            "Welcome to the bot! Use the buttons below to browse command details.",
            "歡迎使用機械人！使用下方按鈕瀏覽指令說明。",
        ),
        "",
        t(language, "🔹 **Command Prefix**", "🔹 **指令前綴**"),
        t(
            language,
            f"Use `{prefix}` as the command prefix (for example, `{prefix}help`).",
            f"使用 `{prefix}` 作為指令前綴（例如 `{prefix}help`）。",
        ),
        "",
        t(language, "🔹 **Features**", "🔹 **功能**"),
        *features,
        "",
        t(language, "🔹 **Navigation**", "🔹 **瀏覽方式**"),
        t(
            language,
            "Use Previous and Next to browse individual command details. A bare mention opens this manual; mention me with text to chat.",
            "使用「上一頁」和「下一頁」瀏覽個別指令說明。單獨提及機械人會開啟此手冊；提及時附上文字則會開始聊天。",
        ),
    ]
    embed = discord.Embed(
        title=t(language, "Bot User Manual", "機械人使用手冊"),
        description="\n".join(description),
        color=0x3498DB,
    )
    embed.set_footer(
        text=t(
            language,
            f"Page 1/{page_count} | Use {prefix}help for the full user manual.",
            f"第 1/{page_count} 頁 | 使用 {prefix}help 開啟完整使用手冊。",
        )
    )
    return embed


async def build_manual_pages(
    ctx: PenguinContext,
    commands_list: list[commands.Command] | None = None,
) -> list[discord.Embed]:
    bot = ctx.bot
    language = get_manual_language(ctx)
    help_command = bot.help_command.copy() if bot.help_command is not None else None
    if help_command is not None:
        help_command.context = ctx
    if commands_list is None:
        commands_list = []
        if help_command is not None:
            for command_list in help_command.get_bot_mapping().values():
                commands_list.extend(
                    await help_command.filter_commands(command_list, sort=True)
                )

    visible = {
        command.qualified_name: command
        for command in commands_list
        if not command.hidden and command.name != "help"
    }
    roots = sorted(
        (command for command in visible.values() if command.parent is None),
        key=lambda command: command.qualified_name,
    )

    prefix_names = {command.name for command in roots}
    slash_only = [
        command
        for command in bot.tree.get_commands()
        if command.name not in prefix_names
        and command.name != "help"
        and bot.get_command(command.name) is None
    ]
    page_count = 1 + len(roots) + len(slash_only)
    pages = [_home_page(ctx, roots, page_count, language)]

    for command in roots:
        children = None
        if isinstance(command, commands.Group):
            children = command.commands
            if help_command is not None:
                children = await help_command.filter_commands(children, sort=True)
        pages.append(
            build_command_page(
                ctx,
                command,
                len(pages) + 1,
                page_count,
                language,
                children=children,
            )
        )

    for command in sorted(slash_only, key=lambda item: item.name):
        pages.append(
            _app_command_page(ctx, command, len(pages) + 1, page_count, language)
        )
    return pages


async def show_help_manual(
    ctx: PenguinContext,
    commands_list: list[commands.Command] | None = None,
):
    language = get_manual_language(ctx)
    pages = await build_manual_pages(ctx, commands_list)
    view = HelpManualView(ctx, pages, language)
    await view.start()
    return view
