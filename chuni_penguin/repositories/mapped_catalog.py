"""Read a confirmed mapping and canonical target in one SQL statement."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chuni_penguin.database.identity import (
    ExternalChartID,
    ExternalSongID,
    SourceSnapshot,
)
from chuni_penguin.database.songs import Chart, Song
from chuni_penguin.identities import (
    CanonicalChart,
    CanonicalSong,
    CatalogLookupError,
    ChartMappingView,
    ChartRef,
    LookupFailure,
    SongMappingView,
    SongRef,
    chart_key,
    scope_key,
    snapshot_identity,
    song_key,
)
from chuni_penguin.providers.models import (
    CatalogSnapshot,
    ExternalChartRef,
    ExternalSongRef,
)


def _song(song: Song) -> CanonicalSong:
    return CanonicalSong(
        SongRef(song.id), song.title, song.artist, song.available, song.removed
    )


def _source(source: SourceSnapshot | None, identity: tuple[str, str]):
    if source is None or (source.id, source.payload_sha256) != identity:
        raise CatalogLookupError(LookupFailure.SOURCE_MISMATCH)


class MappedCatalogRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def song(
        self, ref: ExternalSongRef, snapshot: CatalogSnapshot
    ) -> SongMappingView:
        namespace, external_id = song_key(ref)
        identity = snapshot_identity(snapshot)
        statement = (
            select(ExternalSongID, Song, SourceSnapshot)
            .outerjoin(Song, Song.id == ExternalSongID.song_id)
            .outerjoin(
                SourceSnapshot, SourceSnapshot.id == ExternalSongID.source_snapshot_id
            )
            .where(
                ExternalSongID.namespace == namespace,
                ExternalSongID.external_id == external_id,
                ExternalSongID.scope_key == scope_key(snapshot.provenance.scope),
            )
        )
        try:
            async with self._sessions() as session:
                row = (await session.execute(statement)).one_or_none()
                if row is None or row[0].status != "confirmed":
                    raise CatalogLookupError(LookupFailure.UNRESOLVED)
                entry, song, source = row
                _source(source, identity)
                if song is None:
                    raise CatalogLookupError(LookupFailure.INVALID_TARGET)
                return SongMappingView(_song(song), entry.revision, identity[0])
        except SQLAlchemyError:
            raise CatalogLookupError(LookupFailure.STORAGE_UNAVAILABLE) from None

    async def chart(
        self, ref: ExternalChartRef, snapshot: CatalogSnapshot
    ) -> ChartMappingView:
        namespace, external_id = song_key(ref.song)
        identity = snapshot_identity(snapshot)
        statement = (
            select(ExternalChartID, ExternalSongID, Chart, Song, SourceSnapshot)
            .join(ExternalSongID, ExternalSongID.id == ExternalChartID.external_song_id)
            .outerjoin(Chart, Chart.id == ExternalChartID.chart_id)
            .outerjoin(Song, Song.id == ExternalSongID.song_id)
            .outerjoin(
                SourceSnapshot, SourceSnapshot.id == ExternalChartID.source_snapshot_id
            )
            .where(
                ExternalSongID.namespace == namespace,
                ExternalSongID.external_id == external_id,
                ExternalSongID.scope_key == scope_key(snapshot.provenance.scope),
                ExternalChartID.external_key == chart_key(ref),
            )
        )
        try:
            async with self._sessions() as session:
                row = (await session.execute(statement)).one_or_none()
                if (
                    row is None
                    or row[0].status != "confirmed"
                    or row[1].status != "confirmed"
                ):
                    raise CatalogLookupError(LookupFailure.UNRESOLVED)
                entry, parent, chart, song, source = row
                _source(source, identity)
                if parent.source_snapshot_id != identity[0]:
                    raise CatalogLookupError(LookupFailure.SOURCE_MISMATCH)
                if (
                    chart is None
                    or song is None
                    or chart.song_id != song.id
                    or chart.difficulty != ref.difficulty
                    or entry.difficulty != ref.difficulty
                    or entry.variant != "default"
                    or ref.variant != "default"
                    or ref.difficulty not in {"BAS", "ADV", "EXP", "MAS", "ULT"}
                ):
                    raise CatalogLookupError(LookupFailure.INVALID_TARGET)
                canonical = CanonicalChart(
                    ChartRef(chart.id, song.id, chart.difficulty),
                    chart.level,
                    Decimal(str(chart.const)) if chart.const is not None else None,
                    chart.available,
                )
                return ChartMappingView(
                    _song(song), canonical, entry.revision, parent.revision, identity[0]
                )
        except SQLAlchemyError:
            raise CatalogLookupError(LookupFailure.STORAGE_UNAVAILABLE) from None
