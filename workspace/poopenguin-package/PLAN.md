# Poopenguin Package Work Plan

- [x] Inventory current Poopenguin source modules, command surfaces, optional extensions, and legacy test expectations.
  - Result: Cataloged nine cogs and six support modules, preserved the four default-on and five opt-in extension split, and recorded the command/decorator inventory in the new regression test.
  - Changed: `tests/test_poopenguin_package.py`
  - Verified: Compared the canonical source decorator inventory with an explicit expected manifest; retained the existing extension and test compatibility expectations.
- [x] Move Poopenguin implementations into `chuni_penguin/cogs/poopenguin/` and retain legacy import paths as module-identity shims.
  - Result: Moved all nine cog modules and six support modules; legacy module imports now resolve to the exact canonical module object so monkeypatches and module state stay shared.
  - Changed: `chuni_penguin/cogs/poopenguin/`, `chuni_penguin/legacy_b/`
  - Verified: Identity assertions passed for all 15 former imports; stored data path remains `data/poopenguin`.
- [x] Update canonical extension specs and alias handling while preserving defaults, optional status, and legacy configuration keys.
  - Result: Registered nine canonical specs with legacy extension keys as aliases. Selection and lifecycle resolve either key; status-map lookup supports aliases while iteration and JSON serialization contain canonical rows only.
  - Changed: `chuni_penguin/cogs/__init__.py`, `chuni_penguin/application/extensions.py`
  - Verified: Alias selection/default assertions pass; report assertions confirm aliases resolve in memory and do not duplicate serialized extension rows.
- [x] Add focused package, command behavior, compatibility, and decorator-inventory coverage in `tests/test_poopenguin_package.py`.
  - Result: Added offline tests for module identity, command schemas, random/pick/rng, gacha target/pull/stats/reset/pool/artwork and rebuilt controls, template/keyword CRUD and ownership, plus mocked optional admin/messages/vote/feed/AI paths.
  - Changed: `tests/test_poopenguin_package.py`
  - Verified: Included in the 46 passing scoped regression tests. The tests use temporary JSON stores and mocks; they do not exercise live Discord, feed providers, or an LM Studio server.
- [x] Document Poopenguin and B feature inventories in `docs/POOPENGUIN_FEATURES.md` and `docs/B_FEATURES.md`.
  - Result: Documented all nine cogs, command surfaces, default/optional status, support modules, data location, and shared help/sync ownership.
  - Changed: `docs/POOPENGUIN_FEATURES.md`, `docs/B_FEATURES.md`
  - Verified: Matched the documents against the expected decorator inventory and canonical extension specs.
- [x] Run scoped Poopenguin and relevant regression tests, lint the scoped implementation, and record actual outcomes plus meaningful issues.
  - Result: Ruff lint and format checks pass for the compatibility shims, extension alias handling, and new tests. The moved legacy implementation files still produce existing Ruff diagnostics when linted as a whole; they were moved without feature rewrites.
  - Changed: `workspace/poopenguin-package/PLAN.md`
  - Verified: `python -m pytest tests/test_poopenguin_package.py tests/test_phase1.py` plus four selected Poopenguin panel tests: 46 passed, 1 stale serialized-alias assertion deselected. With that assertion included, 46 passed and `test_startup_required_commands_and_safe_defaults` initially failed because it expected a legacy alias row in JSON. Scoped Ruff lint passed; Ruff format check reported 18 files already formatted.
  - Notes: Legacy status lookup remains available on the in-memory map. Serialized reports intentionally contain canonical specs only, per the integration requirement. The parent updated the out-of-scope phase1 assertion to read the canonical key; the later combined run passed it.

## Lifecycle Alias Follow-up

- [x] Keep legacy and canonical module references synchronized during Discord extension load, reload, rollback, and unload.
  - Result: ExtensionManager now refreshes `sys.modules` and both package attributes after load/reload, removes stale legacy aliases and canonical/legacy package attributes after unload, and only applies this behavior to aliased specs. A failed reload restores the previous module identity and alias.
  - Changed: `chuni_penguin/application/extensions.py`
  - Verified: Added a real `commands.Bot` regression covering load, successful reload, failed reload rollback, unload, normal canonical-package re-import, legacy re-import, and canonical-only extension registration.
- [x] Re-run lifecycle-sensitive package and UI regressions and lint the lifecycle repair.
  - Result: Scoped lifecycle run passed; no separate legacy extension key was registered.
  - Changed: `tests/test_poopenguin_package.py`, `workspace/poopenguin-package/PLAN.md`
  - Verified: `python -m pytest tests/test_phase1.py`, four selected Poopenguin panel tests, and `tests/test_poopenguin_package.py`: 48 passed. Ruff check passed and Ruff format check reported both edited Python files already formatted.
  - Notes: The provided full-suite report reproduced 677 passed / 1 failed before the repair. The parent will rerun the full suite after this focused fix.
