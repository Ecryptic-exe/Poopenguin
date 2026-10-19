"""Shared bilingual text and explicit guild preferences for B feature domains."""

from chuni_penguin.config import FunConfig


def t(language: str, english: str, chinese: str) -> str:
    return english if language == "english" else chinese


def guild_language(settings: FunConfig, guild_id: int | None) -> str:
    if guild_id in settings.english_guilds:
        return "english"
    if guild_id in settings.chinese_guilds:
        return "chinese"
    return settings.language
