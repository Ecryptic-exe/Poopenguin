# Poop Pengin (vto bot)

Every user-facing command works both
as a `!`-prefixed text command and as a `/` slash command (see
[`COMMANDS.md`](COMMANDS.md) for the full list and the couple of
exceptions).

## Layout

```
Pooping Penguin/
├── VTO.py                   # entry point: builds the bot, loads cogs, syncs
│                             #   slash commands, runs it
├── key.py                   # NOT included — see key.py.example. api = "your token"
├── config.py                 # JSON load/save helpers for vote_settings.json
├── i18n.py                   # t(language, en, zh) + get_guild_language()
├── keyword_manager.py         # engine behind the global keyword-triggered responses
├── copypasta_manager.py       # engine behind the !copypasta template pools
├── cleanup_settings.py        # one-off helper for pruning stale autoreact entries
├── requirements.txt
├── cogs/
│   ├── help_cog.py            # !help
│   ├── vote_cog.py            # !vto, !setvote
│   ├── admin_cog.py           # !setperms, !autoreact, !lang
│   ├── general_cog.py         # !ask, !pick, !rng, !rcg
│   ├── keywords_cog.py        # !keyword ... (manage keyword sets live)
│   ├── copypasta_cog.py       # !copypasta / !cp ... (manage copypasta types live)
│   └── messages_cog.py        # on_message pipeline: autoreact, mentions,
│                               #   keyword matching, repeat-echo
└── data/
    ├── keyword_sets.json      # live keyword data (safe to hand-edit or
    │                           #   manage via !keyword commands)
    ├── copypasta_sets.json    # live copypasta template data (safe to hand-edit
    │                           #   or manage via !copypasta commands)
    ├── vote_settings.json     # generated at runtime (language, autoreact, vote config)
    └── votes.json             # generated at runtime
```

## Setup

```
pip install -r requirements.txt
cp key.py.example key.py   # then paste your bot token in
python VTO.py
```

`data/keyword_sets.json` and `data/copypasta_sets.json` already ship
seeded with the original hardcoded content, so behaviour is unchanged
out of the box.

### Slash command sync & `DEV_GUILD_ID`

Slash ("/") commands have to be registered with Discord separately from
just loading the cogs. `VTO.py` does this once per process, in
`setup_hook()`:

- **No `DEV_GUILD_ID` set** → commands sync **globally**. Works in every
  server the bot is in, but Discord can take up to ~1 hour to show new
  or changed global commands to users the first time.
- **`DEV_GUILD_ID` set** to a server ID you control → commands sync to
  that guild only, which Discord applies instantly (good for
  development). `setup_hook()` also clears out any commands that were
  previously registered *globally*, so the same command can't end up
  listed twice in Discord's `/` picker (once as a global entry, once as
  a guild entry) — see the comment in `VTO.py` for details.

```
export DEV_GUILD_ID=123456789012345678   # your test server's ID
python VTO.py
```

Leave `DEV_GUILD_ID` unset for production so commands sync globally to
every server the bot is invited to.

The bot must also be invited (or have its invite link updated) with the
`applications.commands` OAuth2 scope in addition to `bot`, or slash
commands won't show up at all — see `GAPS.md`.

## Managing keyword sets

Keyword sets are **global** — shared across every server the bot is in.
All `!keyword` subcommands require Administrator permission in the
server they're run from.

```
!keyword                                  # prints the subcommand list (text-only, no slash)
!keyword list [search]                    # browsable/searchable menu of all sets
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

Every subcommand above (`list`/`show`/`create`/.../`removeresponse`) also
works as a `/keyword ...` slash command. The bare `!keyword` (no
subcommand) is text-only, since Discord doesn't allow a slash command
group to be invoked directly.

## Managing copypasta types

`!copypasta <type> <values>` (alias `!cp`) posts a random line from that
type's template pool, swapping in the value(s) you give for the
template's `{placeholder}`s. Three seed types ship out of the box: `tag`,
`activity`, `song` — plus common aliases like `name`/`person` for `tag`.

```
!copypasta tag @User                      # e.g. "@User is handsome"
!copypasta activity digging
!copypasta song a song

!copypasta list                           # list all types + template counts
!copypasta show <type>                    # see every template in a type
!copypasta create <type>                  # make a new empty type          (admin only)
!copypasta delete <type>                  # delete a type entirely         (admin only)
!copypasta enable <type> / disable <type> # toggle without deleting        (admin only)
!copypasta add <type> <template>          # add a template, needs {text}   (admin only)
!copypasta remove <type> <index>          # remove by index (see `show`)   (admin only)
```


## Further reading

- [`COMMANDS.md`](COMMANDS.md) — every command, whether it has a `/`
  slash equivalent, and why the two group actions (`copypasta`,
  `keyword`) don't.
- [`GAPS.md`](GAPS.md) — known trade-offs and setup steps still open
  (OAuth2 scope, dev-guild sync, slash argument limitations, etc.)
