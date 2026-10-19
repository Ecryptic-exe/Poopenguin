"""Pure dry-run review candidates. A matching title never confirms identity."""

from collections import defaultdict
from dataclasses import dataclass

from chuni_penguin.identities import ChartRef, MappingError, SongRef
from chuni_penguin.providers.models import (
    CatalogSnapshot,
    ExternalChartRef,
    ExternalSongRef,
)


@dataclass(frozen=True)
class SongCandidate:
    source: ExternalSongRef
    status: str
    targets: tuple[SongRef, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ChartCandidate:
    source: ExternalChartRef
    status: str
    targets: tuple[ChartRef, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateReport:
    source_revision: str
    local_revision: str
    songs: tuple[SongCandidate, ...]
    charts: tuple[ChartCandidate, ...]
    automatically_confirmed: int = 0


def plan_candidates(source: CatalogSnapshot, local: CatalogSnapshot) -> CandidateReport:
    if (
        local.provenance.provider_key != "a_catalog"
        or local.provenance.source_namespace != "a_local"
        or any(s.ref.namespace != "a_local" for s in local.songs)
    ):
        reason = "canonical_catalog_required"
        raise MappingError(reason)
    by_title = defaultdict(list)
    ids = {s.ref.id for s in local.songs}
    for song in local.songs:
        by_title[song.title].append(song)
    songs, charts = [], []
    for song in source.songs:
        matches = by_title[song.title]
        targets = tuple(SongRef(int(s.ref.id)) for s in matches)
        reasons = ["exact_title_requires_review"] if matches else ["no_title_candidate"]
        if song.ref.id in ids:
            reasons.append("numeric_equality_is_not_evidence")
        if any(song.artist != s.artist for s in matches):
            reasons.append("artist_difference")
        status = (
            "candidate"
            if len(matches) == 1
            else "conflict"
            if matches
            else "unresolved"
        )
        songs.append(SongCandidate(song.ref, status, targets, tuple(reasons)))
        for chart in song.charts:
            chart_targets = []
            chart_reasons = ["confirmed_parent_and_chart_review_required"]
            if chart.ref.variant != "default" or chart.ref.difficulty not in {
                "BAS",
                "ADV",
                "EXP",
                "MAS",
                "ULT",
            }:
                chart_reasons.append("unsupported_chart_identity")
            else:
                for match in matches:
                    for candidate in match.charts:
                        if (
                            candidate.ref.difficulty == chart.ref.difficulty
                            and candidate.ref.variant == "default"
                            and candidate.ref.chart_id is not None
                        ):
                            chart_targets.append(
                                ChartRef(
                                    int(candidate.ref.chart_id),
                                    int(match.ref.id),
                                    candidate.ref.difficulty,
                                )
                            )
                            if candidate.constant != chart.constant:
                                chart_reasons.append("constant_difference")
            chart_status = (
                "candidate"
                if len(chart_targets) == 1 and status == "candidate"
                else "conflict"
                if chart_targets
                else "unresolved"
            )
            charts.append(
                ChartCandidate(
                    chart.ref,
                    chart_status,
                    tuple(chart_targets),
                    tuple(dict.fromkeys(chart_reasons)),
                )
            )
    return CandidateReport(
        source.provenance.revision,
        local.provenance.revision,
        tuple(songs),
        tuple(charts),
    )
