# vto bot

A general-purpose Discord bot built on `discord.py`, structured as a cog per
command group. Started as a vote-to-timeout (`!vto`) bot, since grew a
data-driven keyword-response system and a fill-in-the-blank "copypasta"
generator.

Every command is a `commands.hybrid_command`/`hybrid_group`, so `!thing ...`
and `/thing ...` both run the exact same function - there's only ever one
implementation per command. See [GAPS.md](GAPS.md) for the handful of
places slash commands can't do everything the text version can.

## Layout

```
.
├── bot.py                 # entry point: builds the bot, loads cogs, runs it
├── key.py                 # NOT committed - copy key.py.example, add token
├── config.py               # JSON load/save helpers, file paths
├── i18n.py                 # t(language, en, zh) + get_guild_language()
├── keyword_manager.py       # engine behind the global keyword-triggered responses
├── copypasta_manager.py     # engine behind the fill-in-the-blank copypasta system
├── cleanup_settings.py      # one-off migration script (legacy autoreact format)
├── requirements.txt
├── GAPS.md                  # known slash-command limitations, see above
├── cogs/
│   ├── help_cog.py          # !help
│   ├── vote_cog.py          # !vto, !setvote
│   ├── admin_cog.py         # !setperms, !autoreact, !lang, !sync
│   ├── general_cog.py       # !ask, !pick, !rng, !rcg
│   ├── keywords_cog.py      # !keyword ... - manage keyword sets live
│   ├── copypasta_cog.py     # !copypasta ... - manage & generate copypasta
│   └── messages_cog.py      # on_message pipeline: autoreact, mentions,
│                             #   keyword matching, repeat-echo
└── data/
    ├── keyword_sets.json    # seed keyword data (safe to hand-edit or
    │                         #   manage via !keyword commands)
    ├── copypasta_sets.json  # seed copypasta templates (same - hand-edit
    │                         #   or manage via !copypasta commands)
    ├── vote_settings.json   # generated at runtime, gitignored
    └── votes.json           # generated at runtime, gitignored
```

## Setup

```bash
pip install -r requirements.txt
cp key.py.example key.py     # then paste your bot token in
python bot.py
```

Get a token at https://discord.com/developers/applications -> your
application -> Bot -> Reset Token. The bot needs the `bot` **and**
`applications.commands` OAuth2 scopes when you generate its invite link,
or slash commands won't register (see GAPS.md).

Optionally set a `DEV_GUILD_ID` environment variable to a server ID you
control - `bot.py` will sync slash commands to that guild instantly instead
of globally (which can take up to ~1h to appear the first time).

`data/keyword_sets.json` and `data/copypasta_sets.json` ship pre-seeded
with example content so the bot is immediately usable out of the box;
edit either by hand or manage them live with `!keyword`/`!copypasta`.

## Managing keyword sets

Keyword sets are **global** - shared across every server the bot is in.
All `!keyword` commands require Administrator permission in the server
they're run from.

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

A message matches a set if any of its keywords appear as a
case-insensitive substring. If several sets match, one is picked at
random; then a random response from that set is sent.

## Managing & generating copypasta

Copypasta "types" are pools of fill-in-the-blank templates (also global,
same admin-only rule for management commands).

```
!copypasta <type> <value1> [value2 ...]   # post a random copypasta from that type's pool
!copypasta list                           # browsable, searchable menu of every type
!copypasta info <type>                    # browse a type's templates; pick one from the
                                           #   dropdown or hit Random to fill it in via a
                                           #   popup form, right from the menu
!copypasta create <type>                  # make a new empty type (admin)
!copypasta delete <type>                  # delete a type entirely (admin)
!copypasta enable <type> / disable <type> # toggle without deleting (admin)
!copypasta add <type> <template>          # add a template, e.g. '{text} is handsome' (admin)
!copypasta remove <type> <index>          # remove a template by index (admin)
```

A template can use any number of named `{placeholder}`s, not just
`{text}` - e.g. `{people}'s {act} is cool` needs two values, matched
positionally to that template's placeholders in the order they first
appear. `!copypasta info <type>` shows exactly what each template needs.

Some types can optionally carry per-game terminology (`game_terms` in
`copypasta_sets.json`) so a `{game}` placeholder auto-derives related
jargon (rating tiers, difficulty labels, clear-lamp terms, etc.) via
`{{term_key}}` tokens in the template text - see the module docstring in
`copypasta_manager.py` for the full shape.
