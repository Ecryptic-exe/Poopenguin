"""Explicit identity review using the existing writer transaction and read pool."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Never, Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chuni_penguin.database.identity import (
    ExternalChartID,
    ExternalSongID,
    IDMappingEvent,
    SourceSnapshot,
)
from chuni_penguin.database.songs import Chart, Song
from chuni_penguin.identities import (
    ChartRef,
    MappingError,
    ResolvedChart,
    ResolvedSong,
    ReviewAction,
    ReviewEvidence,
    ReviewStep,
    SongRef,
    chart_key,
    encode,
    scope_key,
    snapshot_identity,
    song_key,
)
from chuni_penguin.providers.models import (
    CatalogScope,
    CatalogSnapshot,
    ExternalChartRef,
    ExternalSongRef,
)


class MappingWriter(Protocol):
    def execute_fn[T](
        self, operation: Callable[[AsyncSession], Awaitable[T]], /
    ) -> Awaitable[T]: ...


class _TransactionWriter:
    def __init__(self, session: AsyncSession):
        self.session = session

    def execute_fn[T](
        self, operation: Callable[[AsyncSession], Awaitable[T]]
    ) -> Awaitable[T]:
        return operation(self.session)


ExternalRef = ExternalSongRef | ExternalChartRef
Entry = ExternalSongID | ExternalChartID
Target = SongRef | ChartRef
STANDARD = {"BAS", "ADV", "EXP", "MAS", "ULT"}


def _fail(reason: str) -> Never:
    raise MappingError(reason)


@dataclass(frozen=True)
class MappingState:
    status: str
    revision: int


@dataclass(frozen=True)
class ImportResult:
    source_snapshot_id: str
    songs_created: int
    charts_created: int
    observations_added: int


@dataclass(frozen=True)
class MappingHistory:
    revision: int
    action: str
    song_id: int | None
    chart_id: int | None
    source_snapshot_id: str
    evidence_json: str


@dataclass(frozen=True)
class ReviewEntryState:
    status: str
    revision: int
    song_id: int | None
    chart_id: int | None
    source_snapshot_id: str


@dataclass(frozen=True)
class CascadeRevocation:
    external_key: str
    chart_id: int | None
    before_revision: int
    after_revision: int


@dataclass(frozen=True)
class ReviewStepResult:
    before: ReviewEntryState
    after: ReviewEntryState
    revoked_children: tuple[CascadeRevocation, ...]


@dataclass(frozen=True)
class ReviewBatchResult:
    imported: ImportResult
    steps: tuple[ReviewStepResult, ...]


def _review_state(entry: Entry) -> ReviewEntryState:
    return ReviewEntryState(
        entry.status,
        entry.revision,
        entry.song_id if isinstance(entry, ExternalSongID) else None,
        entry.chart_id if isinstance(entry, ExternalChartID) else None,
        entry.source_snapshot_id,
    )


class IdentityMappingRepository:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], writer: MappingWriter
    ):
        self._sessions, self._writer = sessions, writer

    async def review_catalog(
        self, snapshot: CatalogSnapshot, steps: tuple[ReviewStep, ...]
    ) -> ReviewBatchResult:
        """Observe and run explicit review steps in one existing writer transaction."""
        if len(steps) > 1000:
            _fail("review_step_limit")
        refs = {
            ref
            for song in snapshot.songs
            for ref in (song.ref, *(chart.ref for chart in song.charts))
        }
        for step in steps:
            if step.ref not in refs or not isinstance(step.action, ReviewAction):
                _fail("invalid_review_step")
            if (step.action == ReviewAction.REVOKE) != (step.target is None):
                _fail("invalid_review_target")
            step.evidence.validate()

        async def operation(session: AsyncSession):
            transaction = IdentityMappingRepository(
                self._sessions, _TransactionWriter(session)
            )
            imported = await transaction.observe_catalog(snapshot)
            results = []
            for step in steps:
                entry = self._expected(
                    await self._entry(session, step.ref, snapshot.provenance.scope),
                    step.expected_revision,
                )
                before = _review_state(entry)
                children = []
                if step.action == ReviewAction.REVOKE and isinstance(
                    entry, ExternalSongID
                ):
                    entries = await session.scalars(
                        select(ExternalChartID)
                        .where(
                            ExternalChartID.external_song_id == entry.id,
                            ExternalChartID.status == "confirmed",
                        )
                        .order_by(ExternalChartID.id)
                    )
                    children = [
                        (child, child.chart_id, child.revision) for child in entries
                    ]
                if step.action == ReviewAction.REVOKE:
                    await transaction.revoke(
                        step.ref,
                        snapshot.provenance.scope,
                        expected_revision=step.expected_revision,
                        review=step.evidence,
                    )
                else:
                    assert step.target is not None
                    if step.action == ReviewAction.PROPOSE:
                        await transaction.propose(
                            step.ref,
                            snapshot.provenance.scope,
                            step.target,
                            expected_revision=step.expected_revision,
                            evidence=step.evidence,
                        )
                    else:
                        await transaction.confirm(
                            step.ref,
                            snapshot.provenance.scope,
                            step.target,
                            expected_revision=step.expected_revision,
                            review=step.evidence,
                        )
                results.append(
                    ReviewStepResult(
                        before,
                        _review_state(entry),
                        tuple(
                            CascadeRevocation(
                                child.external_key, chart_id, revision, child.revision
                            )
                            for child, chart_id, revision in children
                        ),
                    )
                )
            return ReviewBatchResult(imported, tuple(results))

        return await self._write(operation)

    @asynccontextmanager
    async def _read(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions() as session:
                yield session
        except SQLAlchemyError:
            reason = "mapping_schema_or_storage_unavailable"
            raise MappingError(reason) from None

    async def _write[T](self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
        try:
            return await self._writer.execute_fn(operation)
        except SQLAlchemyError:
            reason = "mapping_schema_or_storage_unavailable"
            raise MappingError(reason) from None

    @staticmethod
    async def _song(
        session: AsyncSession, ref: ExternalSongRef, scope: CatalogScope
    ) -> ExternalSongID | None:
        namespace, external_id = song_key(ref)
        return await session.scalar(
            select(ExternalSongID).where(
                ExternalSongID.namespace == namespace,
                ExternalSongID.external_id == external_id,
                ExternalSongID.scope_key == scope_key(scope),
            )
        )

    async def _entry(
        self, session: AsyncSession, ref: ExternalRef, scope: CatalogScope
    ) -> Entry | None:
        parent = await self._song(
            session, ref.song if isinstance(ref, ExternalChartRef) else ref, scope
        )
        if parent is None or isinstance(ref, ExternalSongRef):
            return parent
        return await session.scalar(
            select(ExternalChartID).where(
                ExternalChartID.external_song_id == parent.id,
                ExternalChartID.external_key == chart_key(ref),
            )
        )

    @staticmethod
    def _event(
        session: AsyncSession,
        entry: Entry,
        action: str,
        evidence: ReviewEvidence,
        target: Target | None = None,
    ):
        session.add(
            IDMappingEvent(
                external_song_id=entry.id
                if isinstance(entry, ExternalSongID)
                else None,
                external_chart_id=entry.id
                if isinstance(entry, ExternalChartID)
                else None,
                revision=entry.revision,
                action=action,
                song_id=target.song_id if isinstance(target, SongRef) else None,
                chart_id=target.chart_id if isinstance(target, ChartRef) else None,
                source_snapshot_id=entry.source_snapshot_id,
                evidence_json=encode(asdict(evidence)),
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _event_query(entry: Entry):
        column = (
            IDMappingEvent.external_song_id
            if isinstance(entry, ExternalSongID)
            else IDMappingEvent.external_chart_id
        )
        return select(IDMappingEvent).where(column == entry.id)

    async def observe_catalog(self, snapshot: CatalogSnapshot) -> ImportResult:
        origin = snapshot.provenance
        scope = scope_key(origin.scope)
        review = ReviewEvidence(
            "catalog_import",
            "Observed source identity; no canonical match",
            "source_revision:" + origin.revision,
            origin.revision,
        )
        review.validate()
        if (
            not origin.provider_key
            or not origin.source_namespace
            or origin.observed_at.utcoffset() is None
        ):
            _fail("invalid_provenance")
        refs, chart_refs = set(), set()
        for song in snapshot.songs:
            song_key(song.ref)
            if song.ref.namespace != origin.source_namespace or song.ref in refs:
                _fail("invalid_or_duplicate_song_ref")
            refs.add(song.ref)
            for chart in song.charts:
                chart_key(chart.ref)
                if chart.ref.song != song.ref or chart.ref in chart_refs:
                    _fail("invalid_or_duplicate_chart_ref")
                chart_refs.add(chart.ref)
        source_id, payload_hash = snapshot_identity(snapshot)

        async def operation(session: AsyncSession):
            previous_source = await session.get(SourceSnapshot, source_id)
            if (
                previous_source is not None
                and previous_source.payload_sha256 != payload_hash
            ):
                _fail("source_revision_content_conflict")
            if previous_source is None:
                session.add(
                    SourceSnapshot(
                        id=source_id,
                        provider_key=origin.provider_key,
                        namespace=origin.source_namespace,
                        scope_key=scope,
                        revision=origin.revision,
                        payload_sha256=payload_hash,
                        provenance_json=encode(asdict(origin)),
                        observed_at=origin.observed_at,
                        imported_at=datetime.now(UTC),
                    )
                )
                await session.flush()
            songs_created = charts_created = observations = 0
            for song in snapshot.songs:
                parent = await self._song(session, song.ref, origin.scope)
                if parent is None:
                    parent = ExternalSongID(
                        namespace=song.ref.namespace,
                        external_id=song.ref.id,
                        scope_key=scope,
                        song_id=None,
                        status="unresolved",
                        revision=1,
                        source_snapshot_id=source_id,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    session.add(parent)
                    await session.flush()
                    songs_created += 1
                observations += await self._observe(session, parent, source_id, review)
                for chart in song.charts:
                    entry = await self._entry(session, chart.ref, origin.scope)
                    if entry is None:
                        entry = ExternalChartID(
                            external_song_id=parent.id,
                            external_key=chart_key(chart.ref),
                            difficulty=chart.ref.difficulty,
                            variant=chart.ref.variant,
                            chart_id=None,
                            status="unresolved",
                            revision=1,
                            source_snapshot_id=source_id,
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        )
                        session.add(entry)
                        await session.flush()
                        charts_created += 1
                    observations += await self._observe(
                        session, entry, source_id, review
                    )
            return ImportResult(source_id, songs_created, charts_created, observations)

        return await self._write(operation)

    async def _observe(
        self,
        session: AsyncSession,
        entry: Entry,
        source_id: str,
        review: ReviewEvidence,
    ) -> int:
        previous = await session.scalar(
            self._event_query(entry).where(
                IDMappingEvent.action == "observed",
                IDMappingEvent.source_snapshot_id == source_id,
            )
        )
        if previous is not None:
            return 0
        if entry.source_snapshot_id != source_id:
            entry.revision += 1
            entry.source_snapshot_id = source_id
        entry.updated_at = datetime.now(UTC)
        self._event(session, entry, "observed", review)
        await session.flush()
        return 1

    async def state(self, ref: ExternalRef, scope: CatalogScope) -> MappingState | None:
        async with self._read() as session:
            entry = await self._entry(session, ref, scope)
            return (
                MappingState(entry.status, entry.revision)
                if entry is not None
                else None
            )

    @staticmethod
    def _expected(entry: Entry | None, revision: int) -> Entry:
        if entry is None:
            _fail("external_ref_not_observed")
        assert entry is not None
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or entry.revision != revision
        ):
            _fail("stale_mapping_revision")
        return entry

    @staticmethod
    async def _validate_target(
        session: AsyncSession, entry: Entry, target: Target, *, confirming: bool
    ):
        if isinstance(entry, ExternalSongID) and isinstance(target, SongRef):
            if await session.get(Song, target.song_id) is None:
                _fail("canonical_song_not_found")
            return
        if not isinstance(entry, ExternalChartID) or not isinstance(target, ChartRef):
            _fail("target_type_mismatch")
        assert isinstance(entry, ExternalChartID) and isinstance(target, ChartRef)
        if entry.variant != "default" or entry.difficulty not in STANDARD:
            _fail("unsupported_chart_identity")
        chart = await session.get(Chart, target.chart_id)
        if (
            chart is None
            or chart.song_id != target.song_id
            or chart.difficulty != target.difficulty
            or chart.difficulty != entry.difficulty
        ):
            _fail("canonical_chart_mismatch")
        if confirming:
            parent = await session.get(ExternalSongID, entry.external_song_id)
            if (
                parent is None
                or parent.status != "confirmed"
                or parent.song_id != chart.song_id
            ):
                _fail("confirmed_parent_required")

    @staticmethod
    def _target_id(entry: Entry) -> int | None:
        return entry.song_id if isinstance(entry, ExternalSongID) else entry.chart_id

    async def propose(
        self,
        ref: ExternalRef,
        scope: CatalogScope,
        target: Target,
        *,
        expected_revision: int,
        evidence: ReviewEvidence,
    ) -> int:
        evidence.validate()

        async def operation(session: AsyncSession):
            entry = self._expected(
                await self._entry(session, ref, scope), expected_revision
            )
            await self._validate_target(session, entry, target, confirming=False)
            target_id = (
                target.song_id if isinstance(target, SongRef) else target.chart_id
            )
            if entry.status == "confirmed":
                if self._target_id(entry) != target_id:
                    _fail("confirmed_mapping_conflict")
                return entry.revision
            target_column = (
                IDMappingEvent.song_id
                if isinstance(target, SongRef)
                else IDMappingEvent.chart_id
            )
            duplicate = await session.scalar(
                self._event_query(entry).where(
                    IDMappingEvent.action == "candidate",
                    target_column == target_id,
                    IDMappingEvent.evidence_json == encode(asdict(evidence)),
                )
            )
            if duplicate is not None:
                return entry.revision
            entry.status, entry.revision = "candidate", entry.revision + 1
            entry.updated_at = datetime.now(UTC)
            self._event(session, entry, "candidate", evidence, target)
            await session.flush()
            return entry.revision

        return await self._write(operation)

    async def confirm(
        self,
        ref: ExternalRef,
        scope: CatalogScope,
        target: Target,
        *,
        expected_revision: int,
        review: ReviewEvidence,
    ) -> int:
        review.validate()

        async def operation(session: AsyncSession):
            entry = self._expected(
                await self._entry(session, ref, scope), expected_revision
            )
            await self._validate_target(session, entry, target, confirming=True)
            target_id = (
                target.song_id if isinstance(target, SongRef) else target.chart_id
            )
            if entry.status == "confirmed":
                if self._target_id(entry) != target_id:
                    _fail("confirmed_mapping_conflict")
                return entry.revision
            target_column = (
                IDMappingEvent.song_id
                if isinstance(target, SongRef)
                else IDMappingEvent.chart_id
            )
            candidate = await session.scalar(
                self._event_query(entry).where(
                    IDMappingEvent.action == "candidate",
                    target_column == target_id,
                )
            )
            if candidate is None:
                _fail("candidate_required")
            entry.status, entry.revision = "confirmed", entry.revision + 1
            if isinstance(entry, ExternalSongID):
                entry.song_id = target_id
            else:
                entry.chart_id = target_id
            entry.updated_at = datetime.now(UTC)
            self._event(session, entry, "confirmed", review, target)
            await session.flush()
            return entry.revision

        return await self._write(operation)

    async def revoke(
        self,
        ref: ExternalRef,
        scope: CatalogScope,
        *,
        expected_revision: int,
        review: ReviewEvidence,
    ) -> int:
        review.validate()

        async def operation(session: AsyncSession):
            entry = self._expected(
                await self._entry(session, ref, scope), expected_revision
            )
            entries: list[Entry] = [entry]
            if isinstance(entry, ExternalSongID):
                children = await session.scalars(
                    select(ExternalChartID).where(
                        ExternalChartID.external_song_id == entry.id,
                        ExternalChartID.status == "confirmed",
                    )
                )
                entries.extend(children.all())
            for current in entries:
                if current.status == "unresolved":
                    continue
                current.status, current.revision = "unresolved", current.revision + 1
                if isinstance(current, ExternalSongID):
                    current.song_id = None
                else:
                    current.chart_id = None
                current.updated_at = datetime.now(UTC)
                self._event(session, current, "revoked", review)
            await session.flush()
            return entry.revision

        return await self._write(operation)

    async def resolve_song(
        self, ref: ExternalSongRef, scope: CatalogScope
    ) -> ResolvedSong | None:
        async with self._read() as session:
            entry = await self._song(session, ref, scope)
            if entry is None or entry.status != "confirmed" or entry.song_id is None:
                return None
            return ResolvedSong(SongRef(entry.song_id), entry.revision)

    async def resolve_chart(
        self, ref: ExternalChartRef, scope: CatalogScope
    ) -> ResolvedChart | None:
        async with self._read() as session:
            entry = await self._entry(session, ref, scope)
            if (
                not isinstance(entry, ExternalChartID)
                or entry.status != "confirmed"
                or entry.chart_id is None
            ):
                return None
            parent, chart = (
                await session.get(ExternalSongID, entry.external_song_id),
                await session.get(Chart, entry.chart_id),
            )
            if (
                parent is None
                or parent.status != "confirmed"
                or chart is None
                or parent.song_id != chart.song_id
                or entry.difficulty != chart.difficulty
                or entry.variant != "default"
            ):
                return None
            return ResolvedChart(
                ChartRef(chart.id, chart.song_id, chart.difficulty),
                entry.revision,
                parent.revision,
            )

    async def history(
        self, ref: ExternalRef, scope: CatalogScope
    ) -> tuple[MappingHistory, ...]:
        async with self._read() as session:
            entry = await self._entry(session, ref, scope)
            if entry is None:
                return ()
            events = await session.scalars(
                self._event_query(entry).order_by(IDMappingEvent.revision)
            )
            return tuple(
                MappingHistory(
                    e.revision,
                    e.action,
                    e.song_id,
                    e.chart_id,
                    e.source_snapshot_id,
                    e.evidence_json,
                )
                for e in events
            )
