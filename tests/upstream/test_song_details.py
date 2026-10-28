import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from chuni_penguin.config import Config, use_config
from chuni_penguin.database import Alias, Chart, SdvxinChartView, Song
from chuni_penguin.services.song_details import (
    SongDetailsData,
    SongLeaderboard,
    SongScores,
)
from chuni_penguin.types import (
    Chart as ScoreChart,
)
from chuni_penguin.types import (
    ClearLamp,
    ComboLamp,
    Difficulty,
    Leaderboard,
    LeaderboardEntry,
    PersonalBest,
)
from chuni_penguin.types import (
    Song as ScoreSong,
)
from chuni_penguin.ui.song_details import (
    COMMON_TABS,
    TABS,
    SongDetailsState,
    SongDetailsView,
    SongMoreSelect,
)


@pytest.fixture(autouse=True)
def chinese_ui():
    with use_config(Config.from_mapping({"fun": {"language": "chinese"}})):
        yield


def make_song(song_id=1, *, difficulties=("BAS", "EXP", "MAS", "ULT")):
    song = Song(
        id=song_id,
        title=f"Song {song_id}",
        artist="Artist",
        genre="ORIGINAL",
        version="X-VERSE",
        available=True,
        removed=False,
        aliases=[],
        bpm=180,
        duration=123000,
        jacket="test.jpg",
    )
    charts = [
        Chart(
            song=song,
            song_id=song_id,
            difficulty=difficulty,
            level="14+",
            const=14.7,
            maxcombo=1000,
            tap=500,
            hold=0,
            slide=200,
            air=250,
            flick=50,
            charter="Designer",
            sdvxin_chart_view=None,
        )
        for difficulty in difficulties
    ]
    return song, charts


def make_context():
    return SimpleNamespace(
        author=SimpleNamespace(id=123),
        bot=SimpleNamespace(is_owner=AsyncMock(return_value=True)),
    )


def make_view(*songs_and_charts, **kwargs):
    ctx = make_context()
    data = SongDetailsData(ctx)
    for song, charts in songs_and_charts:
        data._cache[("charts", song.id)] = tuple(charts)
    view = SongDetailsView(
        ctx, [pair[0] for pair in songs_and_charts], data=data, **kwargs
    )
    view.message = SimpleNamespace(edit=AsyncMock())
    return view, data


def interaction(user_id=123):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            defer=AsyncMock(), is_done=lambda: True, send_message=AsyncMock()
        ),
    )


def assert_limits(kwargs):
    assert sum(len(embed) for embed in kwargs["embeds"]) <= 6000
    for embed in kwargs["embeds"]:
        assert len(embed.title or "") <= 256
        assert len(embed.description or "") <= 4096
        assert len(embed.footer.text or "") <= 2048
        assert len(embed.fields) <= 25
        for field in embed.fields:
            assert len(field.name) <= 256
            assert len(field.value) <= 1024


@pytest.mark.asyncio
async def test_review_groups_and_pagination_leave_room_for_close_and_share_canonical_chart():
    from chuni_penguin.ui.chart_reviews import ReviewEntrySelect
    from chuni_penguin.ui.song_details import SongDetailsPage, SongPageSelect

    song, charts = make_song()
    charts[2].id = 444
    view, _ = make_view((song, charts), initial_tab="reviews")
    summary = {
        "groups": {
            "rating_bucket": [{"label": "16–16.49", "count": 1}],
            "primary_level": [],
            "experience_bucket": [],
        }
    }
    page = SongDetailsPage(
        view.state,
        charts,
        [[discord.Embed(title="Page 1")], [discord.Embed(title="Page 2")]],
        review_summary=summary,
    )
    view._controls(page)
    review = next(c for c in view.children if isinstance(c, ReviewEntrySelect))
    assert review.chart.id == 444
    assert [o.value for o in review.options] == ["write", "overall", "rating_bucket"]
    assert any(isinstance(c, SongPageSelect) for c in view.children)
    assert [c.label for c in view.children if isinstance(c, discord.ui.Button)] == [
        "返回主選單",
        "關閉"
    ]
    rows = view.to_components()
    assert len(rows) == 5
    assert all(
        sum(1 if c["type"] == 2 else 5 for c in row["components"]) <= 5 for row in rows
    )
    view.transition = AsyncMock()
    review._values = ["rating_bucket"]
    changed = interaction()
    await review.callback(changed)
    view.transition.assert_awaited_once_with(
        changed, review_group="rating_bucket", page=0
    )


@pytest.mark.asyncio
async def test_search_starts_with_all_tabs_and_no_remote_requests():
    view, data = make_view(make_song())
    data.scores = AsyncMock()
    data.leaderboard = AsyncMock()
    data.chart_image = AsyncMock()
    kwargs = await view._before_start()
    assert {
        child.label for child in view.children if isinstance(child, discord.ui.Button)
    } == {label for tab, label in TABS if tab in COMMON_TABS and tab != "reviews"} | {
        "返回主選單",
        "關閉",
    }
    menu = next(child for child in view.children if isinstance(child, SongMoreSelect))
    assert {
        option.value for option in menu.options if option.value.startswith("tab:")
    } == {
        "tab:view",
        "tab:scores",
        "tab:reviews",
        "tab:overview",
        "tab:rating",
        "tab:borders",
    }
    assert view.state.difficulty == "MAS"
    assert "Artist" in kwargs["embeds"][0].description
    assert_limits(kwargs)
    data.scores.assert_not_awaited()
    data.leaderboard.assert_not_awaited()
    data.chart_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_detailed_flag_opens_chart_and_preserves_zero_note_counts():
    view, _ = make_view(make_song(), detailed=True)
    kwargs = await view._before_start()
    assert view.state.tab == "chart"
    assert "**HOLD**: 0" in kwargs["embeds"][0].description
    assert "Designer" in kwargs["embeds"][0].description
    assert_limits(kwargs)


@pytest.mark.asyncio
async def test_song_switch_recovers_unavailable_tab_and_falls_back_to_existing_difficulty():
    first = make_song()
    first[1][2].sdvxin_chart_view = SdvxinChartView(
        id="130001", song_id=first[0].id, difficulty="MAS", end_index=""
    )
    view, data = make_view(first, make_song(2, difficulties=("BAS",)))
    data.chart_image = AsyncMock(return_value=b"chart image")
    await view.transition(interaction(), tab="view")
    await view.transition(interaction(), song_index=1, page=0)
    assert view.state == SongDetailsState(
        song_index=1, tab="overview", difficulty="BAS", network="chunithm_net"
    )
    assert view.message.edit.call_args.kwargs["embeds"][0].title == "Song 2"
    assert any(
        field.name == "分頁切換"
        for field in view.message.edit.call_args.kwargs["embeds"][0].fields
    )


@pytest.mark.asyncio
async def test_same_song_worlds_end_is_a_human_named_difficulty_choice_not_another_song(
    monkeypatch,
):
    normal, normal_charts = make_song(1, difficulties=("BAS", "MAS"))
    special, special_charts = make_song(8001, difficulties=("WE",))
    special.title, special.artist = normal.title, normal.artist
    special.genre, special.removed, special.available = "WORLD'S END", True, False
    special_charts[0].level = "狂☆☆☆☆"
    special_charts[0].id = 9001
    view, _ = make_view(
        (normal, normal_charts),
        (special, special_charts),
        variant_charts=(*normal_charts, *special_charts),
        initial_tab="chart",
    )
    await view._before_start()
    chooser = next(
        item
        for item in view.children
        if isinstance(item, discord.ui.Select)
        and item.custom_id == "song_details:difficulty"
    )
    assert chooser.placeholder == "選擇難度／特殊譜面"
    summary = next(
        field.value
        for field in view._info_embed(normal, normal_charts).fields
        if field.name == "譜面版本"
    )
    assert "一般譜面 · 收錄版本 X-VERSE · 可遊玩" in summary
    assert "WORLD'S END · 狂☆☆☆☆ · 收錄版本 X-VERSE · 已下架" in summary
    assert "8001" not in summary
    assert [option.value for option in chooser.options] == ["0:BAS", "0:MAS", "1:WE"]
    assert chooser.options[-1].label == "WORLD'S END · 狂☆☆☆☆"
    assert chooser.options[0].label.startswith("一般 ")
    assert "收錄版本 X-VERSE" in chooser.options[-1].description
    assert "已下架" in chooser.options[-1].description
    assert (
        view.previous_song not in view.children and view.next_song not in view.children
    )
    chooser._values = ["1:WE"]
    await chooser.callback(interaction())
    assert (
        view.songs[view.state.song_index].id == 8001 and view.state.difficulty == "WE"
    )
    assert (
        "WORLD'S END · 狂☆☆☆☆"
        in view.message.edit.call_args.kwargs["embeds"][-1].footer.text
    )
    assert (
        "收錄版本 X-VERSE · 已下架"
        in view.message.edit.call_args.kwargs["embeds"][-1].footer.text
    )
    from chuni_penguin.services.chart_reviews import ChartReviewService

    view.ctx.bot.database = object()
    monkeypatch.setattr(
        ChartReviewService,
        "public_summary",
        AsyncMock(return_value={"available": False, "minimum": 1}),
    )
    await view.transition(interaction(), tab="reviews")
    review_button = next(
        item
        for item in view.children
        if getattr(item, "placeholder", "") == "撰寫／管理評價"
    )
    assert review_button.chart.id == 9001 and review_button.chart.song_id == 8001
    chooser = next(
        item
        for item in view.children
        if getattr(item, "custom_id", "") == "song_details:difficulty"
    )
    chooser._values = ["0:MAS"]
    await chooser.callback(interaction())
    assert view.songs[view.state.song_index].id == 1 and view.state.difficulty == "MAS"
    assert "一般譜面" in view.message.edit.call_args.kwargs["embeds"][-1].footer.text
    assert len(view.to_components()) <= 5
    assert_limits(view.message.edit.call_args.kwargs)
    await view.deactivate()


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", ["const", "maxcombo"])
async def test_unknown_chart_values_do_not_produce_fabricated_analysis(unknown):
    song, charts = make_song(difficulties=("MAS",))
    setattr(charts[0], unknown, None)
    view, _ = make_view((song, charts))
    await view.transition(
        interaction(), tab="rating_table" if unknown == "const" else "borders"
    )
    embed = view.message.edit.call_args.kwargs["embeds"][0]
    assert "未知" in " ".join(str(field.value) for field in embed.fields)
    if unknown == "const":
        assert not any(field.name == "目標分數 → 單曲 Rating" for field in embed.fields)
    else:
        assert not any(field.name == "Score Deduction" for field in embed.fields)
    assert_limits(view.message.edit.call_args.kwargs)


@pytest.mark.asyncio
async def test_worlds_end_is_browsable_without_a_rating_value():
    view, _ = make_view(make_song(difficulties=("WE",)))
    await view.transition(interaction(), tab="rating_table")
    embed = view.message.edit.call_args.kwargs["embeds"][0]
    assert view.state.difficulty == "WE"
    assert any("不計入 Rating" in str(field.value) for field in embed.fields)
    assert not any(field.name == "目標分數 → 單曲 Rating" for field in embed.fields)


@pytest.mark.asyncio
async def test_missing_chart_data_does_not_fetch_remote_data():
    view, data = make_view(make_song(difficulties=()))
    data.scores = AsyncMock()
    await view.transition(interaction(), tab="scores")
    assert "沒有譜面資料" in view.message.edit.call_args.kwargs["embeds"][0].description
    data.scores.assert_not_awaited()


@pytest.mark.asyncio
async def test_score_is_filtered_by_song_and_selected_difficulty():
    view, data = make_view(make_song())

    def record(song_id, difficulty):
        return PersonalBest(
            song=ScoreSong(id=song_id, title="Song"),
            chart=ScoreChart(
                difficulty=Difficulty(difficulty), level="14+", internal_level=14.7
            ),
            score=1007500,
            clear_lamp=ClearLamp.clear,
            combo_lamp=ComboLamp.all_justice,
            rating=Decimal("16.70"),
            play_count=10,
            ajc_count=0,
        )

    data.scores = AsyncMock(
        return_value=SongScores(
            "Test network", [record(1, "MAS"), record(1, "EXP"), record(999, "MAS")]
        )
    )
    rendered = await view.render(SongDetailsState(tab="scores"))
    assert len(rendered.pages) == 1
    assert "Test network" in rendered.pages[0][0].footer.text
    assert "16.70" in rendered.pages[0][0].footer.text
    await view.transition(interaction(), tab="scores", difficulty="ULT")
    assert "尚無此難度" in view.message.edit.call_args.kwargs["embeds"][0].description


@pytest.mark.asyncio
async def test_failed_page_can_be_retried_and_does_not_expose_errors():
    view, data = make_view(make_song())
    data.scores = AsyncMock(
        side_effect=[commands.CommandError("SECRET-EXAMPLE"), SongScores("Test", ())]
    )
    await view.transition(interaction(), tab="scores")
    first = view.message.edit.call_args.kwargs
    assert "SECRET-EXAMPLE" not in str(first["embeds"][0].to_dict())
    assert "/account" in first["embeds"][0].description
    await view.transition(interaction(), tab="overview")
    await view.transition(interaction(), tab="scores")
    assert "尚無此難度" in view.message.edit.call_args.kwargs["embeds"][0].description
    assert data.scores.await_count == 2


@pytest.mark.asyncio
async def test_slow_old_request_cannot_overwrite_a_later_tab():
    view, data = make_view(make_song())
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_scores(*_):
        entered.set()
        await release.wait()
        return SongScores("Test", ())

    data.scores = slow_scores
    old = asyncio.create_task(view.transition(interaction(), tab="scores"))
    await entered.wait()
    await view.transition(interaction(), tab="chart")
    release.set()
    await old
    assert view.state.tab == "chart"
    assert view.message.edit.await_count == 1
    assert "Designer" in view.message.edit.call_args.kwargs["embeds"][0].description


@pytest.mark.asyncio
async def test_slow_acknowledgment_does_not_reorder_clicks():
    view, _ = make_view(make_song())
    first = interaction()
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_defer():
        entered.set()
        await release.wait()

    first.response.defer.side_effect = delayed_defer
    older = asyncio.create_task(view.transition(first, tab="chart"))
    await entered.wait()
    await view.transition(interaction(), tab="rating")
    release.set()
    await older
    assert view.state.tab == "rating"
    assert view.message.edit.await_count == 1


@pytest.mark.asyncio
async def test_chart_attachment_is_removed_when_switching_back_to_text():
    song, charts = make_song(difficulties=("MAS",))
    charts[0].sdvxin_chart_view = SdvxinChartView(
        id="130001", song_id=song.id, difficulty="MAS", end_index=""
    )
    view, data = make_view((song, charts))
    data.chart_image = AsyncMock(return_value=b"image bytes")
    await view.transition(interaction(), tab="view")
    image_kwargs = view.message.edit.call_args.kwargs
    assert image_kwargs["attachments"][0].filename == "chart.jpg"
    assert image_kwargs["embeds"][0].image.url == "attachment://chart.jpg"
    await view.transition(interaction(), tab="view", mirror=True)
    assert data.chart_image.call_args.kwargs["mirror"] is True
    await view.transition(interaction(), tab="chart")
    assert view.message.edit.call_args.kwargs["attachments"] == []
    assert not view.message.edit.call_args.kwargs["embeds"][0].image.url


@pytest.mark.asyncio
async def test_long_leaderboard_and_aliases_fit_discord_and_keep_all_entries():
    song, charts = make_song()
    song.aliases = [
        Alias(alias=f"alias-{i}-" + "a" * 200, guild_id=0) for i in range(60)
    ]
    view, data = make_view((song, charts))
    overview = await view.render(SongDetailsState())
    assert len(overview.pages) > 1
    assert "alias-59-" in "".join(page[0].description for page in overview.pages)
    entries = [
        LeaderboardEntry(
            position=i,
            player_name=f"Player {i}",
            score=1010000 - i,
            judgements=None,
            combo_lamp=None,
            clear_lamp=None,
            ajc_count=None,
            achieved_at=None,
        )
        for i in range(1, 24)
    ]
    data.leaderboard = AsyncMock(
        return_value=SongLeaderboard(
            "Test", Leaderboard(updated_at=datetime.now(UTC), ranking=entries)
        )
    )
    rendered = await view.render(SongDetailsState(tab="leaderboard"))
    assert len(rendered.pages) == 3
    assert "Player 23" in rendered.pages[-1][0].description
    for result in (overview, rendered):
        for index in range(len(result.pages)):
            result.state = replace_state = SongDetailsState(
                tab=result.state.tab, page=index
            )
            kwargs = view._kwargs(result, editing=True)
            assert view.state.page == replace_state.page
            assert_limits(kwargs)
    await view.transition(interaction(), tab="leaderboard", page=99)
    assert view.state.page == 2


@pytest.mark.asyncio
async def test_no_chunirec_link_is_added():
    song, charts = make_song()
    song.chunirec_id = "not-used"
    view, _ = make_view((song, charts))
    await view.transition(interaction(), tab="links")
    text = str(view.message.edit.call_args.kwargs["embeds"][0].to_dict())
    assert "wikiwiki" in text
    assert "youtube" in text
    assert "chunirec" not in text


@pytest.mark.asyncio
async def test_button_ids_stay_stable_after_a_page_update():
    view, _ = make_view(make_song())
    await view._before_start()
    original = {
        child.label: child.custom_id
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }
    await view.transition(interaction(), tab="chart")
    assert original == {
        child.label: child.custom_id
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }


@pytest.mark.asyncio
async def test_search_command_starts_the_unified_view(monkeypatch):
    from chuni_penguin.cogs.chunithm import search

    song, charts = make_song()
    ctx = make_context()
    ctx.guild = None
    ctx.user_config = SimpleNamespace(synthesis_alt_jacket="default")
    ctx.find_songs = AsyncMock(return_value=SimpleNamespace(songs=[song]))
    message = SimpleNamespace(edit=AsyncMock())
    ctx.respond_or_edit = AsyncMock(return_value=message)

    @asynccontextmanager
    async def typing():
        yield

    ctx.typing = typing
    ctx.bot.utils = SimpleNamespace()
    ctx.bot.services = SimpleNamespace(autocomplete=None)
    ctx.bot.get_cog = lambda _: None
    data = SongDetailsData(ctx)
    data._cache[("charts", song.id)] = tuple(charts)
    created = []

    def build(context, items, **kwargs):
        view = SongDetailsView(context, items, data=data, **kwargs)
        created.append(view)
        return view

    from chuni_penguin.ui import song_details

    monkeypatch.setattr(song_details, "SongDetailsView", build)
    await search.SearchCog(ctx.bot)._info_inner(ctx, query="song", detailed=True)
    ctx.find_songs.assert_awaited_once_with("song", load_global_aliases=True)
    assert ctx.respond_or_edit.await_count == 1
    assert ctx.respond_or_edit.call_args.kwargs["view"] is created[0]
    assert created[0].message is message
    assert "Designer" in ctx.respond_or_edit.call_args.kwargs["embeds"][0].description


@pytest.mark.asyncio
async def test_database_failure_is_contained_and_a_later_retry_can_work():
    view, data = make_view(make_song())
    charts = data.cached_charts(view.songs[0])
    data._cache.clear()
    data.charts = AsyncMock(side_effect=[RuntimeError("SECRET-EXAMPLE"), charts])
    first = await view._before_start()
    assert "SECRET-EXAMPLE" not in str(first["embeds"][0].to_dict())
    await view.transition(interaction(), tab="chart")
    assert "Designer" in view.message.edit.call_args.kwargs["embeds"][0].description


@pytest.mark.asyncio
async def test_other_users_including_owner_cannot_load_requesters_scores():
    view, _ = make_view(make_song())
    other = interaction(999)
    assert not await view.interaction_check(other)
    other.response.send_message.assert_awaited_once()
    view.ctx.bot.is_owner.assert_not_awaited()
    assert await view.interaction_check(interaction())


@pytest.mark.asyncio
async def test_timeout_disables_controls_and_prevents_further_loads():
    view, data = make_view(make_song())
    await view._before_start()
    data.close = AsyncMock()
    await view.on_timeout()
    assert view.children and all(item.disabled for item in view.children)
    assert view.is_finished()
    data.close.assert_awaited_once()
    last = interaction()
    await view.transition(last, tab="scores")
    last.response.defer.assert_not_awaited()
    assert last.response.send_message.call_args.kwargs["ephemeral"]
