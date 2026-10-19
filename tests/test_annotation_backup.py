import hashlib
import sqlite3

import pytest

from penguin.annotation_backup import backup_database, database_fingerprint


def _create_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE players (
            id INTEGER PRIMARY KEY,
            player_name TEXT NOT NULL
        );
        CREATE TABLE source_snapshots (
            id INTEGER PRIMARY KEY,
            payload BLOB NOT NULL
        );
        INSERT INTO players VALUES (1, 'synthetic player');
        INSERT INTO source_snapshots VALUES (1, X'00FF1080');
        """
    )
    connection.commit()
    return connection


def test_backup_includes_committed_wal_content_and_verifies_output(tmp_path):
    source = tmp_path / "source.sqlite3"
    connection = _create_database(source)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute("INSERT INTO players VALUES (2, 'wal player')")
    connection.commit()

    wal_path = source.with_name(source.name + "-wal")
    assert wal_path.exists() and wal_path.stat().st_size > 32

    destination = tmp_path / "nested" / "backup.sqlite3"
    summary = backup_database(source, destination)

    assert summary["integrity"] == "ok"
    assert summary["foreign_key_errors"] == 0
    assert summary["bytes"] == destination.stat().st_size
    assert summary["sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert set(summary) == {"integrity", "foreign_key_errors", "sha256", "bytes"}
    assert "player" not in str(summary).casefold()
    copied = sqlite3.connect(destination)
    try:
        assert copied.execute("SELECT player_name FROM players ORDER BY id").fetchall() == [
            ("synthetic player",),
            ("wal player",),
        ]
    finally:
        copied.close()
        connection.close()


def test_existing_destination_is_rejected_without_changing_its_bytes(tmp_path):
    source = tmp_path / "source.sqlite3"
    connection = _create_database(source)
    connection.close()
    destination = tmp_path / "existing.sqlite3"
    destination.write_bytes(b"keep this existing destination")
    original = destination.read_bytes()

    with pytest.raises(ValueError, match=r"^DESTINATION_EXISTS$"):
        backup_database(source, destination)

    assert destination.read_bytes() == original


def test_same_source_and_destination_is_rejected(tmp_path):
    source = tmp_path / "same.sqlite3"
    connection = _create_database(source)
    connection.close()

    with pytest.raises(ValueError, match=r"^SOURCE_EQUALS_DESTINATION$"):
        backup_database(source, source)


@pytest.mark.parametrize(
    ("source_kind", "reason"),
    [
        ("missing", r"^SOURCE_NOT_FOUND$"),
        ("directory", r"^SOURCE_NOT_FILE$"),
    ],
)
def test_missing_or_nonfile_source_is_rejected(tmp_path, source_kind, reason):
    source = tmp_path / "source"
    if source_kind == "directory":
        source.mkdir()
    destination = tmp_path / "backup.sqlite3"

    with pytest.raises(ValueError, match=reason):
        backup_database(source, destination)

    assert not destination.exists()


def test_failed_foreign_key_validation_removes_only_new_destination(tmp_path):
    source = tmp_path / "invalid.sqlite3"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (
            parent_id INTEGER REFERENCES parent(id)
        );
        INSERT INTO child VALUES (99);
        """
    )
    connection.commit()
    connection.close()
    destination = tmp_path / "failed" / "backup.sqlite3"

    with pytest.raises(RuntimeError, match=r"^BACKUP_FOREIGN_KEY_FAILED$"):
        backup_database(source, destination)

    assert not destination.exists()
    check = sqlite3.connect(source)
    try:
        assert len(check.execute("PRAGMA foreign_key_check").fetchall()) == 1
    finally:
        check.close()


def test_fingerprint_detects_changes_and_excludes_named_table(tmp_path):
    source = tmp_path / "fingerprint.sqlite3"
    connection = _create_database(source)
    before = database_fingerprint(source)
    assert before["tables"]["players"]["row_count"] == 1
    assert before["tables"]["source_snapshots"]["row_count"] == 1

    connection.execute(
        "UPDATE players SET player_name = ? WHERE id = ?",
        ("changed synthetic player", 1),
    )
    connection.commit()
    after = database_fingerprint(source)
    assert after["tables"]["players"]["sha256"] != before["tables"]["players"][
        "sha256"
    ]
    assert "changed synthetic player" not in str(after)

    without_snapshots = database_fingerprint(source, exclude=("source_snapshots",))
    assert "source_snapshots" not in without_snapshots["tables"]
    assert without_snapshots["tables"]["players"] == after["tables"]["players"]
    connection.close()


def test_fingerprint_supports_blobs_and_quoted_table_names(tmp_path):
    source = tmp_path / "blob.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute('CREATE TABLE "blob""archive" (payload BLOB NOT NULL)')
    connection.execute('CREATE TABLE "ordinary" (value TEXT)')
    connection.execute('INSERT INTO "blob""archive" VALUES (?)', (b"\x00\xff\x10",))
    connection.execute('INSERT INTO "ordinary" VALUES (?)', ("private value",))
    connection.commit()

    before = database_fingerprint(source)
    assert before["tables"]['blob"archive']["row_count"] == 1
    assert "private value" not in str(before)

    connection.execute(
        'UPDATE "blob""archive" SET payload = ?', (b"\x00\xff\x11",)
    )
    connection.commit()
    after = database_fingerprint(source)
    assert after["tables"]['blob"archive']["sha256"] != before["tables"][
        'blob"archive'
    ]["sha256"]

    connection.close()
