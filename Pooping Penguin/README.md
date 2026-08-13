# vto bot

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

`data/keyword_sets.json` is already seeded with everything the old hardcoded
`on_message()` chain in `vto.py` used to match, so behaviour is unchanged out
of the box.

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

## What changed from the original vto.py

- Split the single ~1000-line file into cogs by responsibility (see layout
  above) instead of one giant `on_message()` + flat list of `@bot.command()`.
- Centralised the `"X" if language == 'english' else "Y"` pattern into
  `i18n.t()`.
- Replaced the hardcoded keyword/copypasta `if/elif` chain with data-driven
  keyword sets (`data/keyword_sets.json`) manageable at runtime via
  `!keyword`, instead of requiring a code change + redeploy.
- Fixed a small bug in the help menu: the Previous/Next/Close button labels
  used to be decided once at class-definition time from whatever the
  *default* guild's language was, so every server saw the same button
  language regardless of their own `!lang` setting. They're now set
  per-instance from the language actually passed in.
- Everything else (vote flow, autoreact, repeat-echo, permissions model)
  is behaviourally the same as before.

## Slash ("/") commands

Every command is now a `commands.hybrid_command` / `commands.hybrid_group`,
so `!ask ...` and `/ask ...` (etc.) both run the exact same function - there
is only ever one implementation per command. This applies to every cog:
`help`, `vto`/`setvote`, `setperms`/`autoreact`/`lang`, `ask`/`pick`/`rng`/`rcg`,
`keyword ...`, and `copypasta ...`.

Two shape differences are unavoidable given how Discord's slash commands
work (both are noted in the affected cog's docstring too):

- **`copypasta`** is a slash command *group*, and Discord doesn't allow
  invoking a group directly - only its named subcommands. So
  `!copypasta tag @User` (unchanged) becomes `/copypasta use type:tag
  values:@User` as a slash command. `type` has autocomplete: start typing
  and Discord will suggest existing copypasta types (and their aliases).
- **`values`** for `/copypasta use` is a single text field, split on
  spaces server-side, since slash commands can't take an open-ended list
  of arguments the way `!copypasta tag "a name"` could. See `GAPS.md` for
  what's still open here.

See `GAPS.md` for the full list of copypasta-specific gaps and setup steps
(inviting the bot with the right OAuth2 scope, dev-guild sync, etc.)
still needed before slash commands are fully polished in production.
