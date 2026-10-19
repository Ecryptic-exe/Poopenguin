from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from chuni_penguin.database import Alias, Chart, Song
from chuni_penguin.ui.chart_results import ChartResultsView
from chuni_penguin.ui.panel import (
    PanelMenu,
    PanelRoute,
    navigate,
    session_for,
    show_account,
    show_panel,
    show_song,
)
from chuni_penguin.ui.song_details import SongDetailsView
from tests.test_panels import Message, assert_rows, interaction
from tests.test_panels import ctx as context_fixture

ctx = context_fixture


async def seed_results(ctx):
    async def seed(db):
        for index in range(12):
            db.add(
                Song(
                    id=50001 + index,
                    title=f"Filter fixture {index:02}",
                    artist="Fixture",
                    chunithm_catcode=0,
                    genre="ORIGINAL",
                    version="X-VERSE",
                    available=True,
                    removed=False,
                )
            )
        await db.flush()
        for index in range(12):
            db.add(
                Chart(
                    id=100001 + index,
                    song_id=50001 + index,
                    difficulty="MAS",
                    level="14",
                    const=14.0,
                    maxcombo=100,
                )
            )

    await ctx.bot.database.writer.execute_fn(seed)
    await ctx.bot.utils.aliases.reload()


@pytest.mark.asyncio
async def test_account_actions_follow_selected_source_and_do_not_duplicate(ctx):
    view = await show_account(ctx)
    actions = {item.action for item in view.children if hasattr(item, "action")}
    assert (
        "login" in actions
        and "run_sync" not in actions
        and "account_settings" in actions
    )
    await navigate(view.ctx, interaction(ctx), "account_settings")
    settings = session_for(ctx).active_view
    actions = {item.action for item in settings.children if hasattr(item, "action")}
    assert not actions.intersection({"token", "confirm:logout", "confirm:unlink"})
    assert "account" in actions and "confirm:delete_account" in actions
    session_for(ctx).route = PanelRoute("account", source="chunilib:export")
    view = await show_account(ctx)
    actions = {item.action for item in view.children if hasattr(item, "action")}
    assert (
        "import" in actions
        and "run_sync" not in actions
        and "link_source" not in actions
    )
    menu = next(item for item in view.children if isinstance(item, PanelMenu))
    assert {option.value for option in menu.options} == {"b50", "source_scores"}
    for candidate in (view, settings):
        assert_rows(candidate)
        candidate.stop()


@pytest.mark.asyncio
async def test_find_to_exact_chart_and_back_keeps_filters_page_and_public_anchor(ctx):
    await seed_results(ctx)
    public = Message(private=False)
    ctx.message = ctx.response = public
    session_for(ctx).message = public
    await show_panel(ctx, "chart")
    await navigate(
        ctx,
        interaction(ctx),
        "find",
        values={"level": "14", "difficulty": "MAS", "version": "X-VERSE"},
    )
    results = session_for(ctx).active_view
    assert isinstance(results, ChartResultsView)
    await results.change_page(interaction(ctx), 1)
    selected = results.page_charts[0]
    await results.select_chart(interaction(ctx), str(selected.id))
    card = session_for(ctx).active_view
    assert isinstance(card, SongDetailsView)
    assert card.songs[card.state.song_index].id == selected.song_id
    assert card.state.difficulty == selected.difficulty and card.state.tab == "chart"
    assert session_for(ctx).message is public
    await navigate(card.ctx, interaction(ctx), "return_results")
    restored = session_for(ctx).active_view
    assert isinstance(restored, ChartResultsView) and restored.page == 1
    assert restored.page_charts[0].id == selected.id
    assert session_for(ctx).parameters["find"] == {
        "level": "14",
        "difficulty": "MAS",
        "version": "X-VERSE",
    }
    assert session_for(ctx).message is public
    assert_rows(restored)
    restored.stop()


@pytest.mark.asyncio
async def test_tag_results_use_canonical_chart_ids_and_preserve_tag_on_return(
    ctx, monkeypatch
):
    async with ctx.bot.begin_db_read() as db:
        chart = await db.scalar(select(Chart))
    monkeypatch.setattr(
        ctx.bot.phase1_charts,
        "tag_matches",
        AsyncMock(return_value=[(1, {"chart_id": chart.id})]),
    )
    await show_panel(ctx, "chart")
    await navigate(ctx, interaction(ctx), "tags", values={"tag": "交互"})
    results = session_for(ctx).active_view
    assert results.charts[0].id == chart.id
    await results.select_chart(interaction(ctx), str(chart.id))
    card = session_for(ctx).active_view
    await navigate(card.ctx, interaction(ctx), "return_results")
    assert session_for(ctx).route.section == "tags"
    assert session_for(ctx).parameters["tags"] == {"tag": "交互"}
    session_for(ctx).active_view.stop()


@pytest.mark.asyncio
async def test_alias_modal_uses_selected_song_then_returns_same_tab_source(ctx):
    ctx.guild = SimpleNamespace(id=33)
    view = await show_song(ctx, "Offline fixture", tab="chart")
    await view.transition(interaction(ctx), network="chunilib:export")
    selected_id = view.songs[view.state.song_index].id
    await navigate(view.ctx, interaction(ctx), "alias")
    aliases = session_for(ctx).active_view
    changed = interaction(ctx)
    await navigate(aliases.ctx, changed, "alias_add")
    modal = changed.response.send_modal.call_args.args[0]
    assert set(modal.inputs) == {"added_alias"}
    modal.inputs["added_alias"]._value = "context shortcut"
    await modal.on_submit(interaction(ctx))
    async with ctx.bot.begin_db_read() as db:
        alias = await db.scalar(select(Alias).where(Alias.alias == "context shortcut"))
        assert alias.song_id == selected_id and alias.guild_id == 33
    await navigate(session_for(ctx).active_view.ctx, interaction(ctx), "song_return")
    card = session_for(ctx).active_view
    assert card.state.tab == "chart" and card.state.network == "chunilib:export"
    assert card.songs[card.state.song_index].id == selected_id
    await card.deactivate()


@pytest.mark.asyncio
async def test_failure_retry_preserves_operation_and_can_recover(ctx, monkeypatch):
    from chuni_penguin.services.player_sources import SourceError

    await show_account(ctx)
    sync = AsyncMock(side_effect=[SourceError("來源暫時無法連線"), "同步完成"])
    monkeypatch.setattr(ctx.bot.get_cog("V2Players"), "sync_native", sync)
    await navigate(ctx, interaction(ctx), "run_sync")
    assert session_for(ctx).retry_action["action"] == "run_sync"
    await navigate(session_for(ctx).active_view.ctx, interaction(ctx), "retry_last")
    assert sync.await_count == 2
    assert "同步完成" in ctx.message.payloads[-1]["embed"].description
    assert session_for(ctx).retry_action is None
    session_for(ctx).active_view.stop()


@pytest.mark.asyncio
async def test_private_link_updates_public_status_without_exposing_submitted_token(
    ctx, monkeypatch
):
    from chuni_penguin.database import PlayerSource

    public = Message(private=False)
    ctx.message = ctx.response = public
    session_for(ctx).message = public
    session_for(ctx).route = PanelRoute("account", source="chunirec:jp2")
    await show_account(ctx)
    changed = interaction(ctx)
    changed.message = public
    await navigate(ctx, changed, "link_source")
    modal = changed.response.send_modal.call_args.args[0]
    secret = "synthetic-private-token-never-publish"
    modal.token._value = secret

    async def linked(*args, **kwargs):
        await ctx.bot.database.writer.add(
            PlayerSource(
                discord_id=11,
                source="chunirec:jp2",
                credential="test-encrypted",
                status="synced",
            )
        )
        return {"records": [], "mapped": 0, "unmapped": 0}

    monkeypatch.setattr(ctx.bot.get_cog("V2Players").sources, "sync_chunirec", linked)
    submitted = interaction(ctx)
    submitted.message = None
    private = Message(private=True)
    private.id = 202
    submitted.original_response = AsyncMock(return_value=private)
    await modal.on_submit(submitted)
    assert session_for(ctx).message is public
    assert "已連結" in public.payloads[-1]["embed"].fields[0].value
    assert secret not in str(public.payloads) and "test-encrypted" not in str(
        public.payloads
    )
    assert modal.token.value == ""
    assert session_for(ctx).active_view is not session_for(modal.ctx).active_view
    session_for(ctx).active_view.stop()
    session_for(modal.ctx).active_view.stop()


@pytest.mark.asyncio
async def test_song_compare_reads_selected_canonical_record_and_returns_without_search(
    ctx, monkeypatch
):
    view = await show_song(ctx, "Offline fixture", tab="chart")
    song_id = view.songs[view.state.song_index].id
    difficulty = view.state.difficulty
    players = ctx.bot.get_cog("V2Players")
    rows = [
        {
            "id": str(song_id),
            "song_id": song_id,
            "title": "Offline fixture",
            "diff": difficulty,
            "score": 1000000,
        },
        {
            "id": "9999",
            "song_id": 9999,
            "title": "Unrelated",
            "diff": difficulty,
            "score": 999999,
        },
    ]
    exact = AsyncMock(return_value=rows)
    fuzzy = AsyncMock(side_effect=AssertionError("Must not rerun fuzzy search"))
    monkeypatch.setattr(players, "source_rows", exact)
    monkeypatch.setattr(players, "matching_rows", fuzzy)
    await navigate(
        view.ctx,
        interaction(ctx),
        "compare",
        values={"user_id": "11", "query": "Offline fixture"},
    )
    assert exact.await_count == 2 and all(
        call.kwargs["song_id"] == song_id for call in exact.call_args_list
    )
    assert "Unrelated" not in str(ctx.message.payloads[-1])
    await navigate(session_for(ctx).active_view.ctx, interaction(ctx), "song_return")
    restored = session_for(ctx).active_view
    assert (
        restored.state == view.state
        and restored.songs[restored.state.song_index].id == song_id
    )
    await restored.deactivate()
