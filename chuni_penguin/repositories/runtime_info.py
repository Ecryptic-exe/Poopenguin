"""Original local diagnostics and random presence song data."""

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from chuni_penguin.database import Cookie, Song

if TYPE_CHECKING:
    from chuni_penguin.services.database import DatabaseService


class RuntimeInfoRepository:
    def __init__(self, database: "DatabaseService"):
        self.database = database

    async def registered_users(self) -> int | None:
        async with self.database.read_sessionmaker() as session:
            users = await session.scalar(select(func.count()).select_from(Cookie))

        return users  # noqa: RET504 - preserve the original query block

    async def presence_song(self) -> Song | None:
        async with self.database.read_sessionmaker() as session:
            query = (
                select(Song)
                .where(Song.genre == "ORIGINAL")
                .order_by(func.random())
                .limit(1)
            )
            song = await session.scalar(query)

            if song is None:
                return  # noqa: RET502 - original early return within the read context

        return song
