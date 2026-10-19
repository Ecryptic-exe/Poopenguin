"""Explicit literal settings files; no interpolation, execution or discovery."""

import copy
import re
from collections.abc import Mapping
from pathlib import Path

from chuni_penguin.config import CommaDelimitedSet, Config

ENV_KEYS = frozenset(
    {
        "TOKEN",
        "BOT_PREFIX",
        "BOT_COGS_EXTRA",
        "BOT_COGS_DISABLED",
        "KAMAITACHI_CLIENT_ID",
        "KAMAITACHI_CLIENT_SECRET",
        "KAMAITACHI_API_KEY",
        "LM_STUDIO_BASE_URL",
        "LM_STUDIO_MODEL",
        "LM_STUDIO_VISION_MODEL",
        "TEST_GUILD_ID",
        "CHUNIREC_PLAYER_TOKEN",
        "CHUNIREC_REGION",
    }
)
LOCAL_AI_KEYS = frozenset(
    {
        "LM_STUDIO_BASE_URL",
        "LM_STUDIO_MODEL",
        "LM_STUDIO_VISION_MODEL",
    }
)


class TokenConfigError(Exception):
    """Safe reason code only, never a file path, token or parser input."""


def read_env_file(path: Path) -> dict[str, str]:
    """Read supported literal values only. Blank optional values keep INI defaults."""
    try:
        with path.open("rb") as handle:
            data = handle.read(65537)
        if len(data) > 65536:
            reason = "env_size_limit"
            raise TokenConfigError(reason)
        lines = data.decode("utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        reason = "env_unreadable"
        raise TokenConfigError(reason) from None
    values = {}
    for line in lines:
        match = re.fullmatch(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*", line)
        if not match or match[1] not in ENV_KEYS:
            continue
        key, literal = match.groups()
        if key in values:
            raise TokenConfigError("env_duplicate_supported_key")
        parsed = re.fullmatch(
            r"""(?:"([^"\r\n]*)"|'([^'\r\n]*)'|([^'"#\r\n]*?))\s*(?:\#.*)?""", literal
        )
        if parsed is None:
            raise TokenConfigError("env_invalid_literal")
        value = next(v for v in parsed.groups() if v is not None)
        if len(value) > 4096 or any(ord(char) < 32 for char in value):
            raise TokenConfigError("env_invalid_value")
        values[key] = value
    return values


def token_from_env(values: Mapping[str, str]) -> str:
    if "TOKEN" not in values:
        reason = "env_token_missing_or_duplicate"
        raise TokenConfigError(reason)
    token = values["TOKEN"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        reason = "env_token_must_be_nonempty_literal"
        raise TokenConfigError(reason)
    return token


def read_token_file(path: Path) -> str:
    return token_from_env(read_env_file(path))


def settings_from_env(base: Config, values: Mapping[str, str]) -> Config:
    """Override a copy; never replace a database path or enable services implicitly."""
    settings = copy.deepcopy(base)
    settings.bot.token = token_from_env(values)
    if prefix := values.get("BOT_PREFIX"):
        if len(prefix) > 32:
            raise TokenConfigError("env_prefix_too_long")
        settings.bot.default_prefix = prefix
    for key, attr in (("BOT_COGS_EXTRA", "extra"), ("BOT_COGS_DISABLED", "disabled")):
        if value := values.get(key):
            setattr(
                settings.cogs,
                attr,
                CommaDelimitedSet(v.strip() for v in value.split(",") if v.strip()),
            )
    for key, attr in (
        ("KAMAITACHI_CLIENT_ID", "kamaitachi_client_id"),
        ("KAMAITACHI_CLIENT_SECRET", "kamaitachi_client_secret"),
        ("KAMAITACHI_API_KEY", "kamaitachi_api_key"),
    ):
        if value := values.get(key):
            setattr(settings.credentials, attr, value)
    return settings
