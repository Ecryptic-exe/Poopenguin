"""Read a pinned v3 semantic snapshot without importing its writing workflow.

These are source observations, never canonical chart bindings. No paths inside
the source database or artifact JSON are opened by this reader.
"""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never

from chuni_penguin.repositories.chart_knowledge import KnowledgeError

MAX_CELL = 2 * 1024 * 1024
MAX_SNAPSHOT = 16 * 1024 * 1024
MAX_ROWS = 10000


def _fail(reason: str) -> Never:
    raise KnowledgeError(reason)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("semantic_duplicate_json_key")
        result[key] = value
    return result


def _json(raw: str) -> dict[str, Any]:
    value = json.loads(
        raw,
        object_pairs_hook=_unique,
        parse_constant=lambda _: _fail("semantic_nonfinite_json"),
    )
    if not isinstance(value, dict):
        _fail("semantic_object_required")
    return value


def _encode(value: object) -> str:
    # Deliberately matches v3.store.encode (including its default separators).
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def _text(value: object, limit: int = MAX_CELL) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > limit:
        _fail("semantic_invalid_or_oversized_field")
    return value


def _hash(value: object) -> str:
    result = _text(value, 64)
    if not re.fullmatch("[0-9a-f]{64}", result):
        _fail("semantic_invalid_hash")
    return result


def _date(value: object) -> datetime:
    parsed = datetime.fromisoformat(_text(value, 64))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("semantic_date_without_timezone")
    return parsed


@dataclass(frozen=True)
class SemanticEvidence:
    unit_id: str
    source_id: str
    locator: str
    text: str
    sha256: str
    assignment: str
    raw_provenance_json: str
    imported_at: datetime


@dataclass(frozen=True)
class SemanticObservation:
    row_id: str
    analysis_id: str
    chart_key: str
    catalogue_id: str
    song_id: str
    difficulty_label: str
    variant: str
    title: str
    role: str
    status: str
    input_sha256: str
    prompt_sha256: str
    imported_at: datetime
    raw_artifact_json: str
    tags: tuple[str, ...]
    summary: str | None
    evidence: tuple[SemanticEvidence, ...]
    is_latest: bool
    current: bool
    evidence_verified: bool
    issues: tuple[str, ...]
    # An artifact alone cannot establish either of these claims.
    input_packet_verified: bool = False
    model_execution_verified: bool = False


@dataclass(frozen=True)
class SemanticOrigin:
    namespace: str
    generation_method: str
    version: str
    catalogue_id: str
    region: str
    game_version: str
    data_version: str
    raw_manifest_json: str
    created_at: datetime
    observed_at: datetime


class SemanticKnowledgeRepository:
    """One bounded, consistent read; immutable until explicit process restart."""

    def __init__(self, workspace: Path, catalogue_id: str):
        try:
            self._load(workspace, _text(catalogue_id, 256))
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            TypeError,
            UnicodeError,
            RecursionError,
        ):
            # Do not surface private paths, raw SQL, artifact text or credentials.
            msg = "semantic_snapshot_unavailable"
            raise KnowledgeError(msg) from None

    def _load(self, workspace: Path, catalogue_id: str):
        if not re.fullmatch("[0-9a-f]{24}", catalogue_id):
            _fail("semantic_invalid_catalogue_pin")
        path = (workspace / "data/workflow.sqlite").resolve()
        if not path.is_file():
            _fail("semantic_database_missing")
        prompt_hashes = {}
        for role in ("llm1", "llm2"):
            with (workspace / f"prompts/v3_{role}.md").open("rb") as handle:
                raw = handle.read(128 * 1024 + 1)
            if not raw or len(raw) > 128 * 1024:
                _fail("semantic_prompt_size_limit")
            prompt_hashes[role] = hashlib.sha256(raw).hexdigest()

        deadline = time.monotonic() + 5
        self._bytes_read = 0
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            db.execute("BEGIN")
            required = {
                "schema_info",
                "catalogues",
                "charts",
                "catalogue_entries",
                "evidence_units",
                "semantics",
            }
            objects = dict(db.execute("SELECT name,type FROM sqlite_schema"))
            if any(objects.get(name) != "table" for name in required):
                _fail("semantic_schema_tables_required")
            if db.execute("SELECT version FROM schema_info LIMIT 2").fetchall() != [
                (1,)
            ]:
                _fail("unsupported_semantic_schema")
            catalogues = self._read(
                db,
                "catalogues",
                "id,region,game_version,data_version,manifest,created_at",
                "WHERE id=?",
                (catalogue_id,),
                limit=1,
            )
            if len(catalogues) != 1:
                _fail("semantic_catalogue_missing")
            catalogue = catalogues[0]
            entries = self._read(
                db,
                "catalogue_entries e JOIN charts c USING(chart_key)",
                "chart_key,song_id,difficulty,variant,title",
                "WHERE catalogue_id=? ORDER BY chart_key",
                (catalogue_id,),
            )
            evidence = self._read(
                db,
                "evidence_units",
                "unit_id,chart_key,source_id,locator,text,sha256,assignment,provenance,created_at",
                "WHERE catalogue_id=? ORDER BY unit_id",
                (catalogue_id,),
            )
            semantics = self._read(
                db,
                "semantics",
                "analysis_id,chart_key,role,input_sha256,artifact,created_at",
                "WHERE catalogue_id=? ORDER BY rowid",
                (catalogue_id,),
                limit=2000,
            )

        charts = {row["chart_key"]: row for row in entries}
        if len(charts) != len(entries):
            _fail("semantic_duplicate_chart")
        for row in charts.values():
            if row["difficulty"] not in {"MASTER", "ULTIMA"}:
                _fail("semantic_unknown_difficulty")
        units: dict[str, dict[str, SemanticEvidence]] = {}
        for row in evidence:
            chart = charts.get(row["chart_key"])
            if chart is None or row["assignment"] not in {
                "defined",
                "undefined",
                "superseded",
            }:
                _fail("semantic_unknown_evidence_chart_or_assignment")
            provenance = _json(row["provenance"])
            if hashlib.sha256(row["text"].encode()).hexdigest() != _hash(row["sha256"]):
                _fail("semantic_evidence_hash_mismatch")
            if (
                row["assignment"] == "defined"
                and provenance.get("difficulty") != chart["difficulty"]
            ):
                _fail("semantic_evidence_difficulty_mismatch")
            if any(
                provenance.get(key) != row[key]
                for key in ("source_id", "locator", "text")
            ):
                _fail("semantic_provenance_mismatch")
            assets = provenance.get("assets", [])
            if not isinstance(assets, list) or any(
                not isinstance(asset, dict) for asset in assets
            ):
                _fail("semantic_invalid_assets")
            identity = {
                key: provenance[key]
                for key in ("source_id", "locator", "text", "difficulty")
                if key in provenance
            }
            identity["asset_hashes"] = sorted(
                _hash(asset.get("sha256")) for asset in assets
            )
            if (
                _digest(
                    {
                        "catalogue": catalogue_id,
                        "chart": row["chart_key"],
                        "unit": identity,
                        "sha": row["sha256"],
                    }
                )
                != row["unit_id"]
            ):
                _fail("semantic_evidence_identity_mismatch")
            unit = SemanticEvidence(
                _hash(row["unit_id"]),
                row["source_id"],
                row["locator"],
                row["text"],
                row["sha256"],
                row["assignment"],
                row["provenance"],
                _date(row["created_at"]),
            )
            bucket = units.setdefault(row["chart_key"], {})
            if unit.unit_id in bucket:
                _fail("semantic_duplicate_evidence")
            bucket[unit.unit_id] = unit

        latest = {
            (row["chart_key"], row["role"]): row["analysis_id"] for row in semantics
        }
        rows: list[SemanticObservation] = []
        current_llm1: dict[str, str] = {}
        # LLM2 currentness depends on latest LLM1, irrespective of import order.
        for role in ("llm1", "llm2"):
            for row in semantics:
                if row["role"] not in {"llm1", "llm2"}:
                    _fail("semantic_unknown_role")
                if row["role"] != role:
                    continue
                chart = charts.get(row["chart_key"])
                if chart is None:
                    _fail("semantic_unknown_analysis_chart")
                artifact = _json(row["artifact"])
                analysis_id, input_sha = (
                    _hash(row["analysis_id"]),
                    _hash(row["input_sha256"]),
                )
                if (
                    artifact.get("role") != role
                    or artifact.get("chart_key") != row["chart_key"]
                    or artifact.get("input_sha256") != input_sha
                    or _digest({"packet": input_sha, "output": artifact}) != analysis_id
                ):
                    _fail("semantic_artifact_identity_mismatch")
                if artifact.get("status") not in {
                    "complete",
                    "partial",
                    "insufficient",
                } or not isinstance(artifact.get("limitations"), list):
                    _fail("semantic_invalid_status")
                hashes = artifact.get("input_unit_hashes")
                if not isinstance(hashes, dict) or any(
                    _hash(k) != k or _hash(v) != v for k, v in hashes.items()
                ):
                    _fail("semantic_invalid_input_hashes")
                prompt_sha = _hash(artifact.get("prompt_sha256"))
                chart_units = units.get(row["chart_key"], {})
                current_hashes = {
                    key: unit.sha256
                    for key, unit in chart_units.items()
                    if unit.assignment == "defined"
                }
                issues = []
                if hashes != current_hashes:
                    issues.append("defined_evidence_changed")
                if prompt_sha != prompt_hashes[role]:
                    issues.append("prompt_changed")
                is_latest = latest[(row["chart_key"], role)] == analysis_id
                if not is_latest:
                    issues.append("superseded_analysis")
                if role == "llm2" and artifact.get(
                    "llm1_analysis_id"
                ) != current_llm1.get(row["chart_key"]):
                    issues.append("llm1_not_current")
                tags, summary, references = self._content(artifact, role)
                evidence_issues = self._check_evidence(references, hashes, chart_units)
                issues.extend(evidence_issues)
                current = not issues
                if role == "llm1" and current:
                    current_llm1[row["chart_key"]] = analysis_id
                rows.append(
                    SemanticObservation(
                        f"v3_semantic:{catalogue_id}:{analysis_id}",
                        analysis_id,
                        row["chart_key"],
                        catalogue_id,
                        chart["song_id"],
                        chart["difficulty"],
                        chart["variant"],
                        chart["title"],
                        role,
                        artifact["status"],
                        input_sha,
                        prompt_sha,
                        _date(row["created_at"]),
                        row["artifact"],
                        tags,
                        summary,
                        tuple(chart_units.values()),
                        is_latest,
                        current,
                        bool(references) and not evidence_issues,
                        tuple(issues),
                    )
                )
        if len({row.row_id for row in rows}) != len(rows):
            _fail("semantic_duplicate_analysis")
        self.rows = tuple(rows)
        self.evidence = tuple(
            unit for bucket in units.values() for unit in bucket.values()
        )
        self.origin = SemanticOrigin(
            "v3_semantic",
            "llm_derived_from_local_evidence",
            _digest(
                {
                    "catalogue": catalogue,
                    "charts": entries,
                    "evidence": evidence,
                    "semantics": semantics,
                    "prompts": prompt_hashes,
                }
            ),
            catalogue_id,
            catalogue["region"],
            catalogue["game_version"],
            catalogue["data_version"],
            catalogue["manifest"],
            _date(catalogue["created_at"]),
            datetime.now(UTC),
        )
        _json(catalogue["manifest"])

    def _read(
        self,
        db,
        table: str,
        columns: str,
        suffix: str,
        args: tuple,
        *,
        limit: int = MAX_ROWS,
    ):
        # Identifiers/suffixes are fixed internal constants, never artifact input.
        names = columns.split(",")
        projection = ",".join(
            f"CASE WHEN length(CAST({n} AS BLOB)) <= {MAX_CELL} THEN {n} ELSE NULL END"
            for n in names
        )
        result = []
        for raw in db.execute(
            f"SELECT {projection} FROM {table} {suffix} LIMIT ?", (*args, limit + 1)
        ):
            if len(result) >= limit:
                _fail("semantic_row_limit")
            values = [_text(value) for value in raw]
            self._bytes_read += sum(len(value.encode()) for value in values)
            if self._bytes_read > MAX_SNAPSHOT:
                _fail("semantic_snapshot_size_limit")
            result.append(dict(zip(names, values, strict=True)))
        return result

    @staticmethod
    def _content(
        artifact: dict[str, Any], role: str
    ) -> tuple[tuple[str, ...], str | None, list]:
        references = []
        tags = []

        def refs(value):
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(ref, dict) for ref in value)
            ):
                _fail("semantic_exact_evidence_required")
            references.extend(value)

        if role == "llm1":
            if not isinstance(artifact.get("tags"), list):
                _fail("semantic_invalid_tags")
            for tag in artifact["tags"]:
                if not isinstance(tag, dict):
                    _fail("semantic_invalid_tag")
                tags.append(_text(tag.get("raw_tag")))
                if not tags[-1].strip():
                    _fail("semantic_empty_tag")
                refs(tag.get("evidence"))
            return tuple(tags), None, references
        summary = artifact.get("summary")
        if not isinstance(summary, str) or not isinstance(
            artifact.get("anchors", []), list
        ):
            _fail("semantic_invalid_summary")
        if summary:
            refs(artifact.get("evidence"))
        for anchor in artifact.get("anchors", []):
            if not isinstance(anchor, dict):
                _fail("semantic_invalid_anchor")
            refs(anchor.get("evidence"))
        return (), summary, references

    @staticmethod
    def _check_evidence(
        refs: list, hashes: dict, units: dict[str, SemanticEvidence]
    ) -> list[str]:
        issues = set()
        for ref in refs:
            unit_id = ref.get("unit_id")
            if not isinstance(unit_id, str) or unit_id not in hashes:
                _fail("semantic_evidence_outside_input")
            unit = units.get(unit_id)
            if (
                unit is None
                or unit.assignment != "defined"
                or unit.sha256 != hashes[unit_id]
            ):
                issues.add("evidence_no_longer_available")
            elif ref.get("asset_file"):
                # Preserve the reference, but never follow untrusted artifact paths.
                issues.add("image_evidence_not_verified")
            elif (
                not isinstance(ref.get("quote"), str)
                or not ref["quote"]
                or ref["quote"] not in unit.text
            ):
                _fail("semantic_quote_mismatch")
        return sorted(issues)
