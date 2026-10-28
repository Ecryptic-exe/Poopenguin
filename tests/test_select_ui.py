from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from chuni_penguin.ui.panel import SongPanelSelect, navigate, session_for, show_song
from chuni_penguin.ui.song_details import SongMoreSelect
from chuni_penguin.ui.song_search import SongSearchActionSelect, SongSearchView
from tests.test_panels import Message, assert_rows, interaction
from tests.test_panels import ctx as context_fixture

ctx = context_fixture


@pytest.mark.asyncio
async def test_reviews_combine_poopenguin_notes_and_anonymous_summary_for_selected_chart(
    ctx, monkeypatch
):
    annotations = AsyncMock(
        return_value=[
            {
                "tags": ["交互"],
                "sss_stars": 3,
                "note": "既有玩家心得",
                "links": [],
                "match_method": "agent_reviewed",
                "sheet": "14+",
                "excel_row": 23,
            }
        ]
    )
    semantic = Mock(
        side_effect=AssertionError("Unrequested semantic UI must stay hidden")
    )
    monkeypatch.setattr(ctx.bot.phase1_charts, "annotations_for", annotations)
    monkeypatch.setattr(ctx.bot.phase1_charts, "semantic_for", semantic)
    public = Message(private=False)
    ctx.message = ctx.response = public
    session_for(ctx).message = public
    view = await show_song(ctx, "Offline fixture", tab="reviews")
    assert session_for(ctx).message is public
    chart = annotations.call_args.args[0]
    assert chart.song_id == view.songs[view.state.song_index].id and chart.id > 0
    fields = {
        f.name: f.value for embed in public.payloads[-1]["embeds"] for f in embed.fields
    }
    assert "尚無評價" in fields
    assert "既有玩家心得" in fields["社群譜面資料"]
    assert "Poopenguin · Luna 核對 · 14+ 第 23 列" in fields["社群譜面資料"]
    semantic.assert_not_called()
    menu = next(c for c in view.children if isinstance(c, SongMoreSelect))
    assert [o.value for o in menu.options] == [
        "tab:view",
        "tab:scores",
        "tab:reviews",
        "tab:overview",
        "tab:rating",
        "tab:borders",
        "panel:alias",
        "action:correct",
    ]
    assert_rows(view)
    await view.deactivate()


@pytest.mark.asyncio
async def test_wrong_song_select_preserves_query_tab_exclusion_and_public_anchor(
    ctx, monkeypatch
):
    import chuni_penguin.ui.song_search as search

    view = await show_song(ctx, "Offline fixture", tab="scores")
    public = ctx.response
    selected_id = view.songs[view.state.song_index].id
    query = view.search_query
    discover = AsyncMock()
    monkeypatch.setattr(search, "discover_song", discover)
    menu = next(c for c in view.children if isinstance(c, SongMoreSelect))
    menu._values = ["action:correct"]
    await menu.callback(interaction(ctx))
    assert discover.call_args.args[1] == query
    assert discover.call_args.kwargs == {
        "tab": "scores",
        "correction": True,
        "exclude_song_id": selected_id,
        "excluded_song_ids": (),
    }
    assert session_for(ctx).message is public
    assert view._expired


@pytest.mark.asyncio
async def test_candidate_recovery_uses_select_and_retains_score_entry(ctx):
    from chuni_penguin.services.song_discovery import DiscoveryResult, SongCandidate

    card = await show_song(ctx, "Offline fixture")
    song = card.songs[0]
    await card.deactivate()
    result = DiscoveryResult("fi", (SongCandidate(song, 90, "fuzzy"),), True, False)
    view = SongSearchView(ctx, result, tab="scores")
    assert [c.label for c in view.children if isinstance(c, discord.ui.Button)] == [
        "返回主選單",
        "關閉"
    ]
    menu = next(c for c in view.children if isinstance(c, SongSearchActionSelect))
    menu._values = ["retry"]
    changed = interaction(ctx)
    await menu.callback(changed)
    modal = changed.response.send_modal.call_args.args[0]
    assert modal.action == "score"
    assert_rows(view)
    modal.stop()
    view.stop()


@pytest.mark.asyncio
async def test_alias_remove_returns_to_same_song_context_without_extra_buttons(ctx):
    from sqlalchemy import select

    from chuni_penguin.database import Alias

    ctx.guild = SimpleNamespace(id=33)
    public = Message(private=False)
    ctx.message = ctx.response = public
    session_for(ctx).message = public
    card = await show_song(ctx, "Offline fixture")
    await navigate(card.ctx, interaction(ctx), "alias")
    await navigate(
        session_for(ctx).active_view.ctx,
        interaction(ctx),
        "alias_add",
        values={"added_alias": "remove shortcut"},
    )
    await navigate(
        session_for(ctx).active_view.ctx,
        interaction(ctx),
        "alias_remove",
        values={"removed_alias": "remove shortcut"},
    )
    view = session_for(ctx).active_view
    assert session_for(ctx).route.section == "alias"
    assert session_for(ctx).message is public
    assert [c.label for c in view.children if isinstance(c, discord.ui.Button)] == [
        "返回主選單",
        "關閉"
    ]
    menu = next(c for c in view.children if isinstance(c, SongPanelSelect))
    assert {o.value for o in menu.options} == {
        "alias_add",
        "alias_remove",
        "alias_reload",
        "song_return",
    }
    async with ctx.bot.begin_db_read() as db:
        assert (
            await db.scalar(select(Alias).where(Alias.alias == "remove shortcut"))
            is None
        )
    assert_rows(view)
    await navigate(view.ctx, interaction(ctx), "song_return")
    assert session_for(ctx).active_view.songs[0].id == card.songs[0].id
    await session_for(ctx).active_view.deactivate()
