# Known gaps / follow-ups

Things the code comments point here for - collected in one place instead of
scattered across docstrings. None of these block running the bot; they're
trade-offs made while converting everything to hybrid (`!`/`/`) commands.

## Slash commands need an extra OAuth2 scope

Slash (`/`) commands only show up in Discord's UI if the bot was invited
with the `applications.commands` scope, not just `bot`. If `bot.py`'s
startup log says it failed to sync with a `Forbidden`/missing-scope error,
re-invite the bot (or edit its existing invite) with that scope checked.

## `/copypasta` and `/keyword` can't run without a subcommand

Discord doesn't allow invoking a slash command *group* directly - only its
named subcommands. So:

- `!copypasta tag @User` (bare group callback) still works as a text
  command, but there's no equivalent `/copypasta tag ...` - only the
  management subcommands (`/copypasta list`, `/copypasta info`, etc.) are
  real slash commands.
- Same shape for `!keyword` vs `/keyword <sub>`.

Posting a copypasta from Discord's UI (rather than typing `!copypasta`) is
done via the buttons/dropdown in `/copypasta info <type>` instead.

## `/copypasta` values can't contain spaces

Slash command options are a fixed list, not an open-ended `*args` the way
`!copypasta tag "a name with spaces"` can take. `/copypasta`'s `values`
option is one string, split on whitespace - so an individual value (e.g. a
multi-word song title) can't contain a space when using the slash version.
The `!copypasta` text command and the Fill-in-template/Random modals in
`/copypasta info` don't have this limitation (each placeholder gets its own
text box).

## Autocomplete is capped at 25 choices

Discord caps slash-command autocomplete dropdowns at 25 entries per field.
`type_autocomplete` in `copypasta_cog.py` truncates to the first 25
matches - fine for a small number of copypasta types, but if the list
grows past that, some types just won't be suggested (typing the full name
by hand still works).

## `!setperms` takes raw IDs, not native pickers

`channel_id`/`role_id` in `admin_cog.py` are plain strings rather than
`discord.TextChannel`/`discord.Role` converters, so `!setperms`/`/setperms`
still take a raw ID exactly like the original bot did. Upgrading these to
native slash channel/role pickers (nicer UX, but a behavior change) hasn't
been done.
