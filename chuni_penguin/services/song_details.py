import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.database import Chart, PlayerSource, Song
from chuni_penguin.database import PersonalBest as DBPersonalBest
from chuni_penguin.types import Difficulty, Leaderboard, PersonalBest

from .card_cache import CardDataCache
from .chart_view import fetch_chart_view

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext

NetworkChoice = Literal[
    "default",
    "chunithm_net",
    "kamaitachi",
    "chunirec:jp2",
    "chunirec:jp",
    "chunilib:export",
]


class MissingScoreSnapshot(Exception):
    """There is no local sync or import for this account and source."""


@dataclass(frozen=True)
class SongScores:
    network: str
    records: Sequence[PersonalBest]
    # Normalized DB rows cannot distinguish an absent imported clear flag from FAILED.
    reported_clear: frozenset[str] | None = None
    saved_at: datetime | None = None
    has_unmapped: bool = False


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
                    rows = (
                        await session.scalars(
                            select(DBPersonalBest).where(
                                DBPersonalBest.discord_id == self.ctx.author.id,
                                DBPersonalBest.network == network,
                                DBPersonalBest.song_id == song.id,
                            )
                        )
                    ).all()
                    snapshot = (
                        await session.execute(
                            select(PlayerSource.payload, PlayerSource.synced_at).where(
                                PlayerSource.discord_id == self.ctx.author.id,
                                PlayerSource.source == network,
                            )
                        )
                    ).one_or_none()
                if snapshot is None or not snapshot.payload:
                    if not rows:
                        raise MissingScoreSnapshot
                    payload = {}
                else:
                    payload = await asyncio.to_thread(json.loads, snapshot.payload)
                reported_clear = frozenset(
                    item["diff"]
                    for item in payload.get("records", ())
                    if item.get("song_id") == song.id
                    and type(item.get("is_clear")) is bool
                )
                records = [
                    await self.ctx.bot.utils.convert_to_network_pb(row) for row in rows
                ]
                return SongScores(
                    network,
                    tuple(records),
                    reported_clear,
                    datetime.fromisoformat(snapshot.synced_at)
                    if snapshot and snapshot.synced_at
                    else None,
                    bool(payload.get("unmapped")),
                )
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
