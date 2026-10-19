# Account UI State Helper

- [x] Rewrite `docs/EMBED_UX_PROPOSAL.md` as a verified Account and Song interface inventory, including controls, conditions, return paths, and remaining validation.
  - Result: Replaced the stale proposal and deployment claims with current A0–A6 and S0–S6 interface tables, per-source/per-feature conditions, return and recovery paths, existing private review/alias exceptions, and explicit pending integration, regression, deployment, and human visual checks.
  - Changed: `docs/EMBED_UX_PROPOSAL.md`
  - Verified: Compared each documented control and condition with the current account state, panel, chart results, song details, song search, and review implementation. Confirmed the old “still proposal” and prior deployment/test counts are absent.

- [x] Define the source state model and safe action/status rules from the existing account stores.
  - Result: Mapped five source states and action flags to the existing cookie and `PlayerSource` stores; the returned model retains no credentials, payloads, or user IDs.
  - Changed: `chuni_penguin/ui/account_state.py`
  - Verified: Confirmed against the existing panel routes, account models, and player source sync behavior.
- [x] Implement the read-only account state loader and source lookup helper.
  - Result: Added `load_account_state(ctx)`, ordered source state objects, and `.source(key)` lookup. Kamaitachi readiness follows the loaded `Kamaitachi` cog; malformed NET credentials remain clearable. Kamaitachi unlink stays disabled because the current panel action does not support it; Chunirec status preserves link state alongside sync results.
  - Changed: `chuni_penguin/ui/account_state.py`
  - Verified: Focused tests cover available and unavailable states.
- [x] Add tests for mixed account states, source-specific actions, and secret-free results.
  - Result: Added database-backed coverage for NET, Kamaitachi, both Chunirec regions (including a snapshot without credentials), Chunilib imports, unknown keys, disabled integration actions, and secret exclusion from the returned object representation.
  - Changed: `tests/test_account_state.py`
  - Verified: 2 focused tests passed.
- [x] Run focused tests and record the verified outcome.
  - Result: Account-state tests, lint, and formatting checks pass.
  - Changed: `workspace/account-ux-state/PLAN.md`
  - Verified: `uv run python -m pytest tests/test_account_state.py` (2 passed); `uv run ruff check chuni_penguin/ui/account_state.py tests/test_account_state.py` (passed); `uv run ruff format --check chuni_penguin/ui/account_state.py tests/test_account_state.py` (passed).
