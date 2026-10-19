"""Read source observations without guessing canonical IDs or requiring a DB."""

from dataclasses import dataclass

from chuni_penguin.identities import CatalogLookupError, LookupFailure
from chuni_penguin.providers.models import (
    CatalogChart,
    CatalogRequest,
    CatalogSnapshot,
    CatalogSong,
    ExternalSongRef,
)
from chuni_penguin.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class CatalogMatches:
    snapshot: CatalogSnapshot
    songs: tuple[CatalogSong, ...]
    total: int


@dataclass(frozen=True)
class SourceSong:
    snapshot: CatalogSnapshot
    song: CatalogSong


@dataclass(frozen=True)
class SourceChart:
    snapshot: CatalogSnapshot
    song: CatalogSong
    chart: CatalogChart


class CatalogQueryService:
    def __init__(self, providers: ProviderRegistry):
        self.providers = providers

    async def search(
        self, query: str, request: CatalogRequest, *, limit: int = 5
    ) -> CatalogMatches:
        query = query.strip().casefold()
        if not query or len(query) > 200 or not 1 <= limit <= 10:
            msg = "Use a title of 1-200 characters and a limit of 1-10."
            raise ValueError(msg)
        snapshot = await self.providers.read_catalog(request)
        # Exact titles rank first; duplicate titles remain separate source rows.
        matches = sorted(
            (song for song in snapshot.songs if query in song.title.casefold()),
            key=lambda song: (
                song.title.casefold() != query,
                song.title.casefold(),
                song.ref.id,
            ),
        )
        return CatalogMatches(snapshot, tuple(matches[:limit]), len(matches))

    async def song(self, song_id: str, request: CatalogRequest) -> SourceSong:
        snapshot = await self.providers.read_catalog(request)
        ref = ExternalSongRef(snapshot.provenance.source_namespace, song_id)
        matches = [song for song in snapshot.songs if song.ref == ref]
        if not matches:
            raise CatalogLookupError(LookupFailure.SOURCE_REF_MISSING)
        if len(matches) != 1:
            raise CatalogLookupError(LookupFailure.SOURCE_REF_AMBIGUOUS)
        return SourceSong(snapshot, matches[0])

    async def chart(
        self,
        song_id: str,
        difficulty: str,
        request: CatalogRequest,
        *,
        variant: str = "default",
        chart_id: str | None = None,
    ) -> SourceChart:
        result = await self.song(song_id, request)
        matches = [
            chart
            for chart in result.song.charts
            if chart.ref.difficulty == difficulty
            and chart.ref.variant == variant
            and (chart_id is None or chart.ref.chart_id == chart_id)
        ]
        if not matches:
            raise CatalogLookupError(LookupFailure.SOURCE_REF_MISSING)
        if len(matches) != 1:
            raise CatalogLookupError(LookupFailure.SOURCE_REF_AMBIGUOUS)
        return SourceChart(result.snapshot, result.song, matches[0])
