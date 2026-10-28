import copy
import hashlib
import importlib.util
import sqlite3
from contextlib import closing, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, selectinload

from chuni_penguin.database import Chart, Song
from chuni_penguin.database.base import Base
from chuni_penguin.repositories.chart_annotations import ChartAnnotationRepository
from chuni_penguin.services.phase1_charts import Phase1Charts
from penguin.annotation_import import import_annotations, rollback_annotations


@contextmanager
def _db(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                Song(
                    id=900000001,
                    title="Offline fixture",
                    artist="Fixture",
                    chunithm_catcode=0,
                    genre="ORIGINAL",
                    version="X-VERSE-X",
                    available=True,
                    removed=False,
                )
            )
            session.flush()
            session.add(
                Chart(
                    song_id=900000001,
                    difficulty="MAS",
                    level="14",
                    const=14.0,
                    available=True,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    with _db(path) as connection:
        connection.execute("DROP TABLE chart_annotation_tags")
        connection.execute("DROP TABLE chart_annotations")
        chart_id, song_id, difficulty = connection.execute(
            "SELECT id,song_id,difficulty FROM chunirec_charts WHERE difficulty='MAS'"
        ).fetchone()
        connection.execute(
            "CREATE TABLE player_sentinel (id INTEGER PRIMARY KEY, value BLOB)"
        )
        connection.execute(
            "INSERT INTO player_sentinel VALUES (1,?)", (b"private fixture\x00",)
        )
    source = {
        "namespace": "poopenguin_manual",
        "revision": "a" * 64,
        "workbook_sha256": "b" * 64,
        "provenance": {"reviewer": "Luna, agent review"},
        "rows": [
            {
                "source_row": 0,
                "sheet": "14",
                "excel_row": 7,
                "chart_id": chart_id,
                "song_id": song_id,
                "difficulty": difficulty,
                "title": "Original source name",
                "genre": None,
                "source_const": 13.9,
                "source_notes": 0,
                "sss_stars": 3,
                "note": "Preserved source note",
                "tags": ["Trill", "ＴＲＩＬＬ", "100%"],
                "flags": ["source flag"],
                "links": [
                    {"cell": "D7", "label": "Video", "url": "https://example.com/chart"}
                ],
                "raw_cells": {"J7": "extra value"},
                "match_method": "agent_reviewed",
                "review_json": {"agent": "Luna"},
            }
        ],
    }
    return path, source


def _state(path):
    with _db(path) as connection:
        return {
            "snapshots": connection.execute(
                "SELECT * FROM source_snapshots ORDER BY id"
            ).fetchall(),
            "player": connection.execute("SELECT * FROM player_sentinel").fetchall(),
            "catalog": connection.execute(
                "SELECT id,song_id,difficulty,const FROM chunirec_charts ORDER BY id"
            ).fetchall(),
            "schema": connection.execute(
                "SELECT name,sql FROM sqlite_master ORDER BY name"
            ).fetchall(),
        }


def test_read_only_dry_run_and_transactional_install(catalog):
    path, source = catalog
    before = _state(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    report = import_annotations(path, source)
    assert report["schema_required"] and report["inserted"] == 1
    assert _state(path) == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    result = import_annotations(path, source, apply=True)
    assert result["status"] == "imported" and result["tags"] == 2
    after = _state(path)
    assert before["player"] == after["player"] and before["catalog"] == after["catalog"]
    assert len(after["snapshots"]) == 1
    assert import_annotations(path, source, apply=True)["status"] == "unchanged"
    assert _state(path) == after
    with _db(path) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda source: source["rows"][0].update(song_id=1), "identity_mismatch"),
        (
            lambda source: source["rows"][0].update(difficulty="ULT"),
            "identity_mismatch",
        ),
        (
            lambda source: source["rows"].append(copy.deepcopy(source["rows"][0])),
            "duplicate_source_row",
        ),
        (lambda source: source["rows"][0].update(sss_stars=8), "stars_invalid"),
    ],
)
def test_invalid_input_changes_nothing(catalog, mutation, reason):
    path, source = catalog
    mutation(source)
    before = _state(path)
    with pytest.raises(ValueError, match=reason):
        import_annotations(path, source, apply=True)
    assert _state(path) == before


def test_failed_schema_install_is_rolled_back(catalog, monkeypatch):
    import penguin.annotation_import as module

    path, source = catalog
    original = module._ddl

    def fail(connection):
        original(connection)
        raise RuntimeError("injected failure after DDL")

    monkeypatch.setattr(module, "_ddl", fail)
    before = _state(path)
    with pytest.raises(RuntimeError, match="injected"):
        import_annotations(path, source, apply=True)
    assert _state(path) == before


def test_batch_switch_rollback_reapply_and_history_conflict(catalog):
    path, first = catalog
    first_id = import_annotations(path, first, apply=True)["batch_id"]
    second = copy.deepcopy(first)
    second["revision"] = "c" * 64
    second["rows"][0]["note"] = "new note"
    second_id = import_annotations(path, second, apply=True)["batch_id"]
    assert rollback_annotations(path, second_id)["restored_batches"] == [first_id]
    with _db(path) as connection:
        assert connection.execute(
            "SELECT DISTINCT source_snapshot_id FROM chart_annotations WHERE active=1"
        ).fetchall() == [(second_id,)]
        connection.execute("UPDATE player_sentinel SET value=?", (b"new player data",))
    rollback_annotations(path, second_id, apply=True)
    assert import_annotations(path, first)["status"] == "unchanged"
    assert import_annotations(path, second, apply=True)["status"] == "reactivated"
    rollback_annotations(path, second_id, apply=True)
    rollback_annotations(path, first_id, apply=True)
    with pytest.raises(ValueError, match="historical_reactivation_conflict"):
        import_annotations(path, second, apply=True)
    with _db(path) as connection:
        assert (
            connection.execute("SELECT value FROM player_sentinel").fetchone()[0]
            == b"new player data"
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM chart_annotations").fetchone()[0]
            == 2
        )
    assert import_annotations(path, first, apply=True)["status"] == "reactivated"


def test_same_revision_rejects_changed_content(catalog):
    path, source = catalog
    import_annotations(path, source, apply=True)
    source["rows"][0]["note"] = "silently changed"
    before = _state(path)
    with pytest.raises(ValueError, match="existing_batch_mismatch"):
        import_annotations(path, source, apply=True)
    assert _state(path) == before


def test_failure_during_insert_keeps_previous_active(catalog):
    path, source = catalog
    first = import_annotations(path, source, apply=True)["batch_id"]
    with _db(path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_tag BEFORE INSERT ON chart_annotation_tags BEGIN SELECT RAISE(ABORT,'test failure'); END"
        )
    source["revision"] = "d" * 64
    before = _state(path)
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        import_annotations(path, source, apply=True)
    assert _state(path) == before
    with _db(path) as connection:
        assert connection.execute(
            "SELECT DISTINCT source_snapshot_id FROM chart_annotations WHERE active=1"
        ).fetchall() == [(first,)]


@pytest.mark.asyncio
async def test_shared_reader_identity_active_version_and_literal_tag_search(catalog):
    path, source = catalog
    batch = import_annotations(path, source, apply=True)["batch_id"]
    engine = create_async_engine("sqlite+aiosqlite:///" + path.as_posix())
    try:
        reader = ChartAnnotationRepository(async_sessionmaker(engine))
        service = Phase1Charts(annotations=reader)
        original = source["rows"][0]
        chart = SimpleNamespace(
            id=original["chart_id"], song_id=original["song_id"], difficulty="MAS"
        )
        rows = await service.annotations_for(chart)
        assert (
            rows[0]["note"] == "Preserved source note" and rows[0]["source_notes"] == 0
        )
        assert len(rows[0]["links"]) == 1
        assert (await service.tag_matches("ｔｒｉｌｌ"))[0][0] == 0
        assert await service.tagged_chart_ids("100%") == {chart.id}
        assert await service.tagged_chart_ids("100_") == set()
        assert await service.tag_matches("") == []
        chart.difficulty = "ULT"
        assert await service.annotations_for(chart) == []
        rollback_annotations(path, batch, apply=True)
        assert await service.tag_matches("trill") == []
    finally:
        await engine.dispose()


def test_default_service_does_not_load_archived_manual_json():
    service = Phase1Charts()
    assert service.manual == {"charts": []} and service.bindings == []


@pytest.mark.parametrize("migrate_first", [True, False])
def test_alembic_accepts_importer_schema_and_preserves_history(catalog, migrate_first):
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path, source = catalog
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "chuni_penguin/database/alembic/versions/2026_10_16_1200-4c6f8b1a0d92_community_annotations.py"
    )
    spec = importlib.util.spec_from_file_location(
        "annotation_migration_test", migration_path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        if migrate_first:
            with (
                engine.begin() as connection,
                Operations.context(MigrationContext.configure(connection)),
            ):
                migration.upgrade()  # Fresh, versioned migration path.
        import_annotations(path, source, apply=True)
        before = _state(path)
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            migration.upgrade()  # Later Alembic reconciliation must accept an exact importer schema.
            with pytest.raises(RuntimeError, match="Preserve annotation history"):
                migration.downgrade()
        assert _state(path) == before
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_song_detail_renders_actual_database_annotation(catalog):
    from chuni_penguin.services.song_details import SongDetailsData
    from chuni_penguin.ui.song_details import SongDetailsState, SongDetailsView

    path, source = catalog
    batch = import_annotations(path, source, apply=True)["batch_id"]
    engine = create_async_engine("sqlite+aiosqlite:///" + path.as_posix())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    view = data = None
    try:
        async with sessions() as session:
            song = await session.scalar(
                select(Song).options(
                    selectinload(Song.charts).selectinload(Chart.sdvxin_chart_view)
                )
            )
            for chart in song.charts:
                chart.song = song
        service = Phase1Charts(annotations=ChartAnnotationRepository(sessions))
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=123),
            bot=SimpleNamespace(
                is_owner=AsyncMock(return_value=False), phase1_charts=service
            ),
        )
        data = SongDetailsData(ctx)
        data._cache[("charts", song.id)] = tuple(song.charts)
        view = SongDetailsView(ctx, [song], data=data)
        rendered = await view.render(SongDetailsState(tab="chart"))
        fields = [
            field for page in rendered.pages for embed in page for field in embed.fields
        ]
        annotation = next(
            field for field in fields if field.name == "Community chart data"
        )
        assert "Preserved source note" in annotation.value
        assert (
            "★★★" in annotation.value
            and "https://example.com/chart" in annotation.value
        )
        assert "Luna reviewed" in annotation.value and "已審核" not in annotation.name
        rollback_annotations(path, batch, apply=True)
        empty = await view.render(SongDetailsState(tab="chart"))
        assert not any(
            field.name == "社群譜面資料"
            for page in empty.pages
            for embed in page
            for field in embed.fields
        )
    finally:
        if view:
            view.stop()
        if data:
            await data.close()
        await engine.dispose()
