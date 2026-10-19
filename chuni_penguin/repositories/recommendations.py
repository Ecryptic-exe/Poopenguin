"""Original A selection/PB SQL behind the existing catalog service boundary."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Row, func, select, text
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Chart, PersonalBest, Song

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


@dataclass(frozen=True, slots=True)
class RandomChartFilter:
    level: str | None = None
    const: float | None = None
    min_const: float | None = None
    max_const: float | None = None


def _random_statement():
    return (
        select(Chart)
        .join(Song, Chart.song_id == Song.id)
        .where(Song.removed == False)  # noqa: E712
        .order_by(text("RANDOM()"))
        .options(joinedload(Chart.song), joinedload(Chart.sdvxin_chart_view))
    )


class RecommendationRepository:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def random_charts(
        self, count: int, filters: RandomChartFilter
    ) -> Sequence[Chart]:
        stmt = _random_statement().limit(count)
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

    async def random_course(
        self,
        course_class: str,
        track_levels: Sequence[str | None],
        sibyl_songs: Sequence[Sequence[int]],
    ) -> Sequence[Chart]:
        stmt = _random_statement()
        charts: list[Chart] = []
        async with self.database.read_sessionmaker() as session:
            for i, track_level in enumerate(track_levels):
                chart_stmt = stmt.limit(1)
                if track_level is not None:
                    chart_stmt = chart_stmt.where(Chart.level == track_level)
                elif course_class == "random":
                    chart_stmt = chart_stmt.where(
                        (Chart.song_id >= 8244) & (Chart.song_id <= 8249)
                    )
                elif course_class == "sibyl":
                    chart_stmt = chart_stmt.where(
                        Chart.song_id.in_(sibyl_songs[i]) & (Chart.difficulty == "MAS")
                    )
                chart = (await session.execute(chart_stmt)).scalar_one_or_none()
                if chart is not None:
                    charts.append(chart)
        return charts

    async def cached_rating(self, user_id: int, network: str) -> float | None:
        async with self.database.read_sessionmaker() as session:
            naive_best50 = (
                select(PersonalBest.rating)
                .where(
                    (PersonalBest.discord_id == user_id)
                    & (PersonalBest.network == network)
                    & (PersonalBest.rating.is_not(None))
                )
                .order_by(PersonalBest.rating.desc())
                .limit(50)
            ).subquery()
            value = (
                await session.execute(func.avg(naive_best50.c.rating))
            ).scalar_one_or_none()
            return int(value) / 100 if value is not None else None

    async def recommendation_candidates(
        self, user_id: int, min_level: float, max_level: float
    ) -> Sequence[Row[tuple[Chart, int | None]]]:
        async with self.database.read_sessionmaker() as session:
            pb_cte = (
                select(
                    PersonalBest.song_id,
                    PersonalBest.difficulty,
                    func.max(PersonalBest.score).label("score"),
                )
                .where(PersonalBest.discord_id == user_id)
                .group_by(PersonalBest.song_id, PersonalBest.difficulty)
                .cte("personal_bests_all_networks")
            )
            stmt = (
                select(Chart, pb_cte.c.score)
                .join(Song, Chart.song_id == Song.id)
                .join(
                    pb_cte,
                    (pb_cte.c.song_id == Chart.song_id)
                    & (pb_cte.c.difficulty == Chart.difficulty),
                    isouter=True,
                )
                .where(
                    (Chart.const >= min_level)
                    & (Chart.const <= max_level)
                    & (Song.available.is_(True))
                )
                .order_by(text("RANDOM()"))
                .options(joinedload(Chart.song), joinedload(Chart.sdvxin_chart_view))
            )
            return (await session.execute(stmt)).all()
