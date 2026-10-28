"""English and Traditional Chinese text support for CHUNITHM commands.

CHUNITHM uses the guild preference saved by Poopenguin's ``!lang`` command.
When there is no guild override, it falls back to LB_Penguin's existing
``[fun]`` language configuration.
"""

from __future__ import annotations

import re
from typing import Any

from chuni_penguin.config import config
from chuni_penguin.utils.localization import guild_language, t
from chuni_penguin.utils.ui_messages import MESSAGES

_CHINESE_UI = {english: chinese for chinese, english in MESSAGES.items()}


def get_chunithm_language(context_or_guild: Any = None) -> str:
    """Get the selected language for a command context, guild, or interaction."""
    guild_id = None
    if context_or_guild is not None:
        guild = getattr(context_or_guild, "guild", None)
        guild_id = getattr(guild, "id", None)
        if guild_id is None:
            guild_id = getattr(context_or_guild, "guild_id", None)
        if guild_id is None and isinstance(context_or_guild, int):
            guild_id = context_or_guild

    if guild_id is not None:
        try:
            from chuni_penguin.cogs.poopenguin.config import load_settings

            language = load_settings().get("language", {}).get(str(guild_id))
            if language in ("english", "chinese"):
                return language
        except (OSError, RuntimeError, ValueError, TypeError):
            # A missing Poopenguin preference should not prevent a CHUNITHM
            # command from running. The existing LB_Penguin setting is next.
            pass

    return guild_language(config.fun, guild_id)


def ui_text(context_or_language: Any, text: str, *values: object) -> str:
    """Translate an explicit interface template before inserting unchanged data.

    Call this where UI text is built, never on arbitrary song titles, comments,
    command arguments or complete messages containing user-controlled text.
    """
    language = (
        context_or_language
        if isinstance(context_or_language, str)
        else get_chunithm_language(context_or_language)
    )
    if language == "chinese":
        text = _CHINESE_UI.get(text, _TRADITIONAL_CHINESE.get(text, text))
    else:
        text = MESSAGES.get(text, text)
    return text.format(*values) if values else text


_TRADITIONAL_CHINESE = {
    "Error": "錯誤",
    "Success": "成功",
    "Successfully logged in": "登入成功",
    "Successfully logged out.": "已成功登出。",
    "Failed to login": "登入失敗",
    "You can now use the bot's CHUNITHM-NET commands.": "現在可以使用機器人的 CHUNITHM-NET 指令。",
    "Login instructions have been sent to your DMs.": "登入說明已傳送至你的私人訊息。",
    "Login successful.": "登入成功。",
    "Could not find your token. This is probably a bug.": "找不到你的登入權杖。這可能是程式錯誤。",
    "Invalid username or password.": "SEGA ID 或密碼無效。",
    "Invalid two-factor authentication code.": "雙重驗證碼無效。",
    "Two-factor authentication was enabled, but a code was not provided.": "已啟用雙重驗證，但你沒有提供驗證碼。",
    "Login was successful, but could not retrieve token.": "登入成功，但無法取得登入權杖。",
    "帳號正在同步或變更，請稍後再登出。": "帳號正在同步或變更，請稍後再登出。",
    "請先關閉正在使用帳號的成績分頁，再登出。": "請先關閉正在使用帳號的成績分頁，再登出。",
    "登入憑證驗證失敗，請重新登入。": "登入憑證驗證失敗，請重新登入。",
    "CHUNITHM-NET is currently undergoing maintenance. Please try again later.": "CHUNITHM-NET 目前正在維護，請稍後再試。",
    "Could not find anyone with this friend code. Please double-check and try again.": "找不到此好友代碼的玩家，請檢查代碼後再試一次。",
    "The bot is missing permissions to do something.": "機器人缺少執行此操作所需的權限。",
    "Insufficient permissions.": "權限不足。",
    "You are not allowed to use this command.": "你沒有使用此指令的權限。",
    "One of the arguments provided cannot be parsed.": "其中一個參數無法解析。",
    "Timed out trying to connect to the network.": "連線逾時，請稍後再試。",
    "An error occurred while communicating with the network. Please try again later (or re-login).": "連線時發生錯誤，請稍後再試或重新登入。",
    "An unknown network error occured trying to connect to the network.": "連線時發生未知網路錯誤，請稍後再試。",
    "You're missing a quote somewhere. Perhaps you're using the wrong kind of quote (`\"` vs `”`)?": '有一個引號沒有配對。請確認使用的是正確的引號（`"` 或 `”`）。',
    "No matching songs were found.": "找不到符合的曲目。",
    "No scores found.": "找不到成績。",
    "沒有找到符合的曲目。": "找不到符合的曲目。",
    "請選擇要查看的曲目：": "請選擇要查看的曲目：",
}


def translate_chunithm_text(text: str | None, language: str) -> str | None:
    """Translate known CHUNITHM interface strings without touching user data."""
    if text is None:
        return text
    if language != "chinese":
        return MESSAGES.get(text, text)
    translated = _TRADITIONAL_CHINESE.get(text)
    if translated is not None:
        return translated

    match = re.fullmatch(
        r"CHUNITHM-NET error (\d+)\. Please retry or log in again\.", text
    )
    if match:
        return f"CHUNITHM-NET 錯誤 {match.group(1)}。請重試或重新登入。"

    match = re.fullmatch(
        r"You're too fast\. Take a break for ([0-9.]+) seconds\.", text
    )
    if match:
        return f"操作過於頻繁，請休息 {match.group(1)} 秒後再試。"

    if text.startswith("The token has expired. Please log in again with "):
        prefix_end = text.find(" in my DMs.")
        login_command = text[
            len("The token has expired. Please log in again with ") : prefix_end
        ]
        return (
            f"登入權杖已過期。請在私人訊息中使用 {login_command} 重新登入。\n\n"
            "避免頻繁登出：\n"
            "- 請勿在使用機器人與直接瀏覽 CHUNITHM-NET 之間快速切換\n"
            "- 請使用獨立的無痕視窗登入\n"
            "- 建議使用 SEGA ID，而非社群媒體帳號（尤其是 Twitter）"
        )

    if text.startswith("Bad argument: "):
        detail, separator, help_text = text.partition(
            "\nView help for this command with "
        )
        detail = "參數錯誤：" + ui_text(language, detail[len("Bad argument: ") :])
        if separator:
            return detail + "\n使用 " + help_text + " 查看此指令的說明。"
        return detail

    if text.startswith(
        "An error occurred while communicating with the network. Please try again later"
    ):
        return "網路通訊時發生錯誤，請稍後再試或重新登入。"

    if text.startswith(
        "An unknown network error occured trying to connect to the network."
    ):
        return "連線時發生未知網路錯誤，請稍後再試。"

    if text.startswith("Missing required argument: "):
        argument = text[len("Missing required argument: ") :].splitlines()[0]
        return f"缺少必要參數：{argument}\n請查看指令說明。"

    if text.startswith("An HTTP error occured while communicating with the network: "):
        code = text.rsplit("`", 2)[-2] if text.count("`") >= 2 else ""
        return f"與網路通訊時發生 HTTP 錯誤：`{code}`"

    # Token output is intentionally handled as one UI message so its secret
    # value is never modified or exposed by the translator.
    prefix = "Your token: ||"
    suffix = "|| (click to reveal, DO NOT show to other people.)"
    if text.startswith(prefix) and text.endswith(suffix):
        token = text[len(prefix) : -len(suffix)]
        return f"你的登入權杖：||{token}||（點擊顯示，請勿分享給他人。）"

    return text


def localize_chunithm_embed(embed, language: str):
    """Return a translated copy of an embed, preserving artwork and links."""
    data = embed.to_dict()
    for key in ("title", "description"):
        if isinstance(data.get(key), str):
            data[key] = translate_chunithm_text(data[key], language)
    for field in data.get("fields", ()):
        for key in ("name", "value"):
            if isinstance(field.get(key), str):
                field[key] = translate_chunithm_text(field[key], language)
    for group, key in (("footer", "text"), ("author", "name")):
        value = data.get(group, {}).get(key)
        if isinstance(value, str):
            data[group][key] = translate_chunithm_text(value, language)
    return type(embed).from_dict(data)


__all__ = (
    "get_chunithm_language",
    "localize_chunithm_embed",
    "t",
    "translate_chunithm_text",
    "ui_text",
)
