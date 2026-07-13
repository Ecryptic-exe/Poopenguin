# Copypasta slash-command gaps

This lists what's still open specifically around `/copypasta` after the
hybrid (`!` + `/`) conversion, plus the one-time Discord setup it depends
on. Each gap has a placeholder showing what needs to be filled in and by
whom (you, in the Discord Developer Portal / server settings - none of
this can be done from the code alone).

## 1. Bot needs the `applications.commands` OAuth2 scope

Slash commands will silently fail to register (`on_ready` will log a
`discord.Forbidden` and skip the sync) if the bot's existing server
invite only granted the `bot` scope.

```
<!-- FILL IN: re-invite the bot (or use "Edit App" in the Discord
Developer Portal > OAuth2 > URL Generator) with BOTH scopes checked:
  [ ] bot
  [ ] applications.commands
Then re-invite/authorize on any server that needs /copypasta. -->
```

## 2. Global vs. dev-guild sync

`VTO.py` syncs globally by default, which can take up to ~1 hour to
appear the first time. `DEV_GUILD_ID` is read from the environment for
instant guild-scoped syncing while testing.

```
<!-- FILL IN: export DEV_GUILD_ID=<your test server's ID> before
running the bot while developing/testing /copypasta. Unset it (or
don't set it at all) for the production deploy so the commands sync
globally to every server the bot is in. -->
```

## 3. `/copypasta use` was removed - posting a copypasta is text-only now

Discord does not allow invoking a slash command group directly - only
one of its named subcommands. `!copypasta tag @User` is unchanged, but
a slash equivalent would have needed a subcommand name (previously
hardcoded to `use` via `fallback="use"` in `copypasta_cog.py`). That
fallback has been removed on purpose, so `/copypasta use ...` no
longer exists - posting a copypasta is `!copypasta <type> <values>` /
`!cp <type> <values>` only. The management subcommands
(`/copypasta list`, `show`, `create`, `delete`, `enable`, `disable`,
`add`, `remove`) are unaffected and still work as slash commands.

```
<!-- FILL IN (optional): if you want a slash equivalent for posting a
copypasta back, re-add fallback="<word>" to the @commands.hybrid_group
decorator in cogs/copypasta_cog.py (e.g. fallback="use", "fire",
"post", "send") and restore the @app_commands.describe /
@app_commands.autocomplete decorators on the group's callback that
were removed alongside it. -->
```

## 4. `values` is one text field, not a list

`!copypasta thattype Alice basketball` takes any number of positional
arguments over text. This only matters if you re-add a slash
equivalent (see item 3 above) - slash commands can't take an
open-ended argument list, so a `/copypasta use` would need a single
`values` string split on whitespace, meaning an individual value
couldn't contain a space (e.g. a two-word song title, or a display
name with a space in it).

```
<!-- FILL IN (optional, only if this bites you in practice): pick one -
  (a) leave as-is: single-word values only per placeholder in slash use,
  (b) change the split to a delimiter you control (e.g. "|"), e.g.
      /copypasta use type:thattype values:"New York|basketball",
  (c) add one dedicated slash option per known placeholder count
      (only works if every type is capped at, say, 2-3 placeholders). -->
```

## 5. Autocomplete is capped at 25 results

Discord hard-caps autocomplete responses to 25 choices. `type_autocomplete`
in `copypasta_cog.py` already truncates to `AUTOCOMPLETE_LIMIT = 25` so it
won't error, but if there are ever more than 25 copypasta types (+
aliases) in play, the least-relevant ones (alphabetically, after the
current text filter) won't show up in the dropdown - `!copypasta list`
still shows all of them regardless.

```
<!-- FILL IN (only if this becomes a real problem): change the ordering
in type_autocomplete() from alphabetical to something like "most
recently used first" (would need to track usage across commands, not
just per-render like _last_used does today), or split types into
categories so no single autocomplete call needs to show more than 25. -->
```

## 6. `setperms`'s channel/role ID inputs weren't upgraded to native pickers

`/setperms` (admin_cog.py, not copypasta, but adjacent enough to flag)
still takes raw ID strings instead of Discord's native channel/role
picker UI, to keep behaviour identical to the old `!setperms <channel_id>
<role_id>`.

```
<!-- FILL IN (optional): change the parameter types in admin_cog.py's
setperms() from `channel_id: str, role_id: str` to
`channel: discord.TextChannel, role: discord.Role` for a native picker
in the slash UI. This is a real behaviour change for the "!" version too
(users would mention/pick instead of pasting an ID), so it's left alone
here rather than assumed. -->
```
