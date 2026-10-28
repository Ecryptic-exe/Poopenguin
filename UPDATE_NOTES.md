# Poopenguin updates

Merge the files into `C:\Users\user\PycharmProjects\LB_Penguin`, preserving their paths. Restart the bot and sync its command tree through the existing administrator workflow. The extension registry includes `chunithm.dailyupdate` by default; add that key to `[cogs] enabled` if your deployment uses an explicit allowlist.

## Files

Changed files (complete replacement contents):

- `chuni_penguin/adapters/chunithm_net/adapter.py`
- `chuni_penguin/calculation/border.py`
- `chuni_penguin/cogs/__init__.py`
- `chuni_penguin/ui/song_details.py`

New files:

- `chuni_penguin/cogs/chunithm/dailyupdate.py`
- `chuni_penguin/services/dailyupdate.py`
- `chuni_penguin/renderers/dailyupdate.py`
- `chuni_penguin/ui/dailyupdate.py`
- `chuni_penguin/ui/custom_justice.py`
- `assets/dailyupdate/NotoSansCJKjp-Regular.otf`
- `assets/dailyupdate/OFL.txt`
- `tests/test_dailyupdate.py`
- `tests/test_custom_justice.py`
- `UPDATE_NOTES.md`

## Configuration, storage, and dependencies

- **New config keys:** none. Existing guild language settings and the English default are reused.
- **JSON:** `data/dailyupdate/<Discord user ID>.json`, created on use. Stores timestamps, window boundaries, player statistics, and records; contains no cookies. Each day keeps its latest observation. Retention is 30 calendar days plus one older baseline, at most 31 snapshots in normal chronological use. Writes use a temporary file and atomic replacement; unreadable histories are preserved.
- **Jackets:** the existing `assets/jackets/<song ID>.webp` cache. Missing artwork gets a placeholder. Artwork downloads use the existing public, cookie-free jacket client.
- **Font:** bundled, unmodified Noto Sans CJK JP Regular, with its SIL Open Font License. Copied from the project's existing vendored font.
- **Runtime dependencies:** existing `discord.py`, Pillow, `msgspec`, `httpx`, `selectolax`, and the account service dependencies. `zoneinfo` uses the project's existing Windows `tzdata` dependency. No new package or API key is required. Tests use the project's existing pytest/pytest-asyncio test dependencies.
- Tunable constants are `HISTORY_DAYS` and `REQUEST_DELAY` in `services/dailyupdate.py`, and `PAGE_SIZE` in `renderers/dailyupdate.py`.

## Daily update behavior and reused functions

Use `/dailyupdate` or `!dailyupdate`. For saved history, use `/dailyupdate date:2026-09-20` or `!dailyupdate 2026-09-20`; that date names the window starting on September 20 at 04:00 Asia/Hong_Kong and ending September 21 at 04:00.

Live comparisons use the latest observation strictly before the current 04:00 boundary. Today's earlier observation is replaced after a successful fetch and is never used as today's baseline. Historical queries use the requested window's last observation and the latest earlier baseline, without fetching or saving. Skipped dates extend the comparison; missing target snapshots produce a clear message.

The subtitle shows the covered **window boundaries**. The next line gives the **actual capture timestamps**, and live results explicitly mark the current window as in progress. On-demand captures cannot reconstruct the state exactly at 04:00. First use saves a baseline; no-change results still save today's observation. There is no scheduler.

Total Score and cumulative S through MAX / FC / AJ counts cover BASIC, ADVANCED, EXPERT, MASTER, and ULTIMA. FC includes AJ/AJC; AJ includes AJC. MAX means 1,010,000. WORLD'S END records can appear in changed-chart rows but are excluded from these normal-chart aggregates. Over Power and its percentage come directly from the player's CHUNITHM-NET profile.

Reused existing functions:

- `AccountService.network(..., chunithm_net=True)`, including `_network_unlocked()`, `chunithm_net()`, and `_chunithm_net()`: the same stored-cookie, authentication, refresh, and cleanup path used by `/login` commands.
- `ChunithmNetAdapter.get_minimal_profile()` -> `parse_player_card_and_avatar()`: player name, level/reincarnation, rating, and Over Power. Called before records to establish the form token and afterward for the displayed profile.
- `ChunithmNetAdapter.get_all_personal_bests()` -> `_request_as_soup()` and `parse_music_for_rating()`: one page per difficulty, sequentially. Added optional pacing, HTTP status checks, and page validation; existing callers keep their defaults.
- `_get_hidden_personal_bests()` -> `get_personal_bests_on_song()` -> `parse_music_record()`: existing hidden-song coverage, with pacing and validation enabled for this command.
- `_process_records()` -> `process_records()` / `hydrate_records()`: existing catalog hydration and score processing.
- `JacketCache.ensure()`, `PenguinView.start()` / `edit_message()`, and the existing owner/close lifecycle.
- `get_chunithm_language()` and `t()`: existing English/Traditional Chinese selection.

Player records are fetched only from CHUNITHM-NET. Maintenance, authentication failures, HTTP errors, and detected malformed record pages return a message before any snapshot write. Fetching has a 12-minute deadline; image rendering runs through `asyncio.to_thread()` after deferral.

## Custom JUSTICE and verification examples

The button appears on the existing chart's Borders tab. Submission **updates the same embed** through `SongDetailsView.transition()`, preserving its dropdowns, ownership, timeout, and message lifecycle. Song/difficulty changes reset the custom value. A modal opened before another transition or expiry is rejected.

`calculate_border()` now accepts optional `justice=N`. Its default output is unchanged. Fixed-count results recover the original integer deduction budget, retain the original recommended MISS allocation where feasible, then maximize ATTACK within both the score budget and the remaining note count. MISS is reduced if necessary. 99AJ always requires zero ATTACK and MISS. The embed states this convention.

All four examples below are **L9 / MASTER / 13+ (13.6) / 1,103 notes**. Each cell is `JUSTICE-ATTACK-MISS`.

| JUSTICE | 99AJ | SSS+ | SSS | SS+ | SS | S+ | S |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0-0-0 | 0-2-0 | 0-5-0 | 0-8-1 | 0-13-4 | 0-27-8 | 0-38-19 |
| 50 | N/A | 50-1-0 | 50-4-0 | 50-7-1 | 50-12-4 | 50-26-8 | 50-37-19 |
| 275 | N/A | N/A | 275-0-0 | 275-3-1 | 275-8-4 | 275-22-8 | 275-32-19 |
| 1103 | N/A | N/A | N/A | N/A | 1103-0-0 | 1103-0-0 | 1103-0-0 |

The existing constants give exact loss `10,000 × (JUSTICE + 51 × ATTACK + 101 × MISS) / notes`. The displayed per-note deductions remain the existing truncated values. For 50 JUSTICE at SSS+, `50-1-0` scores approximately 1,009,084.32; `50-2-0` falls below 1,009,000. For 1,103 JUSTICE, every note is already allocated, so ATTACK and MISS must both be zero.

**Validation:** 138 targeted tests passed, including the existing border, song-details/data, account, interaction, dismissal, select, and jacket tests. New coverage includes every JUSTICE count for several chart sizes, day boundaries, history retention, atomic-write failures, failed fetch preservation, modal validation/ownership/staleness, hybrid registration, and pagination expiry. New modules and the border helper pass the project's Ruff rules. English and Chinese seven-row images were rendered and visually checked. Network behavior was verified with synthetic pages and mocked sessions; live-session verification remains a deployment check.

API references: [discord.py interactions and modals](https://discordpy.readthedocs.io/en/stable/interactions/api.html), [Python zoneinfo and tzdata](https://docs.python.org/3/library/zoneinfo.html).
