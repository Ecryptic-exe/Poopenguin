"""Read A's existing canonical tables without assigning foreign IDs to them."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from chuni_penguin.database import Song

from .models import (
    CatalogChart,
    CatalogScope,
    CatalogSnapshot,
    CatalogSong,
    ExternalChartRef,
    ExternalSongRef,
    Provenance,
    ProviderDescriptor,
)
from .registry import ProviderError


class CatalogDatabase(Protocol):
    @property
    def read_sessionmaker(self) -> async_sessionmaker[AsyncSession]: ...


class LocalCatalogProvider:
    descriptor = ProviderDescriptor(
        "a_catalog",
        "a_local",
        CatalogScope("unspecified", "unspecified"),
        ("chuni_penguin/database/songs.py", "chuni_penguin/services/database.py"),
    )

    def __init__(self, database: CatalogDatabase | None):
        self._database = database
        self._closed = False

    async def start(self):
        if self._database is None or self._closed:
            raise ProviderError(self.descriptor.key, "database_unavailable")

    async def read_catalog(self) -> CatalogSnapshot:
        if self._database is None or self._closed:
            raise ProviderError(self.descriptor.key, "database_unavailable")
        async with self._database.read_sessionmaker() as session:
            query = select(Song).options(selectinload(Song.charts)).order_by(Song.id)
            rows = (await session.scalars(query)).all()
            songs = []
            for row in rows:
                ref = ExternalSongRef(self.descriptor.source_namespace, str(row.id))
                charts = tuple(
                    CatalogChart(
                        ExternalChartRef(ref, chart.difficulty, chart_id=str(chart.id)),
                        Decimal(str(chart.const)) if chart.const is not None else None,
                        chart.level,
                    )
                    for chart in sorted(row.charts, key=lambda c: c.id)
                )
                songs.append(CatalogSong(ref, row.title, row.artist, charts))
        revision = hashlib.sha256(
            json.dumps(
                [
                    [
                        s.ref.id,
                        s.title,
                        s.artist,
                        [
                            [c.ref.chart_id, c.ref.difficulty, str(c.constant), c.level]
                            for c in s.charts
                        ],
                    ]
                    for s in songs
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        return CatalogSnapshot(
            tuple(songs),
            Provenance(
                self.descriptor.key,
                self.descriptor.source_namespace,
                self.descriptor.scope,
                None,
                revision,
                datetime.now(UTC),
                "local_db_read",
            ),
        )

    async def close(self):
        # DatabaseService is owned by ApplicationServices, not this reader.
        self._closed = True
