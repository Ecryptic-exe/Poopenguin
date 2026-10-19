"""Original A course queries, independent of Discord and Cog ownership."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import Row, select
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Chart, Course, CourseTrack
from chuni_penguin.types import CourseClass

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


class CourseRepository:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def version_index(self) -> Sequence[Row[tuple[int, str]]]:
        async with self.database.read_sessionmaker() as session:
            # A-local course IDs encode version groups. Do not apply this to
            # external opaque IDs from other Providers.
            query = (
                select(Course.id // 10000, Course.version)
                .distinct()
                .order_by(Course.id)
            )
            return (await session.execute(query)).fetchall()

    async def list_courses(
        self, versions: Sequence[str], cls: CourseClass
    ) -> list[Course]:
        async with self.database.read_sessionmaker() as session:
            query = (
                select(Course)
                .where(Course.version.in_(versions) & (Course.cls == cls))
                .options(
                    joinedload(Course.tracks)
                    .joinedload(CourseTrack.charts)
                    .options(
                        joinedload(Chart.song), joinedload(Chart.sdvxin_chart_view)
                    )
                )
            )
            return list((await session.execute(query)).scalars().unique())
