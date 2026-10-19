import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chuni_penguin.database import Chart, SdvxinChartView, Song
from chuni_penguin.database.base import Base
from chuni_penguin.services.song_details import SongDetailsData
from chuni_penguin.ui.components.chart_card_embed import ChartCardEmbed


def make_data(*, timeout=20):
    client = SimpleNamespace(
        NAME="Test", get_personal_bests_on_song=AsyncMock(return_value=[])
    )
    calls = []

    @asynccontextmanager
    async def network(ctx, target, **flags):
        calls.append((target, flags))
        yield client

    ctx = SimpleNamespace(
        author=SimpleNamespace(id=123),
        bot=SimpleNamespace(chunithm_networks=SimpleNamespace(network=network)),
    )
    return SongDetailsData(ctx, timeout=timeout), client, calls


@pytest.mark.asyncio
async def test_scores_share_inflight_and_success_but_not_song_or_network():
    data, client, calls = make_data()
    song = Song(id=1)
    gate, entered = asyncio.Event(), asyncio.Event()

    async def fetch(_):
        entered.set()
        await gate.wait()
        return []

    client.get_personal_bests_on_song.side_effect = fetch
    first = asyncio.create_task(data.scores(song, "default"))
    second = asyncio.create_task(data.scores(song, "default"))
    await entered.wait()
    gate.set()
    a, b = await asyncio.gather(first, second)
    assert a is b
    assert await data.scores(song, "default") is a
    assert client.get_personal_bests_on_song.await_count == 1
    await data.scores(song, "kamaitachi")
    await data.scores(Song(id=2), "default")
    assert client.get_personal_bests_on_song.await_count == 3
    assert calls[0] == (123, {"kamaitachi": False, "chunithm_net": False})
    assert calls[1] == (123, {"kamaitachi": True, "chunithm_net": False})


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_retry_is_not_poisoned():
    data, client, _ = make_data(timeout=0.01)

    async def wait_forever(_):
        await asyncio.Event().wait()

    client.get_personal_bests_on_song.side_effect = wait_forever
    with pytest.raises(TimeoutError):
        await data.scores(Song(id=1), "default")
    assert not data._pending
    client.get_personal_bests_on_song.side_effect = None
    result = await data.scores(Song(id=1), "default")
    assert result.records == ()
    assert client.get_personal_bests_on_song.await_count == 2


@pytest.mark.asyncio
async def test_cache_is_private_to_each_message_and_close_cancels_load():
    first, first_client, _ = make_data()
    second, second_client, _ = make_data()
    await first.scores(Song(id=1), "default")
    await second.scores(Song(id=1), "default")
    first_client.get_personal_bests_on_song.assert_awaited_once()
    second_client.get_personal_bests_on_song.assert_awaited_once()
    entered = asyncio.Event()

    async def wait_forever(_):
        entered.set()
        await asyncio.Event().wait()

    first_client.get_personal_bests_on_song.side_effect = wait_forever
    pending = asyncio.create_task(first.scores(Song(id=2), "default"))
    await entered.wait()
    await first.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not first._cache
    assert not first._pending


@pytest.mark.asyncio
async def test_charts_can_render_after_database_session_closes():
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        song = Song(
            id=1,
            title="Test",
            artist="Artist",
            genre="ORIGINAL",
            version="X",
            chunithm_catcode=0,
            available=True,
            removed=False,
            aliases=[],
        )
        async with sessions() as session:
            session.add(song)
            session.add_all(
                [
                    Chart(
                        song=song,
                        difficulty="MAS",
                        level="14",
                        sdvxin_chart_view=SdvxinChartView(
                            id="130001", difficulty="MAS", end_index=""
                        ),
                    ),
                    Chart(song=song, difficulty="BAS", level="4"),
                ]
            )
            await session.commit()
        ctx = SimpleNamespace(bot=SimpleNamespace(begin_db_read=sessions))
        data = SongDetailsData(ctx)
        charts = await data.charts(song)
        assert [chart.difficulty for chart in charts] == ["BAS", "MAS"]
        embed = ChartCardEmbed(charts[1])
        assert embed.title == "Test"
        assert "sdvx.in" in str(embed.fields[1].value)
        assert await data.charts(song) is charts
    finally:
        await engine.dispose()
