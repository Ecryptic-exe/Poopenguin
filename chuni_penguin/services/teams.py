import asyncio
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from chuni_penguin.types import Team
from chuni_penguin.types.ranking import TeamRanking, TeamRankingEntry
from chuni_penguin.utils import TOKYO_TZ

from .card_cache import CardDataCache

if TYPE_CHECKING:
    from chuni_penguin.context import PenguinContext

FIRST_MONTH = date(2015, 1, 1)


def current_month() -> date:
    return datetime.now(TOKYO_TZ).date().replace(day=1)


def shift_month(month: date, offset: int) -> date:
    year, index = divmod(month.year * 12 + month.month - 1 + offset, 12)
    return date(year, index + 1, 1)


def parse_month(value: str | None, *, now: date | None = None) -> date | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", value.strip())
    if match is None:
        msg = "Use YYYY/MM for the month, for example 2026/09."
        raise ValueError(msg)
    try:
        month = date(int(match[1]), int(match[2]), 1)
    except ValueError:
        msg = "Invalid calendar month."
        raise ValueError(msg) from None
    if month < FIRST_MONTH or month > (now or current_month()).replace(day=1):
        msg = "The month must be between 2015/01 and the current month."
        raise ValueError(msg)
    return month


@dataclass(frozen=True)
class TeamBoard:
    month: date | None
    network: str
    color: int
    updated_at: datetime
    entries: Sequence[TeamRankingEntry]

    @classmethod
    def from_ranking(
        cls, month: date | None, ranking: TeamRanking, network: str, color: int
    ):
        return cls(month, network, color, ranking.updated_at, tuple(ranking.ranking))


@dataclass(frozen=True)
class TeamHistoryPoint:
    month: date
    status: Literal["matched", "not_found", "ambiguous", "unavailable"]
    entry: TeamRankingEntry | None = None
    updated_at: datetime | None = None


def find_teams(
    entries: Sequence[TeamRankingEntry], query: str
) -> list[TeamRankingEntry]:
    def normalized(value: str):
        return unicodedata.normalize("NFKC", value).strip().casefold()

    needle = normalized(query)
    if not needle:
        return list(entries)
    exact = [entry for entry in entries if normalized(entry.team_name) == needle]
    return exact or [
        entry for entry in entries if needle in normalized(entry.team_name)
    ]


class TeamDetailsData(CardDataCache):
    def __init__(
        self,
        ctx: "PenguinContext",
        *,
        timeout: float = 20,
        now: Callable[[], date] = current_month,
        initial: TeamBoard | None = None,
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.now = now
        self._limiter = asyncio.Semaphore(2)
        if initial is not None:
            self._cache[("team_board", initial.month)] = initial

    async def monthly(self, month: date | None) -> TeamBoard:
        if month is not None:
            month = parse_month(month.strftime("%Y/%m"), now=self.now())

        async def load():
            async with (
                self._limiter,
                self.ctx.bot.chunithm_networks.bot_network(kamaitachi=False) as client,
            ):
                selected = (
                    datetime(month.year, month.month, 1, tzinfo=TOKYO_TZ)
                    if month
                    else None
                )
                ranking = await client.get_team_ranking(selected)
                return TeamBoard.from_ranking(
                    month, ranking, client.NAME, client.ACCENT_COLOR
                )

        return await self._cached(("team_board", month), load)

    async def mine(self) -> Team | None:
        async def load():
            async with self.ctx.bot.chunithm_networks.network(
                self.ctx, self.ctx.author.id, chunithm_net=True
            ) as client:
                return (await client.get_profile()).team

        return await self._cached(("my_team", self.ctx.author.id), load)

    async def history(
        self, name: str, *, end: date | None = None, count: int = 6
    ) -> list[TeamHistoryPoint]:
        if not 1 <= count <= 12:
            msg = "History is limited to between 1 and 12 months."
            raise ValueError(msg)
        anchor = end or shift_month(self.now(), -1)
        parse_month(anchor.strftime("%Y/%m"), now=self.now())
        months = [shift_month(anchor, -i) for i in range(count)]
        months = [month for month in months if month >= FIRST_MONTH]

        async def point(month: date):
            try:
                board = await self.monthly(month)
            except Exception:  # noqa: BLE001 - one unavailable month must not hide the others
                return TeamHistoryPoint(month, "unavailable")
            # Names are not stable team IDs. Do not infer identity from fuzzy matches.
            matches = [entry for entry in board.entries if entry.team_name == name]
            if not matches:
                return TeamHistoryPoint(month, "not_found", updated_at=board.updated_at)
            if len(matches) != 1:
                return TeamHistoryPoint(month, "ambiguous", updated_at=board.updated_at)
            return TeamHistoryPoint(month, "matched", matches[0], board.updated_at)

        return list(await asyncio.gather(*(point(month) for month in months)))
