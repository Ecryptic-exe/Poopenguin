from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.database import Chart, Song, PersonalBest as DBPersonalBest
from chuni_penguin.types import Difficulty, Leaderboard, PersonalBest

from .card_cache import CardDataCache
from .chart_view import fetch_chart_view

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext

NetworkChoice = Literal["default", "chunithm_net", "kamaitachi", "chunirec:jp2", "chunirec:jp", "chunilib:export"]


@dataclass(frozen=True)
class SongScores:
    network: str
    records: Sequence[PersonalBest]


@dataclass(frozen=True)
class SongLeaderboard:
    network: str
    leaderboard: Leaderboard


class SongDetailsData(CardDataCache):
    """Lazy, per-message cache. No account credentials or failures are cached here."""

    def __init__(self, ctx: "PenguinContext", *, timeout: float = 20):
        super().__init__(timeout=timeout)
        self.ctx = ctx

    async def charts(self, song: Song) -> Sequence[Chart]:
        async def load():
            async with self.ctx.bot.begin_db_read() as session:
                stmt = (
                    select(Chart)
                    .where(Chart.song_id == song.id)
                    .options(
                        joinedload(Chart.song), joinedload(Chart.sdvxin_chart_view)
                    )
                )
                charts = (await session.execute(stmt)).scalars().all()

            return tuple(sorted(charts, key=lambda c: Difficulty(c.difficulty).value))

        return await self._cached(("charts", song.id), load)

    def cached_charts(self, song: Song) -> Sequence[Chart]:
        return self._cache.get(("charts", song.id), ())

    async def scores(self, song: Song, network: NetworkChoice) -> SongScores:
        async def load():
            if ":" in network:
                async with self.ctx.bot.begin_db_read() as session:
                    rows = (await session.scalars(select(DBPersonalBest).where(DBPersonalBest.discord_id == self.ctx.author.id, DBPersonalBest.network == network, DBPersonalBest.song_id == song.id))).all()
                records = [await self.ctx.bot.utils.convert_to_network_pb(row) for row in rows]
                return SongScores(network, tuple(records))
            async with self.ctx.bot.chunithm_networks.network(
                self.ctx,
                self.ctx.author.id,
                kamaitachi=network == "kamaitachi",
                chunithm_net=network == "chunithm_net",
            ) as client:
                if isinstance(client, ChunithmNetAdapter):
                    song.raise_if_not_available()
                records = await client.get_personal_bests_on_song(song.id)
                return SongScores(client.NAME, tuple(records))

        return await self._cached(("scores", song.id, network), load)

    async def leaderboard(
        self, chart: Chart, network: NetworkChoice
    ) -> SongLeaderboard:
        async def load():
            async with self.ctx.bot.chunithm_networks.bot_network(
                kamaitachi=network == "kamaitachi"
            ) as client:
                if isinstance(client, ChunithmNetAdapter):
                    chart.song.raise_if_not_available()
                board = await client.get_chart_leaderboard(
                    chart.song_id, Difficulty(chart.difficulty)
                )
                return SongLeaderboard(client.NAME, board)

        return await self._cached(
            ("leaderboard", chart.song_id, chart.difficulty, network), load
        )

    async def chart_image(self, chart: Chart, *, mirror: bool = False) -> bytes:
        async def load():
            image = await fetch_chart_view(
                self.ctx.bot.caching_http_client, chart, mirror=mirror
            )
            try:
                return image.getvalue()
            finally:
                image.close()

        return await self._cached(
            ("chart_image", chart.song_id, chart.difficulty, mirror), load
        )
