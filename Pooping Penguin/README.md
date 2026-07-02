## Layout

```
vto/
├── bot.py                 # entry point: builds the bot, loads cogs, runs it
├── key.py                 # NOT included — see key.py.example. api = "your token"
├── config.py               # JSON load/save helpers, file paths
├── i18n.py                 # t(language, en, zh) + get_guild_language()
├── keyword_manager.py       # engine behind the global keyword-triggered responses
├── legacy_copypasta.py      # original hardcoded copypasta strings (reference only,
│                             used by migrate_keywords.py to build the seed data)
├── migrate_keywords.py      # one-off script that produced data/keyword_sets.json
├── requirements.txt
├── cogs/
│   ├── help_cog.py          # !help
│   ├── vote_cog.py          # !vto, !setvote
│   ├── admin_cog.py         # !setperms, !autoreact, !lang
│   ├── general_cog.py       # !ask, !pick, !rng, !rcg
│   ├── keywords_cog.py      # !keyword ... (NEW - manage keyword sets live)
│   └── messages_cog.py      # on_message pipeline: autoreact, mentions,
│                             #   keyword matching, repeat-echo
└── data/
    ├── keyword_sets.json    # the live keyword data (safe to hand-edit or
    │                         #   manage via !keyword commands)
    ├── vote_settings.json   # generated at runtime
    └── votes.json           # generated at runtime
```

## Setup

```
pip install -r requirements.txt
cp key.py.example key.py   # then paste your bot token in
python bot.py
```

## Managing keyword sets

Keyword sets are **global** — shared across every server the bot is in,
matching how the original hardcoded chain behaved. All `!keyword` commands
require Administrator permission in the server they're run from.

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

A message matches a set if any of its keywords appear as a case-insensitive
substring. If several sets match, one is picked at random; then a random
response from that set is sent.
