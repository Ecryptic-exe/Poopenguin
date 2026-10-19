"""Back up, rehearse or apply only review tables; emit no private row values."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from chuni_penguin.database.chart_reviews import REVIEW_TABLES
from penguin.annotation_backup import backup_database, database_fingerprint
from penguin.review_schema import install_review_schema

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data/penguin.sqlite3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Stop the bot before using this option"
    )
    args = parser.parse_args()
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    folder = ROOT / "data/backups" / f"reviews-{stamp}"
    backup = folder / "before.sqlite3"
    backup_database(LIVE, backup)
    if args.live:
        target = LIVE
    else:
        target = folder / "rehearsal.sqlite3"
        backup_database(backup, target)
    before_all = database_fingerprint(target)
    before = database_fingerprint(target, exclude=REVIEW_TABLES)
    dry_run = install_review_schema(target)
    assert database_fingerprint(target) == before_all, "Dry run changed data"
    applied = install_review_schema(target, apply=True)
    assert database_fingerprint(target, exclude=REVIEW_TABLES) == before, (
        "Existing tables changed"
    )
    after = database_fingerprint(target)
    repeated = install_review_schema(target, apply=True)
    assert repeated["status"] == "already_installed"
    assert database_fingerprint(target) == after, "Repeat changed data"
    with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        songs = connection.execute("SELECT COUNT(*) FROM chunirec_songs").fetchone()[0]
        charts = connection.execute("SELECT COUNT(*) FROM chunirec_charts").fetchone()[
            0
        ]
        annotations = connection.execute(
            "SELECT COUNT(*) FROM chart_annotations WHERE active=1"
        ).fetchone()[0]
        reviews = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in REVIEW_TABLES
        }
    assert integrity == "ok" and foreign_keys == 0
    assert set(after["tables"]) == set(before_all["tables"]) | set(REVIEW_TABLES)
    result = {
        "complete": True,
        "checked_at": datetime.now().astimezone().isoformat(),
        "mode": "live" if args.live else "rehearsal",
        "backup": str(backup.relative_to(ROOT)),
        "dry_run": dry_run,
        "applied": applied,
        "repeat": repeated,
        "unchanged_existing_table_count": len(before["tables"]),
        "existing_table_fingerprints_match": True,
        "database_integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "songs": songs,
        "charts": charts,
        "active_annotations": annotations,
        "review_rows": reviews,
        "alembic_history_replayed": False,
    }
    destination = ROOT / "reports" / f"review-migration-{result['mode']}-20261016.json"
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
