"""Native sync reads share the adapters' cache writes and their completion boundaries."""

from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Literal

from chuni_penguin.adapters.base import NetworkAdapter
from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.logging import logger
from chuni_penguin.types import Profile

type Progress = Callable[
    [Literal["recent", "details", "bests"], int, int], Awaitable[None]
]


class PartialScoreSync(Exception):
    def __init__(self, source: str, recent: int, detail_count: int | None):
        self.source = source
        self.recent = recent
        self.detail_count = detail_count


@contextmanager
def _partial_on_failure(source: str, recent: int, detail_count: int | None):
    try:
        yield
    except Exception as error:
        logger.warning(
            "Native sync interrupted",
            source=source,
            recent=recent,
            detail_count=detail_count,
            error_type=type(error).__name__,
        )
        raise PartialScoreSync(source, recent, detail_count) from error


async def read_native_scores(
    client: NetworkAdapter,
    progress: Progress | None = None,
) -> tuple[Profile, int, int]:
    profile = await client.get_minimal_profile()
    if progress:
        await progress("recent", 0, 0)
    recent = await client.get_recent_scores()
    if client.SUPPORTS_DETAILED_RECENT_SCORE:
        for done, play in enumerate(recent):
            with _partial_on_failure(client.NAME, len(recent), done):
                await client.get_detailed_recent_score(play)
            if progress and ((done + 1) % 10 == 0 or done + 1 == len(recent)):
                await progress("details", done + 1, len(recent))
    if progress:
        await progress("bests", 0, 0)
    with _partial_on_failure(client.NAME, len(recent), None):
        if isinstance(client, ChunithmNetAdapter):
            records = await client.get_all_personal_bests(validate_pages=True)
        else:
            records = await client.get_all_personal_bests()
    return profile, len(recent), len(records)
