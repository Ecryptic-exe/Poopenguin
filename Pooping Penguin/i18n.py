"""
Tiny bilingual-text helper.

The original code repeated this pattern hundreds of times:

    "Some message" if language == 'english' else "某些訊息"

which made every command function noisy and easy to get wrong (mismatched
english/chinese branches, forgetting a language check, etc). t() centralises
the branching in one place:

    t(language, "Some message", "某些訊息")

and get_guild_language() centralises the "look up this guild's language
from settings" pattern that was also copy-pasted everywhere.
"""


def t(language: str, english: str, chinese: str) -> str:
    """Return the english or chinese string based on language."""
    return english if language == "english" else chinese


def get_guild_language(settings: dict, guild_id) -> str:
    """Look up the configured language for a guild, defaulting to english."""
    return settings.get("language", {}).get(str(guild_id), "english")
