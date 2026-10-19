"""Canonical references and exact external identity serialization."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from chuni_penguin.providers.models import (
    CatalogScope,
    CatalogSnapshot,
    ExternalChartRef,
    ExternalSongRef,
)


class MappingError(Exception):
    """Safe reason code, with no raw evidence or database error text."""


def encode(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def snapshot_identity(snapshot: CatalogSnapshot) -> tuple[str, str]:
    origin = snapshot.provenance
    return (
        digest(
            [
                origin.provider_key,
                origin.source_namespace,
                scope_key(origin.scope),
                origin.revision,
            ]
        ),
        digest([asdict(song) for song in snapshot.songs]),
    )


def scope_key(scope: CatalogScope) -> str:
    if not scope.region or not scope.game_version:
        reason = "empty_scope"
        raise MappingError(reason)
    return encode(asdict(scope))


def song_key(ref: ExternalSongRef) -> tuple[str, str]:
    if (
        not isinstance(ref.namespace, str)
        or not ref.namespace
        or not isinstance(ref.id, str)
        or not ref.id
    ):
        reason = "invalid_external_ref"
        raise MappingError(reason)
    return ref.namespace, ref.id


def chart_key(ref: ExternalChartRef) -> str:
    song_key(ref.song)
    if (
        not ref.difficulty
        or not ref.variant
        or (ref.chart_id is not None and not isinstance(ref.chart_id, str))
    ):
        reason = "invalid_chart_ref"
        raise MappingError(reason)
    return encode([ref.song.id, ref.difficulty, ref.variant, ref.chart_id])


@dataclass(frozen=True)
class SongRef:
    song_id: int

    def __post_init__(self):
        if type(self.song_id) is not int or self.song_id < 0:
            reason = "invalid_canonical_song_ref"
            raise MappingError(reason)


@dataclass(frozen=True)
class ChartRef:
    chart_id: int
    song_id: int
    difficulty: str

    def __post_init__(self):
        SongRef(self.song_id)
        if (
            type(self.chart_id) is not int
            or self.chart_id < 1
            or not isinstance(self.difficulty, str)
            or not self.difficulty
        ):
            reason = "invalid_canonical_chart_ref"
            raise MappingError(reason)


@dataclass(frozen=True)
class ResolvedSong:
    target: SongRef
    mapping_revision: int


@dataclass(frozen=True)
class ResolvedChart:
    target: ChartRef
    mapping_revision: int
    song_mapping_revision: int


@dataclass(frozen=True)
class CanonicalSong:
    ref: SongRef
    title: str
    artist: str
    # These flags describe A's catalog, not availability in the source scope.
    available: bool
    removed: bool


@dataclass(frozen=True)
class CanonicalChart:
    ref: ChartRef
    level: str
    constant: Decimal | None
    available: bool


@dataclass(frozen=True)
class SongMappingView:
    song: CanonicalSong
    mapping_revision: int
    source_snapshot_id: str


@dataclass(frozen=True)
class ChartMappingView:
    song: CanonicalSong
    chart: CanonicalChart
    mapping_revision: int
    song_mapping_revision: int
    source_snapshot_id: str


class LookupFailure(StrEnum):
    UNRESOLVED = "unresolved"
    SOURCE_MISMATCH = "source_mismatch"
    SOURCE_REF_MISSING = "source_ref_missing"
    SOURCE_REF_AMBIGUOUS = "source_ref_ambiguous"
    NAMESPACE_MISMATCH = "namespace_mismatch"
    INVALID_TARGET = "invalid_target"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    TIMEOUT = "timeout"
    CLOSED = "closed"


class CatalogLookupError(MappingError):
    """Stable, safe failures for a source-aware consumer; no raw SQL/evidence."""

    MESSAGES: ClassVar[dict[str, str]] = {
        "unresolved": "This source identity has no confirmed mapping.",
        "source_mismatch": "The selected source observation has not been verified in this mapping store.",
        "source_ref_missing": "The selected catalog does not contain this identity.",
        "source_ref_ambiguous": "The selected catalog contains conflicting identities.",
        "namespace_mismatch": "This identity belongs to a different source namespace.",
        "invalid_target": "The confirmed mapping no longer matches its catalog target.",
        "storage_unavailable": "The mapping store is unavailable or has not been migrated.",
        "timeout": "The catalog lookup timed out. Please retry later.",
        "closed": "The catalog lookup service is closed.",
    }

    def __init__(self, reason: LookupFailure):
        self.reason = reason
        self.message = self.MESSAGES[reason]
        super().__init__(reason)


@dataclass(frozen=True)
class ReviewEvidence:
    actor: str
    reason: str
    reference: str
    sha256: str

    def validate(self):
        if not all(
            isinstance(v, str) and v.strip()
            for v in (self.actor, self.reason, self.reference, self.sha256)
        ) or not re.fullmatch("[0-9a-f]{64}", self.sha256):
            reason = "review_evidence_required"
            raise MappingError(reason)


class ReviewAction(StrEnum):
    PROPOSE = "propose"
    CONFIRM = "confirm"
    REVOKE = "revoke"


@dataclass(frozen=True)
class ReviewStep:
    action: ReviewAction
    ref: ExternalSongRef | ExternalChartRef
    target: SongRef | ChartRef | None
    expected_revision: int
    evidence: ReviewEvidence
