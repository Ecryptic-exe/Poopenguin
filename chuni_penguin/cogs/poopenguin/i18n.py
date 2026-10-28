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
    """Use the same guild override and configured fallback as CHUNITHM."""
    language = settings.get("language", {}).get(str(guild_id))
    if language in ("english", "chinese"):
        return language
    from chuni_penguin.config import config
    from chuni_penguin.utils.localization import guild_language

    return guild_language(config.fun, guild_id)
