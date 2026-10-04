# Poop Penguin Bot

A cog-based Discord bot (discord.py 2.x) with hybrid slash/prefix commands,
built for a small community. Includes vote-based moderation, keyword-triggered
responses, copypasta templates, a gacha mini-game, CHUNITHM rating charts, and
an optional local-AI chat (LM Studio).

Target interpreter: **Python 3.9**.

## Layout

```
.
├── bot.py                  # entry point: builds the bot, loads cogs, runs it
├── key.py                  # YOU create this (gitignored): bot token + AI settings
├── .env                    # optional: CHUNITHM tuning (see below)
├── config.py                # JSON load/save helpers, file paths (all data
│                             #   lives under data/, resolved relative to the
│                             #   project so it works on any machine)
├── i18n.py                  # t() + get_guild_language() translation helper
├── keyword_manager.py       # engine behind global keyword-triggered responses
├── copypasta_manager.py     # engine behind copypasta template generation
├── gacha_manager.py         # gacha pull logic, rates, user records
├── cleanup_settings.py      # one-off maintenance script for settings data
├── requirements.txt
├── ratingbot/               # CHUNITHM song search + chart service (see below)
├── engine/                  # numeric engine behind the rating charts
├── assets/                  # bundled Wiki formula snapshot + Noto Sans CJK font
├── cogs/
│   ├── help_cog.py           # !help
│   ├── vote_cog.py           # !vto, !setvote
│   ├── admin_cog.py          # !setperms, !autoreact, !autoban, !lang, !sync
│   ├── general_cog.py        # !ask, !pick, !rng, !rcg
│   ├── keywords_cog.py       # !keyword ... manage keyword sets live
│   ├── messages_cog.py       # on_message pipeline: autoreact, mentions,
│   │                          #   keyword matching, repeat-echo
│   ├── copypasta_cog.py      # copypasta template commands
│   ├── gacha_cog.py          # gacha pull / roster / rate commands
│   ├── rating_cog.py         # !rating, !song_search (CHUNITHM charts)
│   └── ai_cog.py             # !chat, !chatreset, @mention -> local AI model
├── cache/                   # generated: cached CHUNITHM charts (safe to delete)
└── data/
    ├── catalog.json          # CHUNITHM song list (titles + difficulties only)
    ├── aliases.json          # optional hand-written song aliases
    ├── keyword_sets.json     # seeded with a small example set
    ├── copypasta_sets.json   # seeded with a small example template
    ├── gacha_pool.json       # default gacha roster + rates (hand-editable)
    ├── gacha_users.json      # generated at runtime
    ├── vote_settings.json    # generated at runtime
    └── votes.json            # generated at runtime
```

## Setup

```
pip install -r requirements.txt
python bot.py
```

Create `key.py` next to `bot.py` (it is gitignored - never commit it or put
it in a zip you share):

```python
api = "your-bot-token-here"

# Only needed for cogs/ai_cog.py (local model through LM Studio's
# OpenAI-compatible API):
LM_STUDIO_BASE_URL = "http://localhost:1234/v1"   # adjust to your server
MODEL_NAME = "your-text-model-id"
VISION_MODEL_NAME = "qwen3-vl-4b-instruct"        # optional, this is the default
```

`ai_cog.py` imports `LM_STUDIO_BASE_URL` and `MODEL_NAME` from `key.py` when it
loads, so keep those two lines even if you don't run a local model, or remove
`"cogs.ai_cog"` from `INITIAL_EXTENSIONS` in `bot.py`.

Get a token at https://discord.com/developers/applications -> your
application -> Bot -> Reset Token. The bot needs the `bot` **and**
`applications.commands` OAuth2 scopes when you generate its invite link,
or slash commands won't register. It also needs the **Message Content** and
**Server Members** privileged intents enabled in the developer portal
(`bot.py` requests both).

For instant slash-command updates while developing, set a `DEV_GUILD_ID`
environment variable to a server ID you control - `bot.py` will sync there
instantly instead of waiting on a global sync (which can take up to an hour
to propagate). `!sync` (bot owner only) re-registers slash commands by hand.

## Commands at a glance

Everything is a hybrid command (`!name` and `/name`) unless noted. `!help`
opens the paged manual; `!help <command>` shows one command.

| Area | Commands |
|---|---|
| Moderation | `!vto`, `!setvote`, `!setperms`, `!autoreact`, `!autoban` (admin) |
| Fun | `!ask`, `!pick`, `!rng`, `!rcg`, `!copypasta` (`!cp`), `!gacha` |
| Content sets | `!keyword ...`, `!copypasta ...` (admin) |
| CHUNITHM | `!rating`, `!song_search` |
| Local AI | `!chat`, `!chatreset`, or just @mention the bot |
| Settings | `!lang` (English / Chinese per server), `!sync` (owner, prefix only) |

**Gacha** (`!gacha`): opens a Single / 10x pull menu. Subcommands: `pull`,
`target`, `stats` (alias `results`), `reset`, `browse`, `pool`, plus admin-only
`setfeatured`, `setimage`, `removeimage`, `setthumbnail`, `removethumbnail`,
`reload`. The roster and rates live in `data/gacha_pool.json`.

**Local AI** (`!chat` / @mention): one shared conversation per channel, kept
for 15 minutes of silence. `!chatreset` clears the channel's conversation.
Images attached to a message are described by the vision model first, then the
text model writes the reply. Bot mentions that aren't a command go here
instead of showing an error.

**Autoban** (`!autoban <reason> [delete_days]`, admin): bans anyone who sends a
message in that channel; run `!autoban` with no reason to turn it off. Use with
care - it is meant for honeypot channels.

## CHUNITHM rating charts

`!rating` / `/rating` look up a CHUNITHM song and draw how players of different
ratings score on that chart, with a short numeric read-out ("personal spread",
"relative specialization", "score-eating" ranges). `!song_search` /
`/song_search` only search the song list.

```
/rating song:<title or fragment> difficulty:MASTER detailed:False
!rating "XL TECHNO" MASTER            # prefix: quote titles that contain spaces
!song_search サファリ                  # up to 10 candidates + their difficulties
```

- If the text matches several songs, a dropdown appears (only the requester can
  use it). `/rating` also autocompletes titles.
- Nothing is fetched or drawn at startup, while searching, or in the
  background - only the one chart someone selected. Statistics come from the
  public Chunirec site, so some charts have no usable data; the bot says why.
- Results are cached under `cache/` for 24 hours (safe to delete any time).
  A chart is drawn by one worker subprocess at a time, up to 8 waiting.
- `detailed:True` shows the full score range instead of the default SS-to-MAX
  focus. Cooldown is 5 seconds per user.
- Written to run on Python 3.9. Needs the extra packages listed in
  `requirements.txt` (python-dotenv, requests, beautifulsoup4, numpy,
  matplotlib, Pillow, tzdata).
- The cog is optional: if `ratingbot/`, `engine/`, `assets/` or
  `data/catalog.json` is missing, or one of the extra packages isn't installed,
  the rest of the bot still starts and the reason is logged
  (`rating_cog NOT loaded ...`).

Add song aliases in `data/aliases.json` as `{"<song id>": ["alias", ...]}`
(find ids with `!song_search`) and restart. Optional tuning goes in a `.env`
file next to `bot.py` (`MIN_PLAYERS`, `CACHE_TTL_SECONDS`, `MAX_CHART_WORKERS`,
...); the defaults work without one. The rating feature doesn't need or use a bot
token - `key.py` stays the only place it lives. The chart text/legend and the
numeric read-out are Chinese/Japanese regardless of `!lang`; the bot's own
prompts and errors follow `!lang`.

The bundled Noto Sans CJK font is SIL OFL licensed (license in `assets/`).

If `/rating` replies that the song has no usable data, that is the Chunirec
side (too few players), not a bot fault. `cache/` can be deleted any time to
force fresh charts.

## Data files

Everything under `data/` is plain JSON and safe to hand-edit while the bot is
offline. `data/keyword_sets.json` and `data/copypasta_sets.json` ship with
minimal example content here - swap in your own community's sets, or manage
them live with the `!keyword` and copypasta commands once the bot is running.
`gacha_pool.json` defines the default gacha roster and pull rates and can be
edited directly at any time (`!gacha reload` picks changes up without a
restart). `catalog.json` is the CHUNITHM song list used by `!rating` and
`!song_search`; `aliases.json` is optional hand-written aliases for it.

## Managing keyword sets

Keyword sets are **global** - shared across every server the bot is in. All
`!keyword` commands require Administrator permission in the server they're
run from.

```
!keyword                                  # list subcommands
!keyword list                             # list all sets + enabled status
!keyword show <id>                        # see keywords + responses for one set
!keyword create <id>                      # make a new empty set
!keyword delete <id>                      # delete a set entirely
!keyword enable <id> / disable <id>       # toggle without deleting
!keyword addkeyword <id> <word>           # add a trigger word
!keyword removekeyword <id> <word>        # remove a trigger word
!keyword addresponse <id> <text>          # add a candidate response
!keyword removeresponse <id> <index>      # remove by index (see `show`)
```
