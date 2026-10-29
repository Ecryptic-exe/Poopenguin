# B Feature Inventory

“B” refers to the former `legacy_b` command set, separate from CHUNITHM
features. Its implementations are organized under `chuni_penguin.cogs.poopenguin`
and each former `chuni_penguin.legacy_b.*` import path aliases the same module.
The aliases preserve existing extension configuration, test monkeypatching, and
runtime access without loading duplicate cogs or state.

| Former module | Canonical extension | Default | Feature inventory |
| --- | --- | --- | --- |
| `general_cog` | `poopenguin.general_cog` | On | `ask`, `pick`, `rng`, and `rcg`. |
| `gacha_cog` | `poopenguin.gacha_cog` | On | Pull menu; target selection; 200-pull personal pity; records and confirmed reset; roster browsing, rates, and owner artwork/pool management. |
| `copypasta_cog` | `poopenguin.copypasta_cog` | On | Type browsing and generation, named placeholders, and owner CRUD for templates. |
| `keywords_cog` | `poopenguin.keywords_cog` | On | Trigger-set browsing, substring matching, independent trigger rates, and owner CRUD. |
| `messages_cog` | `poopenguin.messages_cog` | Optional, off | Ordered message processing for auto-ban, auto-reaction, mention/menu/help/AI routing, keywords, and repeat echoes. |
| `vote_cog` | `poopenguin.vote_cog` | Optional, off | Timeout voting, reaction tally, threshold configuration, and optional admin-only voting. |
| `admin_cog` | `poopenguin.admin_cog` | Optional, off | Channel/role permissions, auto-reaction and auto-ban configuration, and language selection. |
| `feed_cog` | `poopenguin.feed_cog` | Optional, off | Bluesky feed management and scheduled polling. |
| `ai_cog` | `poopenguin.ai_cog` | Optional, off | Local model chat, per-channel conversation context, image descriptions, and conversation reset. |

`config`, `i18n`, `ai_settings`, `gacha_manager`, `copypasta_manager`, and
`keyword_manager` are support modules moved alongside the cogs. They are not
extensions and do not register commands by themselves. The JSON path remains
`data/poopenguin/`; all old data files remain in place.

The four required cogs keep their existing default-enabled behavior. Optional
extensions remain opt-in and can be selected with either their canonical key or
their former `chuni_penguin.legacy_b.*_cog` key. Status and load/unload operations
run against the canonical spec, while legacy status lookups remain readable.

The old B command set did not retain separate `help` or `sync` commands. Help is
shared across the bot, and command-tree registration is owned by the common
application lifecycle. This avoids duplicate registrations while preserving the
available help and sync flows.
