# Review background loader

Scope follows the 2026-10-16 review-background item in the root `PLAN.md`: use the current owner’s actual Rating when available, reuse validated saved background for everything else, and leave unavailable fields explicit.

- [x] Inspect review allowlists, native profile Rating semantics, and source snapshot shape.
  - Result: Confirmed the current bucket/skill allowlists, that only `RatingType.in_game` is an actual overall Rating, that Kamaitachi minimal profiles need the full-profile fallback, and that `PlayerSource.payload` contains the snapshot while credentials are a separate column.
  - Changed: No production files; findings captured in this plan.
  - Verified: Read `services/chart_reviews.py`, native adapter/profile definitions, `services/accounts.py`, `services/player_sources.py`, and `database/player_sources.py`.
  - Notes: Root PLAN’s current task requires Rating-only source-derived data and explicit unknown values for other missing fields.
- [x] Implement a read-only background loader for native accounts and the owner’s explicit PlayerSource snapshots, with strict Rating validation, saved fallback, timeout, and cancellation-safe cleanup.
  - Result: Added `load_review_background`; it uses only actual in-game Rating, falls back from minimal to full profile when needed, times out native reads after eight seconds, queries only the owner’s snapshot payload, and returns validated saved fields or `未提供`/empty skills. Cancellation propagates after context cleanup.
  - Changed: `chuni_penguin/services/review_background.py`.
  - Verified: Covered native context flags and close behavior, native failure and timeout fallback, full-profile fallback, saved validation, and cancellation cleanup in focused tests.
  - Notes: Native `net` and `chunithm_net` sources select the Chunithm.net account context; `default` leaves account selection to AccountService.
- [x] Add focused tests for boundaries, saved/missing values, errors and cancellation, source ownership/isolation, and credential exclusion; run focused tests and lint.
  - Result: Added 33 cases covering rating boundaries and invalid values, saved-profile sanitization, native source selection/fallback/failure/timeout/cancellation, and explicit snapshot ownership and secret exclusion.
  - Changed: `tests/test_review_background.py`.
  - Verified: `uv run python -m pytest -q tests/test_review_background.py` passed (33 passed); `uv run ruff check chuni_penguin/services/review_background.py tests/test_review_background.py` passed.
  - Notes: Used `python -m pytest` because the standalone `uv run pytest` entry point did not include the project root on `sys.path` in this environment.
