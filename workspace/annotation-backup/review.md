# Read-only annotation safety review

Scope: `penguin/annotation_import.py`, `chuni_penguin/repositories/chart_annotations.py`, `chuni_penguin/database/annotations.py`, the backup helper, and relevant tests/migration. No live DB, `.env`, or bot was opened.

## Findings

- Normal import writes only `source_snapshots`, `chart_annotations`, and `chart_annotation_tags`. Catalog identity is checked before write; player tables are not addressed. Dry run opens SQLite with `mode=ro`, enables `query_only`, starts a read transaction, and rolls it back. Apply starts `BEGIN IMMEDIATE`; DDL, snapshot, annotation/tag inserts, and active-version changes commit together. `BaseException` triggers rollback.
- Version activation checks that each namespace has at most one active snapshot, deactivates its current batch, activates the selected batch, and keeps old rows. Rollback checks that the requested batch is active and restores its recorded predecessor. The repository reads only active rows whose chart/song/difficulty still match the canonical chart. Downgrade refuses while any annotation rows remain.
- Focused isolated tests passed: `tests/test_annotation_import.py` plus `tests/test_annotation_backup.py` (19 passed).

## High-value issues

1. **Importer-created schema can conflict with Alembic.** When annotation tables are absent, `_schema(..., install=True)` creates them but does not advance `alembic_version`. The repository also has migration `4c6f8b1a0d92` that unconditionally creates those same tables. An isolated reproduction starting at its predecessor revision confirmed: apply import first, then `alembic upgrade head` fails with `OperationalError: table chart_annotations already exists`. This does not alter player rows, but leaves schema migration state inconsistent and blocks a later upgrade. Choose one schema owner or make the migration reconcile the importer-installed schema.
2. **Live bot writes can be delayed by apply.** Import/rollback hold `BEGIN IMMEDIATE` for the whole operation. The bot also uses `BEGIN IMMEDIATE` for writes and has `PRAGMA busy_timeout=10000`; its writes wait behind an external import and can fail if the lock lasts over 10 seconds. Schedule apply/rollback when write traffic is low, or route the operation through the bot's writer queue if it must run live.
3. **Backup publication is not atomic across process interruption.** The helper reserves the final destination with an empty exclusive file, then SQLite populates that path. Python-handled failures clean up the owned file, but a hard process termination can leave an empty/partial target that a retry refuses as `DESTINATION_EXISTS`. Use a fresh destination name after interruption; a temp sibling plus atomic no-replace publication would remove this edge.
4. **Fingerprint memory grows with the largest table.** It materializes one SHA-256 object per row and sorts the whole table before combining hashes. This is deterministic and does not expose rows, but memory is O(rows) with Python-object overhead; on a large score table, budget RAM or replace it with a streaming/external-sort strategy.

## Verification caveat

The isolated Alembic reproduction used a temporary DB only. The migration failed as expected; Alembic left the temporary file locked through interpreter cleanup. A subsequent `Remove-Item` cleanup attempt was blocked by filesystem policy, so the one-file temp directory `C:\Users\noyaa\AppData\Local\Temp\tmpihd_yzct` remains. It contains only the synthetic `catalog.sqlite3`; no project or live DB path was involved.
