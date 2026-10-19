# Score distribution UI regression tests

- [x] Read the existing panel test helpers and SongDetailsView flow.
  - Result: Reused the temporary SQLite `ctx` fixture, `Message`, `interaction`, `assert_rows`, and the actual `SongDetailsView` select callbacks.
  - Changed: `workspace/distribution-ui/PLAN.md`
  - Verified: Confirmed `SongMoreSelect` uses `tab:rating`, difficulty changes use `SongDifficultySelect`, and public edits flow through the same message object.
  - Notes: The fixture blocks network; analysis is mocked at the service boundary.
- [x] Add focused UI regression tests for distribution selection, chart changes, overview clearing, and unavailable/error states.
  - Result: Added five cases covering public-message editing, canonical MAS/ULT chart selection, valid PNG attachment naming and content, selected rating state, attachment clearing, and actionable no-image failure states.
  - Changed: `tests/test_distribution_ui.py`
  - Verified: Tests run only against the temporary fixture database; the mocked source call avoids external requests and real Discord.
- [x] Run the focused tests and record current failures or passing checks.
  - Result: All five UI regression cases pass against the updated parent UI implementation.
  - Changed: `workspace/distribution-ui/PLAN.md`
  - Verified: `pytest tests/test_distribution_ui.py -q` passed (5 passed); Ruff check passed; Ruff format check passed.
  - Notes: Actionability assertions accept equivalent retry or difficulty-switch guidance without requiring exact copy.
