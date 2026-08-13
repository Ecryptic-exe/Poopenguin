# Poop Penguin Bot

A cog-based Discord bot (discord.py 2.x) with hybrid slash/prefix commands,
built for a small community. Includes vote-based moderation, keyword-triggered
responses, copypasta templates, and a gacha mini-game.

## Layout

```
.
├── bot.py                  # entry point: builds the bot, loads cogs, runs it
├── key.py                  # NOT included - copy key.py.example, add your token
├── config.py                # JSON load/save helpers, file paths (all data
│                             #   lives under data/, resolved relative to the
│                             #   project so it works on any machine)
├── i18n.py                  # t() + get_guild_language() translation helper
├── keyword_manager.py       # engine behind global keyword-triggered responses
├── copypasta_manager.py     # engine behind copypasta template generation
├── gacha_manager.py         # gacha pull logic, rates, user records
├── cleanup_settings.py      # one-off maintenance script for settings data
├── requirements.txt
├── cogs/
│   ├── help_cog.py           # !help
│   ├── vote_cog.py           # !vto, !setvote
│   ├── admin_cog.py          # !setperms, !autoreact, !lang
│   ├── general_cog.py        # !ask, !pick, !rng, !rcg
│   ├── keywords_cog.py       # !keyword ... manage keyword sets live
│   ├── messages_cog.py       # on_message pipeline: autoreact, mentions,
│   │                          #   keyword matching, repeat-echo
│   ├── copypasta_cog.py      # copypasta template commands
│   └── gacha_cog.py          # gacha pull / roster / rate commands
└── data/
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
cp key.py.example key.py   # then paste your bot token in
python bot.py
```

`key.py` is gitignored - never commit your real bot token. If you invited an
older version of this bot with only the `bot` OAuth2 scope, re-invite it with
`applications.commands` too, or slash commands won't register.

Get a token at https://discord.com/developers/applications -> your
application -> Bot -> Reset Token. The bot needs the `bot` **and**
`applications.commands` OAuth2 scopes when you generate its invite link,
or slash commands won't register (see GAPS.md).

For instant slash-command updates while developing, set a `DEV_GUILD_ID`
environment variable to a server ID you control - `bot.py` will sync there
instantly instead of waiting on a global sync (which can take up to an hour
to propagate).

## Data files

Everything under `data/` is plain JSON and safe to hand-edit while the bot is
offline. `data/keyword_sets.json` and `data/copypasta_sets.json` ship with
minimal example content here - swap in your own community's sets, or manage
them live with the `!keyword` and copypasta commands once the bot is running.
`gacha_pool.json` defines the default gacha roster and pull rates and can be
edited directly at any time.

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
