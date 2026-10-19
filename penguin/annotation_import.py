"""Offline, transactional annotation import. Never seeds or rewrites player data."""

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from chuni_penguin.database import ChartAnnotation, ChartAnnotationTag
from chuni_penguin.database.annotations import ANNOTATION_TABLES, normalize_tag

TABLES = (ChartAnnotation.__table__, ChartAnnotationTag.__table__)
FIELDS = (
    "source_row",
    "source_sheet",
    "source_excel_row",
    "chart_id",
    "song_id",
    "difficulty",
    "title",
    "genre",
    "source_const",
    "source_notes",
    "sss_stars",
    "note",
    "flags_json",
    "links_json",
    "raw_cells_json",
    "review_json",
    "match_method",
)


def json_text(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fail(reason):
    raise ValueError(reason)


def _hash(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _connect(path, apply=False):
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if apply else "?mode=ro"),
        uri=True,
        isolation_level=None,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if not apply:
        connection.execute("PRAGMA query_only=ON")
    return connection


def _schema_signature(connection, table):
    # Only internal constants are accepted as SQL identifiers here.
    columns = [
        tuple(row)[1:6] for row in connection.execute(f'PRAGMA table_info("{table}")')
    ]
    foreign = sorted(
        tuple(row)[2:]
        for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')
    )
    indices = []
    for index in connection.execute(f'PRAGMA index_list("{table}")'):
        name = index[1].replace('"', '""')
        cols = tuple(
            row[2] for row in connection.execute(f'PRAGMA index_info("{name}")')
        )
        indices.append((index[2], cols, index[4]))
    return columns, foreign, sorted(indices)


def _ddl(connection):
    for table in TABLES:
        connection.execute(str(CreateTable(table).compile(dialect=dialect())))
        for index in table.indexes:
            connection.execute(str(CreateIndex(index).compile(dialect=dialect())))


def _schema(connection, install=False):
    present = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if not {"source_snapshots", "chunirec_songs", "chunirec_charts"} <= present:
        _fail("annotation_catalog_schema_missing")
    found = present.intersection(ANNOTATION_TABLES)
    if found and found != set(ANNOTATION_TABLES):
        _fail("annotation_partial_schema")
    if found:
        with closing(sqlite3.connect(":memory:")) as reference:
            _ddl(reference)
            for table in ANNOTATION_TABLES:
                if _schema_signature(reference, table) != _schema_signature(
                    connection, table
                ):
                    _fail("annotation_schema_mismatch")
    elif install:
        _ddl(connection)
    return not bool(found)


def _prepare(source):
    if (
        source.get("namespace") != "poopenguin_manual"
        or not _hash(source.get("revision"))
        or not _hash(source.get("workbook_sha256"))
    ):
        _fail("annotation_source_identity_invalid")
    rows = source.get("rows")
    if not isinstance(rows, list) or not rows:
        _fail("annotation_rows_missing")
    prepared, row_ids, locations = [], set(), set()
    for row in rows:
        for name, minimum in (
            ("source_row", 0),
            ("excel_row", 1),
            ("chart_id", 1),
            ("song_id", 1),
        ):
            if type(row.get(name)) is not int or row[name] < minimum:
                _fail("annotation_row_identity_invalid")
        if (
            row["source_row"] in row_ids
            or (row.get("sheet"), row["excel_row"]) in locations
        ):
            _fail("annotation_duplicate_source_row")
        row_ids.add(row["source_row"])
        locations.add((row.get("sheet"), row["excel_row"]))
        if (
            not isinstance(row.get("sheet"), str)
            or not row["sheet"]
            or not isinstance(row.get("title"), str)
            or not row["title"]
        ):
            _fail("annotation_source_label_invalid")
        if row.get("difficulty") not in ("BAS", "ADV", "EXP", "MAS", "ULT") or row.get(
            "match_method"
        ) not in ("automatic_exact", "agent_reviewed"):
            _fail("annotation_match_invalid")
        for key in ("genre", "note"):
            if row.get(key) is not None and not isinstance(row[key], str):
                _fail("annotation_text_invalid")
        stars, notes, const = (
            row.get("sss_stars"),
            row.get("source_notes"),
            row.get("source_const"),
        )
        if stars is not None and (type(stars) is not int or not 1 <= stars <= 5):
            _fail("annotation_stars_invalid")
        if notes is not None and (type(notes) is not int or notes < 0):
            _fail("annotation_notes_invalid")
        if const is not None and (
            type(const) not in (int, float) or not math.isfinite(const) or const <= 0
        ):
            _fail("annotation_constant_invalid")
        tags = {}
        if not isinstance(row.get("tags"), list):
            _fail("annotation_tags_invalid")
        for tag in row["tags"]:
            if not isinstance(tag, str) or not normalize_tag(tag):
                _fail("annotation_tags_invalid")
            tags.setdefault(normalize_tag(tag), tag)
        item = {name: row.get(name) for name in FIELDS[:12]}
        item.update(source_sheet=row["sheet"], source_excel_row=row["excel_row"])
        for name, expected in (
            ("flags", list),
            ("links", list),
            ("raw_cells", dict),
            ("review_json", dict),
        ):
            if not isinstance(row.get(name), expected):
                _fail("annotation_metadata_invalid")
            item[name if name == "review_json" else name + "_json"] = json_text(
                row[name]
            )
        item["match_method"] = row["match_method"]
        item["tags"] = dict(sorted(tags.items()))
        prepared.append(item)
    return sorted(prepared, key=lambda row: row["source_row"])


def _check_catalog(connection, rows):
    catalog = {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            "SELECT c.id,c.song_id,c.difficulty FROM chunirec_charts c JOIN chunirec_songs s ON s.id=c.song_id"
        )
    }
    for row in rows:
        if catalog.get(row["chart_id"]) != (row["song_id"], row["difficulty"]):
            _fail("annotation_chart_identity_mismatch")


def _stored(connection, snapshot):
    result = []
    for row in connection.execute(
        "SELECT * FROM chart_annotations WHERE source_snapshot_id=? ORDER BY source_row",
        (snapshot,),
    ):
        item = {name: row[name] for name in FIELDS}
        item["tags"] = dict(
            connection.execute(
                "SELECT normalized_tag,tag FROM chart_annotation_tags WHERE annotation_id=? ORDER BY normalized_tag",
                (row["id"],),
            )
        )
        result.append(item)
    return result


def _active(connection, namespace):
    if connection.execute(
        "SELECT 1 FROM chart_annotations WHERE namespace=? GROUP BY source_snapshot_id HAVING MIN(active) != MAX(active)",
        (namespace,),
    ).fetchone():
        _fail("annotation_partial_active_batch")
    ids = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT source_snapshot_id FROM chart_annotations WHERE namespace=? AND active=1 ORDER BY source_snapshot_id",
            (namespace,),
        )
    ]
    if len(ids) > 1:
        _fail("annotation_multiple_active_versions")
    return ids


def import_annotations(database, source, *, apply=False):
    """Validate everything, then atomically install/activate exactly one source batch."""
    rows = _prepare(source)
    namespace, snapshot = source["namespace"], "annotation:" + source["revision"]
    digest = hashlib.sha256(json_text(rows).encode()).hexdigest()
    with closing(_connect(database, apply)) as connection:
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        try:
            schema_required = _schema(connection)
            _check_catalog(connection, rows)
            active = [] if schema_required else _active(connection, namespace)
            old = connection.execute(
                "SELECT * FROM source_snapshots WHERE id=?", (snapshot,)
            ).fetchone()
            if old:
                provenance = json.loads(old["provenance_json"])
                if (
                    schema_required
                    or old["namespace"] != namespace
                    or old["revision"] != source["revision"]
                    or old["payload_sha256"] != source["workbook_sha256"]
                    or provenance.get("annotation_payload_sha256") != digest
                    or _stored(connection, snapshot) != rows
                ):
                    _fail("annotation_existing_batch_mismatch")
                # Reactivation of an old revision could overwrite a later batch's rollback chain.
                # After rollback, explicit reapply is safe only when its recorded predecessor is current.
                previous = provenance.get("previous_active_snapshots", [])
                if snapshot not in active and previous != active:
                    _fail("annotation_historical_reactivation_conflict")
            status = (
                "unchanged"
                if snapshot in active
                else "reactivated"
                if old
                else "imported"
            )
            result = {
                "mode": "apply" if apply else "dry_run",
                "status": status,
                "batch_id": snapshot,
                "schema_required": schema_required,
                "annotations": len(rows),
                "charts": len({row["chart_id"] for row in rows}),
                "tagged_annotations": sum(bool(row["tags"]) for row in rows),
                "tags": sum(len(row["tags"]) for row in rows),
                "notes": sum(bool(row["note"]) for row in rows),
                "links": sum(len(json.loads(row["links_json"])) for row in rows),
                "automatic_exact": sum(
                    row["match_method"] == "automatic_exact" for row in rows
                ),
                "agent_reviewed": sum(
                    row["match_method"] == "agent_reviewed" for row in rows
                ),
                "inserted": len(rows) if not old else 0,
                "deactivated_batches": active if snapshot not in active else [],
            }
            if not apply or status == "unchanged":
                connection.rollback()
                return result
            _schema(connection, install=True)
            if not old:
                now = datetime.now(UTC).timestamp()
                provenance = dict(source.get("provenance", {}))
                provenance.update(
                    annotation_schema=1,
                    annotation_payload_sha256=digest,
                    previous_active_snapshots=active,
                )
                connection.execute(
                    "INSERT INTO source_snapshots (id,provider_key,namespace,scope_key,revision,payload_sha256,provenance_json,observed_at,imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        snapshot,
                        "poopenguin",
                        namespace,
                        "chart_annotations",
                        source["revision"],
                        source["workbook_sha256"],
                        json_text(provenance),
                        now,
                        now,
                    ),
                )
                fields = (
                    "source_snapshot_id",
                    "namespace",
                    *FIELDS,
                    "active",
                    "imported_at",
                )
                for row in rows:
                    values = (
                        snapshot,
                        namespace,
                        *(row[name] for name in FIELDS),
                        0,
                        now,
                    )
                    cursor = connection.execute(
                        f"INSERT INTO chart_annotations ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                        values,
                    )
                    connection.executemany(
                        "INSERT INTO chart_annotation_tags (annotation_id,normalized_tag,tag) VALUES (?,?,?)",
                        [
                            (cursor.lastrowid, normalized, tag)
                            for normalized, tag in row["tags"].items()
                        ],
                    )
            connection.execute(
                "UPDATE chart_annotations SET active=0 WHERE namespace=? AND active=1",
                (namespace,),
            )
            connection.execute(
                "UPDATE chart_annotations SET active=1 WHERE source_snapshot_id=?",
                (snapshot,),
            )
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                _fail("annotation_foreign_key_failure")
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise


def rollback_annotations(database, batch_id, *, apply=False):
    """Deactivate only this batch, restoring its predecessor without deleting history."""
    if (
        not isinstance(batch_id, str)
        or not batch_id.startswith("annotation:")
        or not _hash(batch_id[11:])
    ):
        _fail("annotation_batch_id_invalid")
    with closing(_connect(database, apply)) as connection:
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        try:
            if _schema(connection):
                _fail("annotation_batch_missing")
            snapshot = connection.execute(
                "SELECT * FROM source_snapshots WHERE id=? AND scope_key='chart_annotations'",
                (batch_id,),
            ).fetchone()
            if snapshot is None:
                _fail("annotation_batch_missing")
            namespace = snapshot["namespace"]
            if _active(connection, namespace) != [batch_id]:
                _fail("annotation_batch_not_active")
            previous = json.loads(snapshot["provenance_json"]).get(
                "previous_active_snapshots", []
            )
            if not isinstance(previous, list) or len(previous) > 1:
                _fail("annotation_rollback_history_invalid")
            for old in previous:
                rows = connection.execute(
                    "SELECT chart_id,song_id,difficulty FROM chart_annotations WHERE source_snapshot_id=? AND namespace=?",
                    (old, namespace),
                ).fetchall()
                if not rows:
                    _fail("annotation_rollback_history_missing")
                _check_catalog(connection, rows)
            if apply:
                connection.execute(
                    "UPDATE chart_annotations SET active=0 WHERE source_snapshot_id=?",
                    (batch_id,),
                )
                for old in previous:
                    connection.execute(
                        "UPDATE chart_annotations SET active=1 WHERE source_snapshot_id=? AND namespace=?",
                        (old, namespace),
                    )
                connection.commit()
            else:
                connection.rollback()
            return {
                "mode": "apply" if apply else "dry_run",
                "status": "rolled_back",
                "batch_id": batch_id,
                "restored_batches": previous,
            }
        except BaseException:
            connection.rollback()
            raise
