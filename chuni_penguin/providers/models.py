"""Read-only source observations, not canonical Song/Chart database replacements."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class Capability(StrEnum):
    CATALOG_READ = "catalog_read"


@dataclass(frozen=True)
class CatalogScope:
    region: str
    game_version: str


@dataclass(frozen=True)
class ExternalSongRef:
    namespace: str
    id: str


@dataclass(frozen=True)
class ExternalChartRef:
    song: ExternalSongRef
    difficulty: str
    variant: str = "default"
    # A has a distinct Chart PK; Chunilib has only the observed composite key.
    chart_id: str | None = None


@dataclass(frozen=True)
class CatalogChart:
    ref: ExternalChartRef
    constant: Decimal | None
    level: str | None = None


@dataclass(frozen=True)
class CatalogSong:
    ref: ExternalSongRef
    title: str
    artist: str | None
    charts: tuple[CatalogChart, ...]


@dataclass(frozen=True)
class SourceFile:
    file: str
    sha256: str
    url: str | None
    mode: str


@dataclass(frozen=True)
class Provenance:
    provider_key: str
    source_namespace: str
    scope: CatalogScope
    data_version: str | None
    revision: str
    observed_at: datetime
    observation_kind: str
    source_updated_at: datetime | None = None
    files: tuple[SourceFile, ...] = ()


@dataclass(frozen=True)
class CatalogIssue:
    reason: str
    source_pointer: str
    song_id: str | None = None
    difficulty: str | None = None


@dataclass(frozen=True)
class CatalogSnapshot:
    songs: tuple[CatalogSong, ...]
    provenance: Provenance
    issues: tuple[CatalogIssue, ...] = ()
    stale: bool = False


@dataclass(frozen=True)
class ProviderDescriptor:
    key: str
    source_namespace: str
    scope: CatalogScope
    evidence: tuple[str, ...]
    capabilities: frozenset[Capability] = frozenset({Capability.CATALOG_READ})
    auth_mode: str = "local_read"


@dataclass(frozen=True)
class CatalogRequest:
    scope: CatalogScope
    provider_key: str | None = None
    # Caller supplies a resolved feature/user preference; no implicit DB writes.
    preferred_provider: str | None = None
    max_age: timedelta | None = None
    allow_stale: bool = False
