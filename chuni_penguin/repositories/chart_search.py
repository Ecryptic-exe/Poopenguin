"""Original A chart-level search SQL; no external identity or routing guesses."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Chart, Song

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


@dataclass(frozen=True, slots=True)
class ChartSearchFilter:
    level: str | None = None
    const: float | None = None
    min_const: float | None = None
    max_const: float | None = None


class ChartSearchRepository:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def charts(self, filters: ChartSearchFilter) -> Sequence[Chart]:
        stmt = (
            select(Chart)
            .options(joinedload(Chart.sdvxin_chart_view), joinedload(Chart.song))
            .join(Song, Chart.song)
            .order_by(Chart.const, Song.title)
        )
        if filters.min_const is not None:
            stmt = stmt.where(Chart.const >= filters.min_const)
        if filters.max_const is not None:
            stmt = stmt.where(Chart.const <= filters.max_const)
        if filters.const is not None:
            stmt = stmt.where(Chart.const == filters.const)
        if filters.level is not None:
            stmt = stmt.where(Chart.level == filters.level)
        async with self.database.read_sessionmaker() as session:
            return (await session.execute(stmt)).scalars().all()
