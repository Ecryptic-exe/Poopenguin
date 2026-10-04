"""
Deployment settings and secrets, in one place.

Every value is looked up in this order:
  1. an environment variable of the same name
  2. a variable of the same name in key.py (copy key.py.example -> key.py)
  3. the default below

key.py is gitignored, so your token never lands in version control.
"""
import os

try:
    import key as _key  # optional, created from key.py.example
except ImportError:
    _key = None


def _get(name, default=None):
    value = os.environ.get(name)
    if value:
        return value
    return getattr(_key, name, default) if _key is not None else default


# --- Required -------------------------------------------------------------
# Discord bot token. `api` is accepted as a legacy name for older key.py files.
DISCORD_TOKEN = _get("DISCORD_TOKEN") or _get("api")

# --- Branding (shown in help text, the status rotation and AI persona) ----
BOT_NAME = _get("BOT_NAME", "Template Bot")
SUPPORT_CONTACT = _get("SUPPORT_CONTACT", "")  # e.g. "YourName#0000"; blank hides the line
COMMAND_PREFIX = _get("COMMAND_PREFIX", "!")

# --- Optional: instant slash-command sync while developing ----------------
# Set to a server ID you control. Leave unset to sync globally (can take up
# to ~1h to show up the first time).
DEV_GUILD_ID = _get("DEV_GUILD_ID")

# --- Optional: local AI (cogs/ai_cog.py) ----------------------------------
# Any OpenAI-compatible server works (LM Studio, llama.cpp, vLLM, Ollama's
# /v1 endpoint, ...).
LM_STUDIO_BASE_URL = _get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
MODEL_NAME = _get("MODEL_NAME", "qwen/qwen3-8b")
VISION_MODEL_NAME = _get("VISION_MODEL_NAME", "qwen3-vl-4b-instruct")

# --- Optional: debugging ---------------------------------------------------
# Logs the content of EVERY message the bot can see to the console.
# Off by default - it's a privacy footgun on a real server.
LOG_ALL_MESSAGES = str(_get("LOG_ALL_MESSAGES", "")).lower() in ("1", "true", "yes")
