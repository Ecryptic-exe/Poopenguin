"""Prepare a reviewed plan, then apply it exclusively to a NEW database copy."""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import URL, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chuni_penguin.application.offline import deny_network
from chuni_penguin.database.identity import MAPPING_TABLES
from chuni_penguin.identities import (
    MappingError,
    ReviewStep,
    digest,
    encode,
    snapshot_identity,
)
from chuni_penguin.providers.chunilib_snapshot import ChunilibSnapshotProvider
from chuni_penguin.providers.models import CatalogSnapshot
from chuni_penguin.providers.registry import ProviderError
from chuni_penguin.repositories.identity import IdentityMappingRepository
from chuni_penguin.services.database import (
    WriterQueue,
    disable_isolation_level,
    emit_begin_immediate,
)
from chuni_penguin.services.mapping_review import (
    decisions,
    fail,
    object_keys,
    parse_steps,
    read_json,
    serialize_steps,
)

HEAD = "7e2a0c9d4b31"
PLAN_FIELDS = {
    "schema_version",
    "kind",
    "prepared_at",
    "database_fingerprint",
    "source",
    "operations",
    "preview",
    "approval_digest",
}


def serialized(value: Any) -> str:
    result = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    if len(result.encode("utf-8")) > 2 * 1024 * 1024:
        fail("review_json_size_limit")
    return result


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _check_database(connection: sqlite3.Connection):
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if (
        not {*MAPPING_TABLES, "alembic_version", "chunirec_songs", "chunirec_charts"}
        <= names
    ):
        fail("mapping_schema_not_migrated")
    if connection.execute("SELECT version_num FROM alembic_version").fetchall() != [
        (HEAD,)
    ]:
        fail("unsupported_database_revision")
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        fail("database_integrity_failure")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        fail("database_foreign_key_failure")


def _fingerprint(connection: sqlite3.Connection) -> str:
    result = hashlib.sha256()
    for statement in connection.iterdump():
        data = statement.encode("utf-8")
        result.update(len(data).to_bytes(8, "big"))
        result.update(data)
    result.update(
        encode(
            [
                connection.execute("PRAGMA user_version").fetchone(),
                connection.execute("PRAGMA application_id").fetchone(),
            ]
        ).encode()
    )
    return result.hexdigest()


def backup_copy(source: Path, destination: Path, expected: str | None = None) -> str:
    """Fingerprint and backup the same read transaction, including WAL content."""
    with closing(_readonly(source)) as reader:
        reader.execute("BEGIN")
        _check_database(reader)
        fingerprint = _fingerprint(reader)
        if expected is not None and fingerprint != expected:
            fail("database_fingerprint_changed")
        with closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer)
    return fingerprint


@contextmanager
def _temporary_database(directory: Path | None = None):
    descriptor, name = tempfile.mkstemp(
        prefix=".mapping-review-", suffix=".sqlite3", dir=directory
    )
    os.close(descriptor)
    path = Path(name)
    try:
        yield path
    finally:
        # Only files owned by this unique tempfile are removed, never a user DB.
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)


async def _snapshot(path: Path, game_version: str) -> CatalogSnapshot:
    provider = ChunilibSnapshotProvider(path, game_version)
    try:
        await provider.start()
        return await provider.read_catalog()
    finally:
        await provider.close()


def _pin(snapshot: CatalogSnapshot) -> dict[str, Any]:
    source_id, payload_hash = snapshot_identity(snapshot)
    return {
        "source_snapshot_id": source_id,
        "payload_sha256": payload_hash,
        "provenance": json.loads(encode(asdict(snapshot.provenance))),
        "songs": len(snapshot.songs),
        "charts": sum(len(s.charts) for s in snapshot.songs),
        "issue_count": len(snapshot.issues),
    }


def _foreign_keys(connection, _):
    with closing(connection.cursor()) as cursor:
        cursor.execute("PRAGMA foreign_keys=ON")


async def _execute(
    path: Path, snapshot: CatalogSnapshot, steps: tuple[ReviewStep, ...]
):
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(path.resolve())),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
    )
    event.listen(engine.sync_engine, "connect", _foreign_keys)
    event.listen(engine.sync_engine, "connect", disable_isolation_level)
    event.listen(engine.sync_engine, "begin", emit_begin_immediate)
    writer = WriterQueue(engine)
    writer.start()
    try:
        repository = IdentityMappingRepository(async_sessionmaker(engine), writer)
        result = await repository.review_catalog(snapshot, steps)
        return json.loads(encode(asdict(result)))
    finally:
        await writer.stop()


def _finish(path: Path):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if connection.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            fail("output_not_standalone")
        _check_database(connection)


async def prepare_plan(
    snapshot_path: Path,
    database: Path,
    steps: tuple[ReviewStep, ...] = (),
    game_version: str = "crossversex",
) -> dict[str, Any]:
    snapshot = await _snapshot(snapshot_path, game_version)
    with _temporary_database() as temporary:
        fingerprint = backup_copy(database, temporary)
        preview = await _execute(temporary, snapshot, steps)
        _finish(temporary)
    plan = {
        "schema_version": 1,
        "kind": "mapping_review_copy_plan",
        "prepared_at": datetime.now(UTC).isoformat(),
        "database_fingerprint": fingerprint,
        "source": _pin(snapshot),
        "operations": serialize_steps(steps),
        "preview": preview,
    }
    plan["approval_digest"] = digest(plan)
    serialized(plan)
    return plan


def validate_plan(
    value: Any, approval: str
) -> tuple[dict[str, Any], tuple[ReviewStep, ...]]:
    plan = object_keys(value, PLAN_FIELDS)
    if (
        type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["kind"] != "mapping_review_copy_plan"
    ):
        fail("unsupported_review_schema")
    token = plan["approval_digest"]
    actual = digest({k: v for k, v in plan.items() if k != "approval_digest"})
    if (
        not isinstance(token, str)
        or not hmac.compare_digest(actual, token)
        or not hmac.compare_digest(actual, approval)
    ):
        fail("review_approval_mismatch")
    return plan, parse_steps(plan["operations"])


def _new_output(path: Path):
    if any(
        Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink()
        for suffix in ("", "-wal", "-shm", "-journal")
    ):
        fail("output_already_exists")


def _publish(temporary: Path, output: Path):
    _new_output(output)
    # Exclusive hard-link publication: only a complete closed DB becomes visible.
    # Existing paths are never replaced, including a race after the check above.
    os.link(temporary, output)


async def apply_plan(
    snapshot_path: Path,
    database: Path,
    value: Any,
    approval: str,
    output: Path,
    game_version: str = "crossversex",
) -> dict[str, Any]:
    plan, steps = validate_plan(value, approval)
    _new_output(output)
    snapshot = await _snapshot(snapshot_path, game_version)
    if _pin(snapshot) != plan["source"]:
        fail("review_source_changed")
    with _temporary_database(output.parent) as temporary:
        backup_copy(database, temporary, plan["database_fingerprint"])
        preview = await _execute(temporary, snapshot, steps)
        if preview != plan["preview"]:
            fail("review_preview_changed")
        _finish(temporary)
        _publish(temporary, output)
    return {
        "complete": True,
        "mode": "new_database_copy",
        "approval_digest": approval,
        "result": preview,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="mode", required=True)
    for mode in ("prepare", "apply"):
        command = subcommands.add_parser(mode)
        command.add_argument("snapshot", type=Path)
        command.add_argument(
            "--catalog-db",
            type=Path,
            required=True,
            help="Existing consistent SQLite copy; opened read-only.",
        )
        command.add_argument("--game-version", default="crossversex")
        if mode == "prepare":
            command.add_argument("--decisions", type=Path)
            command.add_argument(
                "--output",
                type=Path,
                required=True,
                help="New review plan JSON; exclusive creation.",
            )
        else:
            command.add_argument("--plan", type=Path, required=True)
            command.add_argument(
                "--approve", required=True, help="Exact digest of the inspected plan."
            )
            command.add_argument(
                "--output-db",
                type=Path,
                required=True,
                help="NEW DB path; the input is never modified.",
            )
    args = parser.parse_args()

    async def run():
        with deny_network([]):
            if args.mode == "prepare":
                plan = await prepare_plan(
                    args.snapshot,
                    args.catalog_db,
                    decisions(args.decisions),
                    args.game_version,
                )
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(serialized(plan))
                return {
                    "complete": True,
                    "mode": "prepared",
                    "approval_digest": plan["approval_digest"],
                    "preview": plan["preview"],
                }
            return await apply_plan(
                args.snapshot,
                args.catalog_db,
                read_json(args.plan),
                args.approve,
                args.output_db,
                args.game_version,
            )

    try:
        result = asyncio.run(run())
    except (MappingError, ProviderError) as exc:
        reason = exc.reason if isinstance(exc, ProviderError) else str(exc)
        parser.exit(2, encode({"error": reason}) + "\n")
    except (OSError, sqlite3.Error, SQLAlchemyError, ValueError):
        parser.exit(2, encode({"error": "review_io_or_storage_failure"}) + "\n")
    print(serialized(result))


if __name__ == "__main__":
    main()
