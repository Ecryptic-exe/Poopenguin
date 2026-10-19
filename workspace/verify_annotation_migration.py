"""Rehearse/perform the authorized import; export no player records or credentials."""

import argparse
import asyncio
import copy
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chuni_penguin.database.annotations import ANNOTATION_TABLES
from chuni_penguin.repositories.chart_annotations import ChartAnnotationRepository
from penguin.annotation_backup import backup_database, database_fingerprint
from penguin.annotation_import import import_annotations, rollback_annotations
from penguin.annotation_source import load_source

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data/penguin.sqlite3"


def source_data():
    return load_source(
        ROOT / "outputs/chart-mapping-20261015/luna/ChunithmChart_reviewed.xlsx",
        ROOT / "outputs/chart-mapping-20261015/bm25-matches.json",
        ROOT / "outputs/chart-mapping-20261015/luna/review.json",
        ROOT / "snapshots/manual.json",
    )


def original_state(database, imported_snapshot):
    fingerprint = database_fingerprint(
        database, exclude=(*ANNOTATION_TABLES, "source_snapshots")
    )
    with closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        snapshots = connection.execute(
            "SELECT * FROM source_snapshots WHERE id != ? ORDER BY id",
            (imported_snapshot,),
        ).fetchall()
    return fingerprint, snapshots


async def verify_reader(database, source):
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=database.as_uri(),
            query={"mode": "ro", "uri": "true"},
        ),
        hide_parameters=True,
    )
    try:
        reader = ChartAnnotationRepository(async_sessionmaker(engine))
        tagged = next(row for row in source["rows"] if row["tags"])
        for sample in [
            source["rows"][0],
            tagged,
            next(row for row in source["rows"] if row["note"] and not row["tags"]),
            next(
                row for row in source["rows"] if row["match_method"] == "agent_reviewed"
            ),
        ]:
            rows = await reader.for_chart(
                SimpleNamespace(
                    id=sample["chart_id"],
                    song_id=sample["song_id"],
                    difficulty=sample["difficulty"],
                )
            )
            assert len(rows) == 1
            actual = rows[0]
            for key in (
                "note",
                "source_const",
                "source_notes",
                "sss_stars",
                "links",
                "match_method",
            ):
                assert actual[key] == sample[key], key
        tag = tagged["tags"][0]
        assert tagged["chart_id"] in await reader.chart_ids(tag)
        assert any(
            row["chart_id"] == tagged["chart_id"] for row in await reader.search(tag)
        )
        return {
            "sample_chart_annotations": 4,
            "tag_browsing": True,
            "tag_filter": True,
            "readonly_pool": True,
        }
    finally:
        await engine.dispose()


def validate_database(database, source):
    with closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        active = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT chart_id) FROM chart_annotations WHERE active=1"
        ).fetchone()
        assert active == (
            len(source["rows"]),
            len({r["chart_id"] for r in source["rows"]}),
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chart_annotations a JOIN chunirec_charts c ON c.id=a.chart_id WHERE a.active=1 AND (a.song_id!=c.song_id OR a.difficulty!=c.difficulty)"
            ).fetchone()[0]
            == 0
        )
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("chunirec_songs", "chunirec_charts")
        }
    return {
        "integrity": "ok",
        "foreign_key_errors": 0,
        "active_annotations": active[0],
        "unique_charts": active[1],
        "public_catalog": counts,
    }


def migrate(*, live=False):
    source = source_data()
    snapshot = "annotation:" + source["revision"]
    if live:
        rehearsal = json.loads(
            (ROOT / "reports/annotation-migration-rehearsal-20261016.json").read_text(
                encoding="utf-8"
            )
        )
        assert rehearsal["complete"] and rehearsal["rollback_reapply_verified"]
        assert rehearsal["import"]["batch_id"] == snapshot, (
            "Rehearsal does not cover this source revision"
        )
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    directory = ROOT / "data/backups" / ("annotations-" + stamp)
    backup = directory / "before.sqlite3"
    backup_result = backup_database(LIVE, backup)
    database = LIVE if live else directory / "rehearsal.sqlite3"
    if not live:
        backup_database(backup, database)
    original = original_state(database, snapshot)
    dry_state = database_fingerprint(database)
    dry = import_annotations(database, source)
    assert database_fingerprint(database) == dry_state
    applied = import_annotations(database, source, apply=True)
    assert original_state(database, snapshot) == original, (
        "Existing catalog/player/source rows changed"
    )
    imported_state = database_fingerprint(database)
    repeat = import_annotations(database, source, apply=True)
    assert repeat["status"] == "unchanged"
    assert database_fingerprint(database) == imported_state
    rollback = None
    if not live:
        altered = copy.deepcopy(source)
        altered["rows"][0]["song_id"] = -1
        try:
            import_annotations(database, altered, apply=True)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid identity was accepted")
        assert database_fingerprint(database) == imported_state
        rollback = rollback_annotations(database, applied["batch_id"], apply=True)
        assert original_state(database, snapshot) == original
        with closing(sqlite3.connect(database)) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM chart_annotations WHERE active=1"
                ).fetchone()[0]
                == 0
            )
        assert (
            import_annotations(database, source, apply=True)["status"] == "reactivated"
        )
        assert database_fingerprint(database) == imported_state
    report = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "mode": "live" if live else "rehearsal",
        "complete": True,
        "backup": {"relative_path": str(backup.relative_to(ROOT)), **backup_result},
        "dry_run": dry,
        "import": applied,
        "same_revision_rerun": repeat["status"],
        "existing_tables_unchanged": True,
        "existing_source_snapshots_unchanged": True,
        "original_tables_verified": len(original[0]["tables"]),
        "rollback_reapply_verified": rollback is not None,
        "database": validate_database(database, source),
        "reader": asyncio.run(verify_reader(database, source)),
        "excluded_unmapped_rows": [
            {key: row.get(key) for key in ("sheet", "excel_row", "title", "reason")}
            for row in source.get("provenance", {}).get("excluded_unmapped_rows", [])
        ],
        "new_required_env_settings": [],
        "remote_commands_modified": False,
    }
    destination = (
        ROOT
        / "reports"
        / (
            "annotation-migration-live-20261016.json"
            if live
            else "annotation-migration-rehearsal-20261016.json"
        )
    )
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Apply only after rehearsal passed and the workspace bot is stopped",
    )
    arguments = parser.parse_args()
    migrate(live=arguments.live)
