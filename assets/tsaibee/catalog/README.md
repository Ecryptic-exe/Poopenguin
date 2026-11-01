# TSAIBEE catalog source files

The catalog updater reads this directory by default, independently of the current
working directory. These are public song data, separate from player records and
the public player-distribution samples in the parent directory.

Included files, supplied on 2026-10-30:

- `idx.json`: song IDs, titles and chart metadata.
- `mate.json`: Mate chart constants.
- `version.json`: source revision `20261022v3`.

Original sources:

- <https://chuni.tsaibee.org/data/idx.json>
- <https://chuni.tsaibee.org/data/song-const/mate.json>
- <https://chuni.tsaibee.org/data/version.json>

From the project root, preview an update without modifying the database:

```powershell
& ".\.venv314-fresh\Scripts\python.exe" -X utf8 -m dbutils update chunilib --report data/catalog-preview.json
```

Apply an update after reviewing the report:

```powershell
& ".\.venv314-fresh\Scripts\python.exe" -X utf8 -m dbutils update chunilib --apply --report data/catalog-applied.json
```

The apply operation creates a consistent SQLite backup in `data/catalog-backups/`
before updating. This backup contains private player/session data; keep it local.
Missing files or invalid data stop the operation. `--catalog-dir` can override this
directory. To use `--game-version crossversex`, also provide `crossversex.json`.

Replace all three JSON files together when downloading a later source revision.
They are not downloaded automatically. Updating the SQLite catalog does not load
newly replaced JSON files until the updater is run. Restart the bot after an update
to refresh its in-memory catalog.

The updater preserves song/chart IDs, existing notes and player data. Missing
canonical metadata is reported and skipped. The initial import found 10 songs and
one ULT chart absent from local seed metadata; they were not created by guessing.

These files do not configure the separate `[providers]` query service in `bot.ini`.

The 2026-10-30 follow-up adds song 3069 (狂命) and its four charts from the
user-supplied upstream seeds. Its real NET jacket is `5f167154120c68ed.jpg`; the
index currently has a placeholder. Nine other songs and one ULT chart still need
canonical metadata. Existing catalog rows and player data are preserved.
