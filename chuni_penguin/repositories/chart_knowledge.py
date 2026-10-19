"""Pinned legacy annotations and explicit read-only chart binding evidence."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Never

from chuni_penguin.identities import ReviewEvidence
from chuni_penguin.providers.models import ExternalChartRef, ExternalSongRef


class KnowledgeError(Exception):
    """Safe reason code, with no private data or storage path."""


def _invalid(reason: str) -> Never:
    raise KnowledgeError(reason)


def _keys(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _invalid("invalid_annotation_fields")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("duplicate_json_key")
        result[key] = value
    return result


def _document(path: Path) -> tuple[bytes, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            _invalid("annotation_size_limit")
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: _invalid("nonfinite_json_number"),
        )
    except (OSError, ValueError, UnicodeError):
        msg = "annotation_document_unavailable"
        raise KnowledgeError(msg) from None
    return raw, value


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid("invalid_annotation_reference")
    assert isinstance(value, str)
    return value


def _hash(value: object) -> str:
    result = _string(value)
    if not re.fullmatch("[0-9a-f]{64}", result):
        _invalid("invalid_annotation_hash")
    return result


@dataclass(frozen=True)
class AnnotationOrigin:
    namespace: str
    generation_method: str
    version: str
    source_document_sha256: str
    claimed_workbook: str
    observed_at: datetime
    # The legacy JSON does not record these facts. Import time cannot supply them.
    created_at: datetime | None = None
    updated_at: datetime | None = None
    worksheet_row: str | None = None
    original_builder_revision: str | None = None
    difficulty_evidence_verified: bool = False


@dataclass(frozen=True)
class ManualAnnotation:
    row_id: str
    pointer: str
    origin: AnnotationOrigin
    title: str | None
    difficulty_label: str | None
    difficulty_code: str | None
    tags: tuple[str, ...]
    raw_record_json: str
    issues: tuple[str, ...]


class ChartKnowledgeRepository:
    """One immutable artifact; duplicate/unknown rows stay observations, not IDs."""

    def __init__(self, path: Path):
        self.raw_bytes, document = _document(path)
        if (
            not isinstance(document, dict)
            or not isinstance(document.get("charts"), list)
            or len(document["charts"]) > 10000
        ):
            _invalid("invalid_legacy_annotation_document")
        self.sha256 = hashlib.sha256(self.raw_bytes).hexdigest()
        self.origin = AnnotationOrigin(
            "poopenguin_manual",
            "human_spreadsheet_legacy_transform",
            self.sha256,
            self.sha256,
            _string(document.get("source")),
            datetime.now(UTC),
        )
        rows = []
        for position, raw in enumerate(document["charts"]):
            issues = []
            obj = raw if isinstance(raw, dict) else {}
            title = obj.get("title")
            if not isinstance(title, str) or not title:
                title = None
                issues.append("unknown_title")
            label = obj.get("difficulty")
            label = label if isinstance(label, str) else None
            code = {"Expert": "EXP", "Master": "MAS", "Ultima": "ULT"}.get(label or "")
            if code is None:
                issues.append("unknown_difficulty")
            tags = obj.get("tags")
            if not isinstance(tags, list) or any(
                not isinstance(tag, str) or not tag for tag in tags
            ):
                issues.append("invalid_tag_list")
                normalized_tags = ()
            else:
                normalized_tags = tuple(tags)
            pointer = f"#/charts/{position}"
            rows.append(
                ManualAnnotation(
                    self.sha256 + pointer,
                    pointer,
                    self.origin,
                    title,
                    label,
                    code,
                    normalized_tags,
                    json.dumps(
                        raw, ensure_ascii=False, sort_keys=True, allow_nan=False
                    ),
                    tuple(issues),
                )
            )
        self.rows = tuple(rows)

    def legacy_table(self) -> dict[str, Any]:
        """Fresh decoded original values for later B algorithm compatibility."""
        return json.loads(self.raw_bytes.decode("utf-8-sig"))


@dataclass(frozen=True)
class AnnotationBinding:
    row_id: str
    ref: ExternalChartRef
    evidence: ReviewEvidence


class AnnotationBindings:
    def __init__(self, path: Path, rows: dict[str, ManualAnnotation]):
        raw, document = _document(path)
        obj = _keys(document, {"schema_version", "catalog", "bindings"})
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            _invalid("unsupported_annotation_binding_schema")
        catalog = _keys(
            obj["catalog"],
            {
                "provider_key",
                "namespace",
                "region",
                "game_version",
                "revision",
                "source_id",
            },
        )
        self.catalog = MappingProxyType(catalog)
        for key in ("provider_key", "namespace", "region", "game_version"):
            _string(self.catalog[key])
        for key in ("revision", "source_id"):
            _hash(self.catalog[key])
        if not isinstance(obj["bindings"], list) or len(obj["bindings"]) > 1000:
            _invalid("invalid_annotation_bindings")
        bindings = []
        seen = set()
        for raw_binding in obj["bindings"]:
            binding = _keys(raw_binding, {"row_id", "ref", "evidence"})
            row_id = _string(binding["row_id"])
            row = rows.get(row_id)
            if row is None or row_id in seen or row.issues:
                _invalid("unknown_duplicate_or_invalid_annotation_row")
            seen.add(row_id)
            ref = _keys(
                binding["ref"], {"song_id", "difficulty", "variant", "chart_id"}
            )
            native = ref["chart_id"]
            target = ExternalChartRef(
                ExternalSongRef(self.catalog["namespace"], _string(ref["song_id"])),
                _string(ref["difficulty"]),
                _string(ref["variant"]),
                None if native is None else _string(native),
            )
            if target.difficulty != row.difficulty_code:
                _invalid("annotation_difficulty_mismatch")
            evidence = ReviewEvidence(
                **_keys(binding["evidence"], {"actor", "reason", "reference", "sha256"})
            )
            evidence.validate()
            if evidence.sha256 != row.origin.source_document_sha256:
                _invalid("annotation_review_hash_mismatch")
            bindings.append(AnnotationBinding(row_id, target, evidence))
        self.entries = tuple(bindings)
        self.revision = hashlib.sha256(raw).hexdigest()
