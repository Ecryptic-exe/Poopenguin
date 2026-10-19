"""Shared annotations; source rows and confirmed canonical results stay distinct."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from chuni_penguin.identities import (
    CanonicalChart,
    CanonicalSong,
    MappingError,
    ReviewEvidence,
)
from chuni_penguin.providers.models import ExternalChartRef, Provenance
from chuni_penguin.repositories.chart_knowledge import (
    AnnotationBindings,
    ChartKnowledgeRepository,
    KnowledgeError,
    ManualAnnotation,
)
from chuni_penguin.repositories.semantic_knowledge import (
    SemanticKnowledgeRepository,
    SemanticObservation,
)
from chuni_penguin.services.mapped_catalog import MappedChart


@dataclass(frozen=True)
class BoundAnnotation:
    song: CanonicalSong
    chart: CanonicalChart
    annotation: ManualAnnotation
    evidence: ReviewEvidence
    mapping_revision: int
    song_mapping_revision: int
    binding_revision: str
    source_ref: ExternalChartRef
    catalog_provenance: Provenance
    stale: bool


class ChartKnowledgeService:
    def __init__(
        self,
        repositories: tuple[ChartKnowledgeRepository, ...],
        bindings: AnnotationBindings | None = None,
        *,
        semantic_repositories: tuple[SemanticKnowledgeRepository, ...] = (),
        source_failures: tuple[tuple[str, str], ...] = (),
    ):
        self.repositories = repositories
        self.rows = MappingProxyType(
            {row.row_id: row for repo in repositories for row in repo.rows}
        )
        self.bindings = bindings
        self.semantic_repositories = semantic_repositories
        self.semantic_rows = MappingProxyType(
            {row.row_id: row for repo in semantic_repositories for row in repo.rows}
        )
        self.source_failures = source_failures

    @classmethod
    def load(
        cls,
        manual: Path | None,
        bindings: Path | None,
        semantic_root: Path | None = None,
        semantic_catalogue_id: str | None = None,
    ):
        repositories = ()
        semantic_repositories = ()
        failures = []
        reviewed = None
        if manual is not None:
            try:
                repository = ChartKnowledgeRepository(manual)
            except KnowledgeError as exc:
                failures.append(("poopenguin_manual", str(exc)))
            else:
                repositories = (repository,)
        if bindings is not None:
            try:
                # Schema 1 bindings apply exclusively to legacy manual rows.
                reviewed = AnnotationBindings(
                    bindings,
                    {row.row_id: row for repo in repositories for row in repo.rows},
                )
            except (KnowledgeError, MappingError, ValueError):
                # Invalid reviews never affect unbound source observations.
                failures.append(("manual_bindings", "invalid_annotation_bindings"))
        if semantic_root is not None or semantic_catalogue_id is not None:
            if semantic_root is None or semantic_catalogue_id is None:
                failures.append(("v3_semantic", "semantic_explicit_catalogue_required"))
            else:
                try:
                    semantic_repositories = (
                        SemanticKnowledgeRepository(
                            semantic_root, semantic_catalogue_id
                        ),
                    )
                except KnowledgeError as exc:
                    failures.append(("v3_semantic", str(exc)))
        if not repositories and not semantic_repositories:
            msg = "no_readable_annotation_source"
            raise KnowledgeError(msg)
        return cls(
            repositories,
            reviewed,
            semantic_repositories=semantic_repositories,
            source_failures=tuple(failures),
        )

    def search_semantics(
        self, query: str, limit: int = 5
    ) -> tuple[SemanticObservation, ...]:
        """Keep source namespaces and historical analysis identities separate."""
        needle = query.strip().casefold()
        if not needle:
            return ()
        found = [
            row for row in self.semantic_rows.values() if needle in row.title.casefold()
        ]
        found.sort(
            key=lambda row: (
                row.title.casefold() != needle,
                row.title,
                not row.is_latest,
                row.row_id,
            )
        )
        return tuple(found[: min(10, max(1, limit))])

    def search(self, query: str, limit: int = 5) -> tuple[ManualAnnotation, ...]:
        needle = query.strip().casefold()
        if not needle:
            return ()
        found = [
            row for row in self.rows.values() if needle in (row.title or "").casefold()
        ]
        found.sort(
            key=lambda row: (
                row.title.casefold() != needle if row.title else True,
                row.title or "",
                row.row_id,
            )
        )
        return tuple(found[: min(10, max(1, limit))])

    def for_chart(self, mapped: MappedChart) -> tuple[BoundAnnotation, ...]:
        """Only call after a fresh confirmed Mapping lookup, never a title join."""
        if self.bindings is None:
            return ()
        origin = mapped.provenance
        pin = {
            "provider_key": origin.provider_key,
            "namespace": origin.source_namespace,
            "region": origin.scope.region,
            "game_version": origin.scope.game_version,
            "revision": origin.revision,
            "source_id": mapped.mapping.source_snapshot_id,
        }
        if pin != self.bindings.catalog:
            msg = "annotation_catalog_pin_mismatch"
            raise KnowledgeError(msg)
        return tuple(
            BoundAnnotation(
                mapped.mapping.song,
                mapped.mapping.chart,
                self.rows[binding.row_id],
                binding.evidence,
                mapped.mapping.mapping_revision,
                mapped.mapping.song_mapping_revision,
                self.bindings.revision,
                mapped.source.ref,
                mapped.provenance,
                mapped.stale,
            )
            for binding in self.bindings.entries
            if binding.ref == mapped.source.ref
        )
