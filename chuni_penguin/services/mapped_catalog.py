"""Opt-in source-aware reads. No imports, confirmations, uploads or Cog access."""

import asyncio
from dataclasses import dataclass

from chuni_penguin.identities import (
    CatalogLookupError,
    ChartMappingView,
    LookupFailure,
    SongMappingView,
)
from chuni_penguin.providers.models import (
    CatalogChart,
    CatalogRequest,
    CatalogSnapshot,
    CatalogSong,
    ExternalChartRef,
    ExternalSongRef,
    Provenance,
)
from chuni_penguin.providers.registry import ProviderRegistry
from chuni_penguin.repositories.mapped_catalog import MappedCatalogRepository


@dataclass(frozen=True)
class MappedSong:
    source: CatalogSong
    mapping: SongMappingView
    provenance: Provenance
    stale: bool


@dataclass(frozen=True)
class MappedChart:
    source_song: CatalogSong
    source: CatalogChart
    mapping: ChartMappingView
    provenance: Provenance
    stale: bool


def _source_song(ref: ExternalSongRef, snapshot: CatalogSnapshot) -> CatalogSong:
    if ref.namespace != snapshot.provenance.source_namespace:
        raise CatalogLookupError(LookupFailure.NAMESPACE_MISMATCH)
    matches = [song for song in snapshot.songs if song.ref == ref]
    if not matches:
        raise CatalogLookupError(LookupFailure.SOURCE_REF_MISSING)
    if len(matches) != 1:
        raise CatalogLookupError(LookupFailure.SOURCE_REF_AMBIGUOUS)
    return matches[0]


class MappedCatalogService:
    def __init__(
        self, providers: ProviderRegistry, repository: MappedCatalogRepository
    ):
        self._providers, self._repository = providers, repository
        self._closed = False
        self._consumers: set[asyncio.Task] = set()

    async def song(self, ref: ExternalSongRef, request: CatalogRequest) -> MappedSong:
        result = await self._lookup(ref, request)
        assert isinstance(result, MappedSong)
        return result

    async def chart(
        self, ref: ExternalChartRef, request: CatalogRequest
    ) -> MappedChart:
        result = await self._lookup(ref, request)
        assert isinstance(result, MappedChart)
        return result

    async def _lookup(
        self, ref: ExternalSongRef | ExternalChartRef, request: CatalogRequest
    ) -> MappedSong | MappedChart:
        if self._closed:
            raise CatalogLookupError(LookupFailure.CLOSED)
        task = asyncio.current_task()
        if task is not None:
            self._consumers.add(task)
        try:
            async with asyncio.timeout(self._providers.timeout):
                snapshot = await self._providers.read_catalog(request)
                song_ref = ref.song if isinstance(ref, ExternalChartRef) else ref
                source_song = _source_song(song_ref, snapshot)
                if isinstance(ref, ExternalSongRef):
                    mapping = await self._repository.song(ref, snapshot)
                    return MappedSong(
                        source_song, mapping, snapshot.provenance, snapshot.stale
                    )
                charts = [chart for chart in source_song.charts if chart.ref == ref]
                if not charts:
                    raise CatalogLookupError(LookupFailure.SOURCE_REF_MISSING)
                if len(charts) != 1:
                    raise CatalogLookupError(LookupFailure.SOURCE_REF_AMBIGUOUS)
                chart_mapping = await self._repository.chart(ref, snapshot)
                return MappedChart(
                    source_song,
                    charts[0],
                    chart_mapping,
                    snapshot.provenance,
                    snapshot.stale,
                )
        except TimeoutError:
            raise CatalogLookupError(LookupFailure.TIMEOUT) from None
        finally:
            if task is not None:
                self._consumers.discard(task)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        consumers = [
            task for task in self._consumers if task is not asyncio.current_task()
        ]
        for task in consumers:
            task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
