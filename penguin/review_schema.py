"""Safe installation for the private player chart review schema."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from chuni_penguin.database.chart_reviews import (
    REVIEW_TABLES,
    ChartPlayerReview,
    ChartPlayerReviewHistory,
    ChartReviewerProfile,
)

_REVIEW_MODELS = (ChartReviewerProfile, ChartPlayerReview, ChartPlayerReviewHistory)
_CHECK_PATTERN = re.compile(r"\bCHECK\s*\(([^()]*)\)", re.IGNORECASE)


def _fail(reason: str) -> None:
    raise ValueError(reason)


def _connect(path: Path, *, apply: bool) -> sqlite3.Connection:
    path = path.resolve(strict=True)
    connection = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if apply else "?mode=ro"),
        uri=True,
        isolation_level=None,
        timeout=30,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if not apply:
            connection.execute("PRAGMA query_only=ON")
    except BaseException:
        connection.close()
        raise
    return connection


def _create_review_tables(connection: sqlite3.Connection) -> None:
    for model in _REVIEW_MODELS:
        table = model.__table__
        connection.execute(str(CreateTable(table).compile(dialect=dialect())))
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            connection.execute(str(CreateIndex(index).compile(dialect=dialect())))


def _schema_signature(connection: sqlite3.Connection, table: str) -> tuple:
    columns = [
        tuple(row)[1:6] for row in connection.execute(f'PRAGMA table_info("{table}")')
    ]
    foreign_keys = sorted(
        tuple(row)[1:]
        for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')
    )

    indices = []
    for row in connection.execute(f'PRAGMA index_list("{table}")'):
        name = row[1].replace('"', '""')
        columns_in_index = tuple(
            (index_row[1], index_row[2], index_row[3], index_row[4])
            for index_row in connection.execute(f'PRAGMA index_xinfo("{name}")')
            if index_row[5]
        )
        indices.append((row[1], row[2], row[3], row[4], columns_in_index))

    create_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    checks = tuple(
        sorted(
            " ".join(check.split()).casefold()
            for check in _CHECK_PATTERN.findall(create_sql)
        )
    )
    table_options = (
        bool(re.search(r"\bWITHOUT\s+ROWID\b", create_sql, re.IGNORECASE)),
        bool(re.search(r"\bSTRICT\s*$", create_sql, re.IGNORECASE)),
    )
    triggers = tuple(
        sorted(
            (row[0], row[1])
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                (table,),
            )
        )
    )
    return columns, foreign_keys, sorted(indices), checks, table_options, triggers


def _expected_signatures() -> dict[str, tuple]:
    with closing(sqlite3.connect(":memory:")) as reference:
        _create_review_tables(reference)
        return {table: _schema_signature(reference, table) for table in REVIEW_TABLES}


def _validate_catalog(connection: sqlite3.Connection) -> None:
    catalog = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "chunirec_charts" not in catalog:
        _fail("review_catalog_schema_missing")
    columns = connection.execute('PRAGMA table_info("chunirec_charts")').fetchall()
    primary_key_id = any(row[1] == "id" and row[5] == 1 for row in columns)
    if not primary_key_id:
        _fail("review_catalog_schema_missing")


def _review_schema(connection: sqlite3.Connection, *, install: bool) -> bool:
    placeholders = ",".join("?" for _ in REVIEW_TABLES)
    objects = {
        row[1]: row[0]
        for row in connection.execute(
            f"SELECT type,name FROM sqlite_master WHERE name IN ({placeholders})",
            REVIEW_TABLES,
        )
    }
    if any(object_type != "table" for object_type in objects.values()):
        _fail("review_schema_mismatch")
    found = set(objects)
    if found and found != set(REVIEW_TABLES):
        _fail("review_partial_schema")

    expected = _expected_signatures()
    if found:
        for table in REVIEW_TABLES:
            if _schema_signature(connection, table) != expected[table]:
                _fail("review_schema_mismatch")
        return False

    if install:
        _create_review_tables(connection)
        for table in REVIEW_TABLES:
            if _schema_signature(connection, table) != expected[table]:
                _fail("review_schema_mismatch")
    return True


def _validate_integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        _fail("review_foreign_key_check_failed")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        _fail("review_integrity_check_failed")


def install_review_schema(database: Path, *, apply: bool = False) -> dict:
    """Inspect or install only the review tables in an existing SQLite database."""
    path = Path(database)
    with closing(_connect(path, apply=apply)) as connection:
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        try:
            _validate_catalog(connection)
            schema_required = _review_schema(connection, install=apply)
            _validate_integrity(connection)
            if apply:
                connection.commit()
            else:
                connection.rollback()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    if schema_required:
        status = "installed" if apply else "install_required"
    else:
        status = "already_installed"
    return {
        "status": status,
        "schema_required": schema_required,
        "tables": list(REVIEW_TABLES),
    }
