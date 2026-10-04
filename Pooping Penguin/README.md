# Discord Bot Template

A cog-based Discord bot (discord.py 2.x) with hybrid slash/prefix commands,
ready to fork for a small community. Out of the box it includes vote-based
timeout moderation, keyword-triggered responses, fill-in-the-blank
"copypasta" templates, a gacha mini-game with pity, per-server
English/Traditional Chinese text, and an optional local-AI chat cog.

## Quick start

```bash
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp key.py.example key.py        # then paste your bot token into key.py
python bot.py
```

Requires Python 3.9+.

**Discord Developer Portal checklist** (https://discord.com/developers/applications):

1. Bot -> Reset Token, and put it in `key.py` (or the `DISCORD_TOKEN` env var).
2. Bot -> Privileged Gateway Intents: enable **Message Content** and **Server Members**.
3. OAuth2 -> URL Generator: tick **both** the `bot` and `applications.commands` scopes, then give the
   bot the permissions you want (Moderate Members for `vto`, Ban Members for `autoban`,
   Manage Channels for `setperms`, Add Reactions for `autoreact`).

Slash commands are registered on startup. Global registration can take up to an hour the first
time; for instant updates while developing, set `DEV_GUILD_ID` to a server you control.

## Configuration

All settings live in `settings.py`. Each can be set as an environment variable or in `key.py`.

| Setting | Purpose | Default |
|---|---|---|
| `DISCORD_TOKEN` | Bot token (**required**) | - |
| `BOT_NAME` | Name used in help text, AI persona, `@Bot help` | `Template Bot` |
| `SUPPORT_CONTACT` | Shown in the `help` footer and status rotation (blank hides it) | blank |
| `COMMAND_PREFIX` | Prefix for text commands | `!` |
| `DEV_GUILD_ID` | Instant slash sync to one server | unset |
| `LM_STUDIO_BASE_URL` / `MODEL_NAME` / `VISION_MODEL_NAME` | Local AI server (any OpenAI-compatible API) | LM Studio defaults |
| `LOG_ALL_MESSAGES` | Print every message the bot sees (privacy-sensitive) | off |

Also worth editing: `STATUS_MESSAGES` and `INITIAL_EXTENSIONS` in `bot.py`
(comment out a cog to disable that feature, e.g. `cogs.ai_cog` if you don't run a local model).

## Layout

```
.
├── bot.py                  # entry point: builds the bot, loads cogs, syncs slash commands
├── settings.py             # token + branding + optional settings (env vars / key.py)
├── key.py.example          # copy to key.py (gitignored)
├── config.py               # JSON load/save helpers and data/ file paths
├── i18n.py                 # t(language, english, chinese) helper
├── persona.py              # the AI chat personality: edit this to change who the bot is
├── keyword_manager.py      # engine: keyword -> response sets
├── copypasta_manager.py    # engine: template pools with {placeholders}
├── gacha_manager.py        # engine: pulls, rates, pity, per-user records
├── cogs/
│   ├── help_cog.py         # help (paged manual; add new commands to COMMAND_LIST)
│   ├── vote_cog.py         # vto, setvote
│   ├── admin_cog.py        # setperms, autoreact, autoban, lang, sync (owner only)
│   ├── general_cog.py      # ask, pick, rng, rcg
│   ├── keywords_cog.py     # keyword ... manage keyword sets live (admin)
│   ├── messages_cog.py     # on_message pipeline: autoban, autoreact, @mention, keywords, repeat-echo
│   ├── copypasta_cog.py    # copypasta ... (alias: cp)
│   ├── gacha_cog.py        # gacha ... interactive menu, pity target, artwork
│   └── ai_cog.py           # chat, chatreset, and free-text @mentions -> local model
└── data/
    ├── keyword_sets.json   # seeded with one example set
    ├── copypasta_sets.json # seeded with three example types (tag, activity, song)
    ├── gacha_pool.json     # example roster + rates; hand-editable
    ├── gacha_images.json   # character -> artwork URLs (empty)
    └── (generated at runtime: vote_settings.json, votes.json, gacha_users.json)
```

Everything under `data/` is plain JSON and safe to hand-edit while the bot is offline.
Keyword sets, copypasta pools and the gacha banner are **global** (shared by every server the
bot is in). Settings such as language, autoreact and autoban are per-server/channel.

## Commands

| Command | What it does | Who |
|---|---|---|
| `help [command]` | Paged manual / per-command details (`@Bot` also opens it) | anyone |
| `vto <@member> [time]` | Start a vote to time someone out (`1d`, `2h`, `30m`, `10s`, `random`; capped at Discord's 28-day limit) | anyone |
| `setvote <n \| admin>` | Votes required, or admin-only voting | admin |
| `setperms <channel_id> <role_id>` | Grant a role view/send access to a channel | admin |
| `autoreact [emoji] [@user]` | React to every message in a channel (or one user's) | anyone |
| `autoban [reason] [delete_days]` | Ban anyone who posts in this channel (honeypot); admins exempt | admin |
| `lang` | Toggle the server between English and Traditional Chinese | anyone |
| `ask`, `pick`, `rng`, `rcg` | Random fun commands | anyone |
| `keyword ...` | Manage keyword sets (`list`, `info`, `create`, `delete`, `enable`, `disable`, `addkeyword`, `removekeyword`, `addresponse`, `removeresponse`, `rate`) | admin |
| `copypasta <type> <values>` | Fill a random template from a type's pool; manage with `list/info/create/delete/enable/disable/add/remove` | anyone / admin |
| `gacha` | Interactive pull menu; `target`, `browse`, `stats`, `reset`, `pool`; admin: `setfeatured`, `setimage`, `setthumbnail`, `removeimage`, `removethumbnail`, `reload` | anyone / admin |
| `chat <prompt>`, `chatreset` | Talk to the local AI model; reset the channel's shared conversation | anyone |
| `sync [guild\|global\|clearguild\|clearglobal]` | Re-register slash commands | bot owner |

All of these work as `!command` and `/command` except where Discord can't (a bare group such as
`!gacha` or `!copypasta tag @User` is prefix-only; slash users use `/gacha pull`).

## Adding your own cog

1. Create `cogs/my_cog.py`:

   ```python
   from discord.ext import commands
   from config import load_settings
   from i18n import t, get_guild_language

   class MyCog(commands.Cog, name="my"):
       def __init__(self, bot):
           self.bot = bot

       @commands.hybrid_command(name="hello", description="Say hello.")
       async def hello(self, ctx):
           language = get_guild_language(load_settings(), ctx.guild.id)
           await ctx.send(t(language, "Hello!", "你好！"))

   async def setup(bot):
       await bot.add_cog(MyCog(bot))
   ```

2. Add `"cogs.my_cog"` to `INITIAL_EXTENSIONS` in `bot.py`.
3. Add an entry to `COMMAND_LIST` in `cogs/help_cog.py` so it appears in `help`.

## Notes

- Never commit `key.py`. If a token ever leaks, reset it in the Developer Portal.
- `autoban` is destructive by design: use it only in channels nobody legitimate should post in.
- The AI cog needs a running OpenAI-compatible server; without one it replies with the friendly error from `persona.py`.
