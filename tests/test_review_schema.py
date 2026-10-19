from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from chuni_penguin.database import Chart, Song
from chuni_penguin.database.base import Base
from penguin.annotation_import import import_annotations
from penguin.review_schema import install_review_schema

REVIEW_TABLES = (
    "chart_reviewer_profiles",
    "chart_player_reviews",
    "chart_player_review_history",
)
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "chuni_penguin/database/alembic/versions/2026_10_16_2100-9d3a1f7c2b60_player_chart_reviews.py"
)


def _fixture(path: Path) -> tuple[Path, int, int]:
    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            song = Song(
                id=900000001,
                title="Review schema fixture",
                artist="Fixture",
                chunithm_catcode=0,
                genre="ORIGINAL",
                version="X-VERSE-X",
                available=True,
                removed=False,
            )
            session.add(song)
            session.flush()
            chart = Chart(
                song_id=song.id,
                difficulty="MAS",
                level="14",
                const=14.0,
                available=True,
            )
            session.add(chart)
            session.commit()
            song_id = song.id
            chart_id = chart.id
    finally:
        engine.dispose()

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE chart_player_review_history")
        connection.execute("DROP TABLE chart_player_reviews")
        connection.execute("DROP TABLE chart_reviewer_profiles")
        connection.execute("DROP TABLE chart_annotation_tags")
        connection.execute("DROP TABLE chart_annotations")
        connection.execute(
            "CREATE TABLE player_sentinel (id INTEGER PRIMARY KEY, value BLOB NOT NULL)"
        )
        connection.execute("INSERT INTO player_sentinel VALUES (1, ?)", (b"keep",))
        connection.commit()
    return path, song_id, chart_id


def _state(path: Path) -> tuple:
    with closing(sqlite3.connect(path)) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        schema = tuple(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
        )
        data = [
            (
                    table,
                    tuple(
                        connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
                        .fetchall()
                    ),
            )
            for table in tables
        ]
        return tuple(tables), schema, tuple(data)


def _non_review_state(state: tuple) -> tuple:
    tables, schema, data = state
    retained = set(tables).difference(REVIEW_TABLES)
    return (
        tuple(table for table in tables if table in retained),
        tuple(row for row in schema if row[2] in retained),
        tuple(row for row in data if row[0] in retained),
    )


def _seed_review_history(path: Path, chart_id: int) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO chart_reviewer_profiles VALUES (?, ?, ?, ?, ?, ?, ?)",
            (123, "16.00", "14", "experienced", "[]", "[]", 1_700_000_000.0),
        )
        connection.execute(
            "INSERT INTO chart_player_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                chart_id,
                123,
                5,
                1,
                "played",
                "improve",
                "private note",
                "{}",
                1,
                1_700_000_000.0,
                1_700_000_000.0,
            ),
        )
        connection.execute(
            "INSERT INTO chart_player_review_history VALUES (?, ?, ?, ?, ?)",
            (1, 1, 1, "{}", 1_700_000_000.0),
        )
        connection.commit()


def _annotation_source(chart_id: int, song_id: int) -> dict:
    return {
        "namespace": "poopenguin_manual",
        "revision": "a" * 64,
        "workbook_sha256": "b" * 64,
        "provenance": {"reviewer": "offline fixture"},
        "rows": [
            {
                "source_row": 0,
                "sheet": "14",
                "excel_row": 7,
                "chart_id": chart_id,
                "song_id": song_id,
                "difficulty": "MAS",
                "title": "Review schema fixture",
                "genre": "ORIGINAL",
                "source_const": 14.0,
                "source_notes": 100,
                "sss_stars": 3,
                "note": "preserve annotation",
                "tags": ["fixture"],
                "flags": [],
                "links": [],
                "raw_cells": {},
                "match_method": "agent_reviewed",
                "review_json": {"agent": "offline fixture"},
            }
        ],
    }


def _migration_module():
    spec = importlib.util.spec_from_file_location("review_schema_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_migration(path: Path, function: str) -> None:
    module = _migration_module()
    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            module.op = Operations(context)
            getattr(module, function)()
    finally:
        engine.dispose()


def test_readonly_dry_run_leaves_database_bytes_and_schema_unchanged(tmp_path):
    path, _, _ = _fixture(tmp_path / "reviews.sqlite3")
    before = _state(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    report = install_review_schema(path)

    assert report == {
        "status": "install_required",
        "schema_required": True,
        "tables": list(REVIEW_TABLES),
    }
    assert _state(path) == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_apply_installs_only_review_schema_and_repeat_preserves_review_rows(tmp_path):
    path, _, chart_id = _fixture(tmp_path / "reviews.sqlite3")
    before = _state(path)

    first = install_review_schema(path, apply=True)

    assert first["status"] == "installed"
    installed = _state(path)
    assert set(installed[0]).difference(before[0]) == set(REVIEW_TABLES)
    assert _non_review_state(installed) == _non_review_state(before)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    _seed_review_history(path, chart_id)
    before_repeat = _state(path)
    second = install_review_schema(path, apply=True)

    assert second["status"] == "already_installed"
    assert _state(path) == before_repeat


@pytest.mark.parametrize("shape", ["partial", "mismatched", "name_conflict"])
def test_partial_or_mismatched_schema_is_rejected_without_changes(tmp_path, shape):
    path, _, _ = _fixture(tmp_path / f"{shape}.sqlite3")
    with closing(sqlite3.connect(path)) as connection:
        if shape == "partial":
            connection.execute(
                "CREATE TABLE chart_reviewer_profiles (discord_id INTEGER PRIMARY KEY)"
            )
            reason = "review_partial_schema"
        else:
            if shape == "mismatched":
                for table in REVIEW_TABLES:
                    connection.execute(f'CREATE TABLE "{table}" (wrong_column INTEGER)')
                reason = "review_schema_mismatch"
            else:
                connection.execute(
                    "CREATE VIEW chart_reviewer_profiles AS SELECT id FROM player_sentinel"
                )
                reason = "review_schema_mismatch"
        connection.commit()
    before = _state(path)

    with pytest.raises(ValueError, match=reason):
        install_review_schema(path, apply=True)

    assert _state(path) == before


def test_failed_ddl_rolls_back_all_review_tables(tmp_path, monkeypatch):
    import penguin.review_schema as module

    path, _, _ = _fixture(tmp_path / "reviews.sqlite3")
    before = _state(path)
    original = module._create_review_tables

    def fail_after_ddl(connection):
        original(connection)
        failure_message = "injected DDL failure"
        raise RuntimeError(failure_message)

    monkeypatch.setattr(module, "_create_review_tables", fail_after_ddl)
    with pytest.raises(RuntimeError, match="injected DDL failure"):
        install_review_schema(path, apply=True)

    assert _state(path) == before


def test_migration_creates_frozen_schema_and_downgrade_requires_empty_tables(tmp_path):
    path, _, chart_id = _fixture(tmp_path / "migration.sqlite3")

    _run_migration(path, "upgrade")
    assert set(_state(path)[0]).issuperset(REVIEW_TABLES)
    _seed_review_history(path, chart_id)
    with pytest.raises(RuntimeError, match="only for empty tables"):
        _run_migration(path, "downgrade")
    assert set(_state(path)[0]).issuperset(REVIEW_TABLES)

    empty_path, _, _ = _fixture(tmp_path / "empty-migration.sqlite3")
    _run_migration(empty_path, "upgrade")
    _run_migration(empty_path, "downgrade")
    assert not set(_state(empty_path)[0]).intersection(REVIEW_TABLES)


def test_importer_first_then_review_migration_preserves_both_schemas_and_data(tmp_path):
    path, song_id, chart_id = _fixture(tmp_path / "importer-first.sqlite3")

    annotation_result = import_annotations(
        path, _annotation_source(chart_id, song_id), apply=True
    )
    assert annotation_result["status"] == "imported"
    assert install_review_schema(path, apply=True)["status"] == "installed"
    _seed_review_history(path, chart_id)
    before = _state(path)

    _run_migration(path, "upgrade")

    assert _state(path) == before
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM chart_annotations").fetchone() == (
            1,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM chart_player_review_history"
        ).fetchone() == (1,)
