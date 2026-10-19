"""Strict local maintenance plans. No guessed matches or implicit approvals."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Never

from chuni_penguin.identities import (
    ChartRef,
    MappingError,
    ReviewAction,
    ReviewEvidence,
    ReviewStep,
    SongRef,
)
from chuni_penguin.providers.models import ExternalChartRef, ExternalSongRef


def fail(reason: str) -> Never:
    raise MappingError(reason)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            fail("duplicate_review_json_key")
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    with path.open("rb") as handle:
        data = handle.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        fail("review_json_size_limit")
    try:
        return json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: fail("nonfinite_review_json_number"),
        )
    except (ValueError, UnicodeError):
        fail("invalid_review_json")


def object_keys(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        fail("invalid_review_json_fields")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        fail("invalid_review_string")
    return value


def _integer(value: Any, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        fail("invalid_review_integer")
    return value


def _song(value: Any) -> ExternalSongRef:
    obj = object_keys(value, {"namespace", "id"})
    return ExternalSongRef(_string(obj["namespace"]), _string(obj["id"]))


def parse_steps(value: Any) -> tuple[ReviewStep, ...]:
    if not isinstance(value, list) or len(value) > 1000:
        fail("review_step_limit")
    steps = []
    for raw in value:
        obj = object_keys(
            raw, {"kind", "action", "ref", "target", "expected_revision", "evidence"}
        )
        try:
            action = ReviewAction(_string(obj["action"]))
        except ValueError:
            fail("invalid_review_action")
        kind = obj["kind"]
        if kind == "song":
            ref: ExternalSongRef | ExternalChartRef = _song(obj["ref"])
        elif kind == "chart":
            chart = object_keys(
                obj["ref"], {"song", "difficulty", "variant", "chart_id"}
            )
            native = chart["chart_id"]
            ref = ExternalChartRef(
                _song(chart["song"]),
                _string(chart["difficulty"]),
                _string(chart["variant"]),
                None if native is None else _string(native),
            )
        else:
            fail("invalid_review_kind")
        if action == ReviewAction.REVOKE:
            if obj["target"] is not None:
                fail("invalid_review_target")
            target = None
        elif kind == "song":
            raw_target = object_keys(obj["target"], {"song_id"})
            target = SongRef(_integer(raw_target["song_id"], 0))
        else:
            raw_target = object_keys(
                obj["target"], {"chart_id", "song_id", "difficulty"}
            )
            target = ChartRef(
                _integer(raw_target["chart_id"], 1),
                _integer(raw_target["song_id"], 0),
                _string(raw_target["difficulty"]),
            )
        evidence = object_keys(
            obj["evidence"], {"actor", "reason", "reference", "sha256"}
        )
        review = ReviewEvidence(**{k: _string(v) for k, v in evidence.items()})
        review.validate()
        steps.append(
            ReviewStep(
                action, ref, target, _integer(obj["expected_revision"], 1), review
            )
        )
    return tuple(steps)


def decisions(path: Path | None) -> tuple[ReviewStep, ...]:
    if path is None:
        return ()
    obj = object_keys(read_json(path), {"schema_version", "operations"})
    if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
        fail("unsupported_review_schema")
    return parse_steps(obj["operations"])


def serialize_steps(steps: tuple[ReviewStep, ...]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "song" if isinstance(step.ref, ExternalSongRef) else "chart",
            "action": step.action.value,
            "ref": asdict(step.ref),
            "target": None if step.target is None else asdict(step.target),
            "expected_revision": step.expected_revision,
            "evidence": asdict(step.evidence),
        }
        for step in steps
    ]
