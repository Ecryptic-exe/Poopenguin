"""Read-only SQLite backup and content fingerprint helpers."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import struct
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

__all__ = ["backup_database", "database_fingerprint"]

_INVALID_PATH = "INVALID_PATH"
_SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
_SOURCE_NOT_FILE = "SOURCE_NOT_FILE"
_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
_SOURCE_EQUALS_DESTINATION = "SOURCE_EQUALS_DESTINATION"
_DESTINATION_EXISTS = "DESTINATION_EXISTS"
_DESTINATION_SIDECAR_EXISTS = "DESTINATION_SIDECAR_EXISTS"
_DESTINATION_DIRECTORY_FAILED = "DESTINATION_DIRECTORY_FAILED"
_DESTINATION_CREATE_FAILED = "DESTINATION_CREATE_FAILED"
_BACKUP_FAILED = "BACKUP_FAILED"
_BACKUP_INTEGRITY_FAILED = "BACKUP_INTEGRITY_FAILED"
_BACKUP_FOREIGN_KEY_FAILED = "BACKUP_FOREIGN_KEY_FAILED"
_BACKUP_CLOSE_FAILED = "BACKUP_CLOSE_FAILED"
_SOURCE_CLOSE_FAILED = "SOURCE_CLOSE_FAILED"
_DESTINATION_CHANGED = "DESTINATION_CHANGED"
_BACKUP_READ_FAILED = "BACKUP_READ_FAILED"
_DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
_DATABASE_NOT_FILE = "DATABASE_NOT_FILE"
_DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
_INVALID_EXCLUDE = "INVALID_EXCLUDE"
_FINGERPRINT_FAILED = "FINGERPRINT_FAILED"


def _as_path(value: Path) -> Path:
    try:
        return Path(value)
    except (TypeError, ValueError):
        raise ValueError(_INVALID_PATH) from None


def _readonly_connection(database: Path) -> sqlite3.Connection:
    uri = f"{database.resolve(strict=True).as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _path_identity(path: Path) -> tuple[int, int] | None:
    try:
        file_stat = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        return None
    return file_stat.st_dev, file_stat.st_ino


def _remove_owned_target(path: Path, identity: tuple[int, int]) -> None:
    try:
        file_stat = path.lstat()
        if (
            stat.S_ISREG(file_stat.st_mode)
            and (file_stat.st_dev, file_stat.st_ino) == identity
        ):
            path.unlink()
    except OSError:
        pass


def backup_database(source: Path, destination: Path) -> dict:
    """Create and validate a consistent, new SQLite backup.

    The destination is claimed with exclusive creation so an existing file is
    never replaced. The source is opened using SQLite's read-only URI mode.
    """
    source_path = _as_path(source)
    destination_path = _as_path(destination)

    try:
        source_exists = source_path.exists()
        source_is_file = source_path.is_file()
    except (OSError, ValueError):
        raise RuntimeError(_SOURCE_UNAVAILABLE) from None

    if not source_exists:
        raise ValueError(_SOURCE_NOT_FOUND)
    if not source_is_file:
        raise ValueError(_SOURCE_NOT_FILE)

    try:
        source_resolved = source_path.resolve(strict=True)
        destination_resolved = destination_path.resolve(strict=False)
    except (OSError, ValueError):
        raise RuntimeError(_SOURCE_UNAVAILABLE) from None

    if source_resolved == destination_resolved:
        raise ValueError(_SOURCE_EQUALS_DESTINATION)
    try:
        destination_exists = destination_path.exists() or destination_path.is_symlink()
        destination_has_sidecar = any(
            sidecar.exists() or sidecar.is_symlink()
            for suffix in ("-wal", "-shm", "-journal")
            for sidecar in (Path(f"{destination_path}{suffix}"),)
        )
    except (OSError, ValueError):
        raise RuntimeError(_DESTINATION_CREATE_FAILED) from None
    if destination_exists:
        raise ValueError(_DESTINATION_EXISTS)
    if destination_has_sidecar:
        raise ValueError(_DESTINATION_SIDECAR_EXISTS)

    try:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise RuntimeError(_DESTINATION_DIRECTORY_FAILED) from None

    try:
        descriptor = os.open(
            destination_path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR,
            0o600,
        )
    except FileExistsError:
        raise ValueError(_DESTINATION_EXISTS) from None
    except OSError:
        raise RuntimeError(_DESTINATION_CREATE_FAILED) from None

    try:
        identity_stat = os.fstat(descriptor)
        destination_identity = identity_stat.st_dev, identity_stat.st_ino
    except OSError:
        os.close(descriptor)
        _remove_owned_target(destination_path, (0, 0))
        raise RuntimeError(_DESTINATION_CREATE_FAILED) from None
    try:
        os.close(descriptor)
    except OSError:
        _remove_owned_target(destination_path, destination_identity)
        raise RuntimeError(_DESTINATION_CREATE_FAILED) from None

    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    failure_code: str | None = None
    foreign_key_errors = 0

    try:
        source_connection = _readonly_connection(source_path)
        destination_connection = sqlite3.connect(destination_path, timeout=5)
        source_connection.backup(destination_connection)

        integrity_result = destination_connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()
        if integrity_result != ("ok",):
            failure_code = _BACKUP_INTEGRITY_FAILED
        else:
            foreign_key_errors = len(
                destination_connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            if foreign_key_errors:
                failure_code = _BACKUP_FOREIGN_KEY_FAILED
    except (OSError, sqlite3.Error):
        failure_code = _BACKUP_FAILED
    finally:
        if destination_connection is not None:
            try:
                destination_connection.close()
            except sqlite3.Error:
                failure_code = failure_code or _BACKUP_CLOSE_FAILED
        if source_connection is not None:
            try:
                source_connection.close()
            except sqlite3.Error:
                failure_code = failure_code or _SOURCE_CLOSE_FAILED

    current_identity = _path_identity(destination_path)
    if current_identity != destination_identity:
        failure_code = failure_code or _DESTINATION_CHANGED

    if failure_code is not None:
        _remove_owned_target(destination_path, destination_identity)
        raise RuntimeError(failure_code) from None

    try:
        digest = hashlib.sha256()
        byte_count = 0
        with destination_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                byte_count += len(chunk)
                digest.update(chunk)
    except OSError:
        _remove_owned_target(destination_path, destination_identity)
        raise RuntimeError(_BACKUP_READ_FAILED) from None

    if _path_identity(destination_path) != destination_identity:
        _remove_owned_target(destination_path, destination_identity)
        raise RuntimeError(_DESTINATION_CHANGED) from None

    return {
        "integrity": "ok",
        "foreign_key_errors": foreign_key_errors,
        "sha256": digest.hexdigest(),
        "bytes": byte_count,
    }


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _encode_row(row: tuple[object, ...]) -> bytes:
    encoded = bytearray(len(row).to_bytes(8, "big"))
    for value in row:
        if value is None:
            kind, payload = b"N", b""
        elif isinstance(value, int):
            kind, payload = b"I", str(value).encode("ascii")
        elif isinstance(value, float):
            kind, payload = b"F", struct.pack(">d", value)
        elif isinstance(value, str):
            kind, payload = b"T", value.encode("utf-8", errors="surrogateescape")
        elif isinstance(value, bytes):
            kind, payload = b"B", value
        else:
            raise TypeError from None
        encoded.extend(kind)
        encoded.extend(len(payload).to_bytes(8, "big"))
        encoded.extend(payload)
    return bytes(encoded)


def database_fingerprint(
    database: Path, *, exclude: Iterable[str] = ()
) -> dict:
    """Return deterministic SHA-256 fingerprints for persistent tables.

    Only per-table hashes and row counts leave this function; row values are
    encoded and hashed in memory. SQLite's internal tables are omitted.
    """
    database_path = _as_path(database)
    try:
        database_exists = database_path.exists()
        database_is_file = database_path.is_file()
    except (OSError, ValueError):
        raise RuntimeError(_DATABASE_UNAVAILABLE) from None

    if not database_exists:
        raise ValueError(_DATABASE_NOT_FOUND)
    if not database_is_file:
        raise ValueError(_DATABASE_NOT_FILE)
    try:
        database_path.resolve(strict=True)
    except (OSError, ValueError):
        raise RuntimeError(_DATABASE_UNAVAILABLE) from None

    try:
        excluded_names = (exclude,) if isinstance(exclude, str) else exclude
        excluded = set()
        for name in excluded_names:
            if not isinstance(name, str):
                raise ValueError(_INVALID_EXCLUDE)  # noqa: TRY004
            excluded.add(name.casefold())
    except TypeError:
        raise ValueError(_INVALID_EXCLUDE) from None

    connection: sqlite3.Connection | None = None
    fingerprints: dict[str, dict[str, int | str]] = {}
    try:
        connection = _readonly_connection(database_path)
        connection.execute("BEGIN")
        tables = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' ORDER BY name COLLATE BINARY"
        ).fetchall()
        for table_name, create_sql in tables:
            if table_name.casefold().startswith("sqlite_"):
                continue
            if table_name.casefold() in excluded:
                continue

            table_hasher = hashlib.sha256()
            table_hasher.update(b"sqlite-table-fingerprint-v1\0")
            table_name_bytes = table_name.encode("utf-8", errors="surrogateescape")
            schema_bytes = (create_sql or "").encode(
                "utf-8", errors="surrogateescape"
            )
            table_hasher.update(len(table_name_bytes).to_bytes(8, "big"))
            table_hasher.update(table_name_bytes)
            table_hasher.update(len(schema_bytes).to_bytes(8, "big"))
            table_hasher.update(schema_bytes)

            cursor = connection.execute(
                f"SELECT * FROM {_quote_identifier(table_name)}"
            )
            row_hashes = [
                hashlib.sha256(_encode_row(row)).digest() for row in cursor
            ]
            row_hashes.sort()
            row_count = len(row_hashes)
            table_hasher.update(row_count.to_bytes(8, "big"))
            for row_hash in row_hashes:
                table_hasher.update(row_hash)

            fingerprints[table_name] = {
                "sha256": table_hasher.hexdigest(),
                "row_count": row_count,
            }

    except (OSError, sqlite3.Error, TypeError, UnicodeError, OverflowError):
        raise RuntimeError(_FINGERPRINT_FAILED) from None
    finally:
        if connection is not None:
            with suppress(sqlite3.Error):
                connection.close()

    return {"tables": fingerprints}
