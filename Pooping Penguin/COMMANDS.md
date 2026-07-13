# Command Reference — Slash Support

Every command is a `commands.hybrid_command` (or lives inside a
`commands.hybrid_group`), which means the `!`-prefixed text version and
the `/` slash version run the exact same function. Almost everything
below has both. The two exceptions are the two group "base" actions
(`copypasta` and `keyword`), which Discord's slash system won't let you
invoke directly — only their named subcommands can be slash commands, so
those two rows are text-only.

Legend: ✅ = works both as `!command` and `/command` · 🚫 = text-only, no
slash equivalent (see *Notes*).

## General

| Command | Slash? | Description |
|---|---|---|
| `!help [command]` | ✅ `/help` | Displays the user manual for the bot or specific command details. |
| `!ask <question>` | ✅ `/ask` | Asks a question and receives a response based on a random success rate. |
| `!pick <choice1> [choice2] ...` | ✅ `/pick` | Randomly selects one option from a list of provided choices. |
| `!rng [min] [max] [int/float]` | ✅ `/rng` | Generates a random number between a specified minimum and maximum. |
| `!rcg` | ✅ `/rcg` | Generates a random color in hexadecimal format with a preview. |

## Moderation & Admin

| Command | Slash? | Description |
|---|---|---|
| `!vto <@member> [time]` | ✅ `/vto` | Initiates a vote to timeout a member from the server. |
| `!setvote <number\|admin>` | ✅ `/setvote` | Configures the timeout voting system. **Admin only.** |
| `!setperms <channel_id> <role_id>` | ✅ `/setperms` | Grants a role view/send/read-history permissions in a channel. **Admin only.** |
| `!autoreact [emoji] [user]` | ✅ `/autoreact` | Sets or disables auto-reactions for messages in the channel. |
| `!lang` | ✅ `/lang` | Toggles the bot's help-panel language between English and Chinese for this server. |

## Copypasta (`cogs/copypasta_cog.py`)

| Command | Slash? | Description |
|---|---|---|
| `!copypasta <type> <values>` (alias `!cp`) | 🚫 text-only | Posts a random copypasta from a chosen type's template pool. No `/copypasta use`. |
| `!copypasta list` | ✅ `/copypasta list` | Lists all copypasta types and how many templates each has. |
| `!copypasta show <type>` (alias `info`) | ✅ `/copypasta show` | Shows every template in one copypasta type. |
| `!copypasta create <type>` | ✅ `/copypasta create` | Creates a new, empty copypasta type. **Admin only.** |
| `!copypasta delete <type>` | ✅ `/copypasta delete` | Deletes a copypasta type entirely. **Admin only.** |
| `!copypasta enable <type>` | ✅ `/copypasta enable` | Enables a copypasta type so `!copypasta` can pick it again. **Admin only.** |
| `!copypasta disable <type>` | ✅ `/copypasta disable` | Disables a copypasta type without deleting it. **Admin only.** |
| `!copypasta add <type> <template>` | ✅ `/copypasta add` | Adds a template to a type. Must contain a `{text}`-style placeholder. **Admin only.** |
| `!copypasta remove <type> <index>` | ✅ `/copypasta remove` | Removes a template by its index (see `show`). **Admin only.** |

`type` has autocomplete on every slash subcommand above that takes one —
it suggests real type names only (not aliases like `name`/`person` for
`tag`), so the dropdown doesn't show what looks like duplicate types for
the same underlying pool. Aliases still work fine if typed by hand.

## Keywords (`cogs/keywords_cog.py`)

Keyword sets are **global** — shared across every server the bot is in.
All `!keyword` subcommands require Administrator permission.

| Command | Slash? | Description |
|---|---|---|
| `!keyword` | 🚫 text-only | Prints the subcommand list. No bare `/keyword`. |
| `!keyword list [search]` | ✅ `/keyword list` | Lists all keyword sets in a browsable, searchable menu. |
| `!keyword show <id>` (alias `info`) | ✅ `/keyword show` | Shows the keywords and responses for one set. |
| `!keyword create <id>` | ✅ `/keyword create` | Creates a new, empty keyword set. |
| `!keyword delete <id>` | ✅ `/keyword delete` | Deletes a keyword set entirely. |
| `!keyword enable <id>` | ✅ `/keyword enable` | Enables a keyword set so it starts matching messages again. |
| `!keyword disable <id>` | ✅ `/keyword disable` | Disables a keyword set without deleting it. |
| `!keyword addkeyword <id> <word>` | ✅ `/keyword addkeyword` | Adds a trigger keyword to a set. |
| `!keyword removekeyword <id> <word>` | ✅ `/keyword removekeyword` | Removes a trigger keyword from a set. |
| `!keyword addresponse <id> <text>` | ✅ `/keyword addresponse` | Adds a candidate response text to a set. |
| `!keyword removeresponse <id> <index>` | ✅ `/keyword removeresponse` | Removes a response by its index. |
