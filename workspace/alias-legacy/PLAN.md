# Legacy Alias Conflict Guards

## Plan

- [x] Inspect legacy alias lookups, scope rules, and fixture database patterns.
  - Result: Found three scalar lookups that could raise on historical duplicates; confirmed source visibility is global plus current guild (or all guilds for global additions), and removal ownership/scope filters must remain intact.
  - Changed: None.
  - Verified: Read `AliasService`, alias command call sites, database models, and fixture setup.
  - Notes: The shared alias normalization helper is in `alias_learning.py`; legacy service will use a small local equivalent to avoid a circular import.
- [x] Guard add-source resolution, guild conflict lookup, and alias removal against historical duplicates.
  - Result: Legacy text is matched with local NFKC, collapsed-whitespace, and casefold normalization. Source resolution accepts repeated rows for one song and raises `BadArgument` with candidate titles and IDs for multiple targets or canonical title matches. Guild conflict lookup returns the first global/current row by stable scope and row ordering when targets agree, and raises a readable conflict for different targets. Removal raises `BadArgument` whenever more than one eligible row matches.
  - Changed: `chuni_penguin/services/catalog.py`.
  - Verified: Covered by the focused alias tests recorded below.
  - Notes: Existing source visibility and removal owner/guild scope predicates remain in place; no global promotion code changed.
- [x] Add regression tests for duplicate sources, title ambiguity, scope filtering, and safe removal.
  - Result: Added direct service tests for normalized duplicate sources, current/global/all-guild source scope, duplicate canonical titles, deterministic same-song conflicts, multi-target conflicts, ownership filters, and manager scope. Confirmed ambiguous removal leaves all alias rows intact.
  - Changed: `tests/test_alias_legacy_conflicts.py`.
  - Verified: Tests pass with the existing alias-learning regression suite.
- [x] Run focused alias service tests and record the outcomes.
  - Result: `tests/test_alias_legacy_conflicts.py` and `tests/test_alias_learning.py` passed (24 passed).
  - Changed: `workspace/alias-legacy/PLAN.md`.
  - Verified: Focused pytest run completed successfully. Ruff check was unavailable because Ruff is not installed in this checkout's virtual environment.
  - Notes: No other files were changed by this delegated work.
