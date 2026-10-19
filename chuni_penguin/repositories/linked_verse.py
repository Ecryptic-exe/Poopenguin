"""Original A Linked VERSE local queries and detached eager relationships."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import Row, func, select
from sqlalchemy.orm import contains_eager, joinedload
from sqlalchemy.sql.elements import ColumnElement

from chuni_penguin.database import Alias, Chart, LinkedGateCondition, Song
from chuni_penguin.database import LinkedGate as DBLinkedGate

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


class LinkedVerseRepository:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def choices(self) -> Sequence[Row[tuple[int, str, bool]]]:
        async with self.database.read_sessionmaker() as session:
            query = select(
                DBLinkedGate.id, DBLinkedGate.name, DBLinkedGate.available
            ).order_by(DBLinkedGate.id)
            results = (await session.execute(query)).fetchall()

        return results  # noqa: RET504 - keep the original query block and close boundary

    async def gates(self, whereclause: ColumnElement[bool]) -> Sequence[DBLinkedGate]:
        async with self.database.read_sessionmaker() as session:
            query = (
                select(DBLinkedGate)
                .where(whereclause)
                .join(Song, DBLinkedGate.song_id == Song.id)
                .outerjoin(Alias, (Alias.song_id == Song.id) & (Alias.guild_id == 0))
                .outerjoin(
                    LinkedGateCondition,
                    (LinkedGateCondition.linked_gate_id == DBLinkedGate.id)
                    & (LinkedGateCondition.start_date <= func.unixepoch())
                    & (
                        LinkedGateCondition.end_date.is_(None)
                        | (func.unixepoch() < LinkedGateCondition.end_date)
                    ),
                )
                .options(
                    joinedload(DBLinkedGate.song)
                    .joinedload(Song.charts)
                    .joinedload(Chart.sdvxin_chart_view),
                    joinedload(DBLinkedGate.song).contains_eager(Song.aliases),
                    contains_eager(DBLinkedGate.conditions),
                )
            )

            return (await session.execute(query)).unique().scalars().all()

    async def by_id(self, gate_id: int) -> Sequence[DBLinkedGate]:
        return await self.gates(DBLinkedGate.id == gate_id)
