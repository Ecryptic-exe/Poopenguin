"""Original A score-image lookup; URLs remain local catalog lookup keys."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Song, SongJacket

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


class ScoreQueryRepository:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def jackets_for_images(
        self, message_images: Sequence[str]
    ) -> Sequence[SongJacket]:
        sql = (
            select(SongJacket)
            .where(SongJacket.jacket_url.in_(message_images))
            .group_by(SongJacket.song_id)
            .options(joinedload(SongJacket.song).joinedload(Song.charts))
        )
        async with self.database.read_sessionmaker() as session:
            return (await session.execute(sql)).scalars().unique().all()
