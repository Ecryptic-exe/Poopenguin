"""Original paired local folder-statistics queries with source-specific filters."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.kamaitachi import KamaitachiAdapter
from chuni_penguin.converters import Level, LevelRange
from chuni_penguin.database import Chart, Song
from chuni_penguin.database import PersonalBest as DBPersonalBest
from chuni_penguin.types import Difficulty, Genre

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


class FolderStatisticsRepository:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def load_folder(
        self,
        target_id: int,
        client_name: str,
        *,
        level: Level | LevelRange | None = None,
        difficulty: Difficulty | Literal["MASTER+ULTIMA"] | None = None,
        genre: Genre | None = None,
        version: str | None = None,
        omnimix: bool = False,
    ) -> tuple[Sequence[DBPersonalBest], Sequence[Chart]]:
        async with self.database.read_sessionmaker() as session:
            pb_query = (
                select(DBPersonalBest)
                .where(
                    (DBPersonalBest.discord_id == target_id)
                    & (DBPersonalBest.network == client_name)
                )
                .join(Song, DBPersonalBest.song_id == Song.id)
                .join(
                    Chart,
                    (DBPersonalBest.song_id == Chart.song_id)
                    & (DBPersonalBest.difficulty == Chart.difficulty),
                )
                .order_by(
                    DBPersonalBest.score.desc(), Chart.const.desc(), Chart.id.desc()
                )
            )
            chart_query = (
                select(Chart)
                .join(Song, Chart.song_id == Song.id)
                .where(Chart.song_id.not_in([50, 81]))  # basic and master tutorials
            )

            if client_name == ChunithmNetAdapter.NAME:
                cond = (Song.available == True) & (Chart.available == True)  # noqa: E712
                pb_query = pb_query.where(cond)
                chart_query = chart_query.where(cond)
            elif client_name == KamaitachiAdapter.NAME:
                if not omnimix:
                    cond = Song.removed == False  # noqa: E712
                    pb_query = pb_query.where(cond)
                    chart_query = chart_query.where(cond)

                cond = Chart.tachi_chart_id.is_not(None)
                pb_query = pb_query.where(cond)
                chart_query = chart_query.where(cond)

            if isinstance(level, LevelRange):
                if level.min_level is not None:
                    cond = Chart.const >= (
                        level.min_level.const or level.min_level.inferred_const
                    )
                    pb_query = pb_query.where(cond)
                    chart_query = chart_query.where(cond)

                if level.max_level is not None:
                    cond = Chart.const <= (
                        level.max_level.const or level.max_level.inferred_max_const
                    )
                    pb_query = pb_query.where(cond)
                    chart_query = chart_query.where(cond)
            elif level is not None:
                if level.const is not None:
                    cond = Chart.const == level.const
                else:
                    cond = Chart.level == level.level

                pb_query = pb_query.where(cond)
                chart_query = chart_query.where(cond)

            if difficulty is not None and isinstance(difficulty, Difficulty):
                cond = Chart.difficulty == difficulty.short()
            elif difficulty == "MASTER+ULTIMA":
                cond = Chart.difficulty.in_(
                    [Difficulty.master.short(), Difficulty.ultima.short()]
                )
            else:
                cond = Chart.difficulty != Difficulty.worlds_end.short()

            pb_query = pb_query.where(cond)
            chart_query = chart_query.where(cond)

            if genre is not None:
                cond = Song.genre == str(genre)
                pb_query = pb_query.where(cond)
                chart_query = chart_query.where(cond)

            if version is not None:
                cond = Song.version == version
                pb_query = pb_query.where(cond)
                chart_query = chart_query.where(cond)

            pbs = (await session.execute(pb_query)).scalars().all()
            charts = (await session.execute(chart_query)).scalars().all()

        return pbs, charts
