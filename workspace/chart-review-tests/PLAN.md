# Chart review service tests

- [x] Add a temporary `DatabaseService` fixture and reusable chart, profile, review, and history helpers.
  - Result: Added a per-test SQLite fixture through `create_fixture_database`, `DatabaseService`, and the real writer/read-only session paths, with helpers for charts, valid inputs, and history inspection.
  - Changed: `tests/test_chart_reviews.py`
  - Verified: All focused tests pass against isolated temporary databases.
- [x] Test validation, chart foreign keys, UInt64 owner IDs, revisions, snapshots, and stale or cross-owner writes.
  - Result: Covered required and optional profile fields, review field boundaries, invalid submissions with empty tables, SQLite chart-FK enforcement, high-bit UInt64 round trips, upsert history and snapshots, stale revision atomicity, and owner/chart isolation.
  - Changed: `tests/test_chart_reviews.py`
  - Verified: The targeted suite passes, including the real writer transaction and read-session assertions.
- [x] Test review/profile deletion, anonymous public summaries, minimum sample size, and complementary suppression.
  - Result: Covered per-chart deletion and history cascade, profile deletion with owner-only review/history cascade, anonymous aggregate shape, the five-review threshold, suppression for an 8-person 5/3 split, and allowed groups for a 10-person 5/5 split.
  - Changed: `tests/test_chart_reviews.py`
  - Verified: The targeted suite passes all deletion and aggregate checks.
- [x] Run focused tests and Ruff checks; record the implementation review findings and verified result here.
  - Result: All 11 focused tests and Ruff checks pass. Read-only review of the chart-review model/service found no concrete correctness or privacy defect within the agreed behavior.
  - Changed: `workspace/chart-review-tests/PLAN.md`
  - Verified: `python -m pytest tests/test_chart_reviews.py -q` (11 passed); Ruff check passed; Ruff format check passed.
  - Notes: Ruff 0.13.3 was run through `uvx` because it is absent from the project virtual environment.
