# Alias Learning Work Plan

- [x] Implement a private guild alias learning service with normalized validation, safe conflict inspection, and transactional confirmation.
  - Result: Added `AliasLearningService.inspect` and `confirm`, public NFKC/casefold normalization helpers, conflict/result dataclasses, guild-only ID validation, and a serialized recheck-before-insert path. Existing alias rows are never changed; the cache reloads only after an insert commits.
  - Changed: `chuni_penguin/services/alias_learning.py`
  - Verified: Focused tests cover inspection and commit behavior, including the confirmation transaction.
  - Notes: Title matching uses normalized Unicode comparison across song titles; title candidates have `rowid=None`.
- [x] Add isolated database tests for Unicode/case normalization, duplicate and title collisions, owner preservation, invalid input, and concurrent confirmations.
  - Result: Added 17 isolated tests covering Unicode compatibility/casefold/spacing, same-name duplicates across scopes and within a scope, title collisions, owner preservation, validation, stale inspection, and competing confirmations.
  - Changed: `tests/test_alias_learning.py`
  - Verified: All 17 focused tests passed against temporary SQLite databases created from explicit test configuration.
  - Notes: Tests do not access the live database or environment configuration.
- [x] Run the focused tests and record the concrete outcome here.
  - Result: Focused suite passed and Ruff reported no issues.
  - Changed: `workspace/alias-learning/PLAN.md`
  - Verified: `python -m pytest tests/test_alias_learning.py -q` — 17 passed; Ruff 0.13.3 — all checks passed.
  - Notes: Ruff was run through an isolated offline tool environment because it is not installed in the project virtual environment.
