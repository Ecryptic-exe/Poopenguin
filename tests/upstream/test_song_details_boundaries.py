import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import discord
import pytest

from chuni_penguin.config import Config, use_config
from chuni_penguin.ui._embed_pages import embed_pages
from chuni_penguin.ui.panel import session_for, show_song
from chuni_penguin.ui.song_details import (
    SongDetailsView,
    SongDifficultySelect,
    SongPageSelect,
)
from tests.test_panels import Message, assert_rows, interaction
from tests.test_panels import ctx as panel_context_fixture
from tests.upstream.test_song_details import make_song, make_view

ctx = panel_context_fixture


def test_removed_song_uses_only_a_publishable_jacket_url():
    from chuni_penguin.utils import get_jacket_url

    song, _ = make_song()
    song.available, song.removed = False, True
    with use_config(Config.from_mapping({})):
        assert get_jacket_url(song) is None
        song.jacket = "https://example.com/archived-jacket.jpg"
        assert get_jacket_url(song) == song.jacket


def test_reader_paginates_long_fields_without_mutating_or_repeating_them():
    card = discord.Embed(title="Chart", description="d" * 3900)
    for i in range(31):
        card.add_field(name=f"Note {i}", value=str(i) + ":" + "x" * 1150)
    original = card.to_dict()
    pages = [row[0] for row in embed_pages(card)]
    assert len(pages) > 2
    assert card.to_dict() == original
    assert "".join(page.description or "" for page in pages) == card.description
    for page in pages:
        assert len(page.fields) <= 25 and len(page) + 256 + 385 <= 6000
        assert all(len(f.value or "") <= 1024 for f in page.fields)
    for i in range(31):
        parts = [
            (f.value or "")
            for page in pages
            for f in page.fields
            if f.name == f"Note {i}"
        ]
        assert "".join(parts) == card.fields[i].value


@pytest.mark.asyncio
async def test_switch_to_missing_view_keeps_tab_clears_attachment_and_clamps_page():
    from chuni_penguin.database import SdvxinChartView
    from tests.upstream.test_song_details import interaction as click

    song, charts = make_song()
    charts[2].sdvxin_chart_view = SdvxinChartView(
        id="130001", song_id=1, difficulty="MAS", end_index=""
    )
    with use_config(Config.from_mapping({"fun": {"language": "chinese"}})):
        view, data = make_view((song, charts))
        data.chart_image = AsyncMock(return_value=b"fixture")
        await view.transition(cast(discord.Interaction, click()), tab="view")
        await view.transition(
            cast(discord.Interaction, click()), difficulty="ULT", page=7
        )
        assert view.message is not None and isinstance(view.message.edit, AsyncMock)
        payload = view.message.edit.call_args.kwargs
        assert view.state.tab == "view" and view.state.page == 0
        assert "ULTIMA" in payload["embeds"][0].author.name
        assert "尚無視譜" in payload["embeds"][0].description
        assert payload["attachments"] == [] and not payload["embeds"][0].image.url
        await view.deactivate()


@pytest.mark.asyncio
async def test_large_variant_family_keeps_all_charts_reachable():
    from tests.upstream.test_song_details import interaction as click

    variants = [make_song(i, difficulties=("WE",)) for i in range(1, 30)]
    charts = [pair[1][0] for pair in variants]
    view, _ = make_view(*variants, variant_charts=charts)
    await view._before_start()
    view.state = replace(view.state, song_index=27)
    await view.transition(cast(discord.Interaction, click()), page=0)
    pager = next(item for item in view.children if isinstance(item, SongPageSelect))
    pager._values = ["next_song"]
    await pager.callback(cast(discord.Interaction, click()))
    choose = next(
        item for item in view.children if isinstance(item, SongDifficultySelect)
    )
    assert choose.options[0].value == "28:WE"
    assert len(choose.options) <= 25
    assert_rows(view)
    await view.deactivate()


@pytest.mark.asyncio
async def test_public_song_uses_latest_share_preference_on_score_click(
    ctx, monkeypatch
):
    public, private = Message(private=False), Message(private=True)
    private.id = 202
    ctx.guild = SimpleNamespace(id=33)
    ctx.response = ctx.message = public
    monkeypatch.setattr(session_for(ctx), "message", public)
    card = await show_song(ctx, "Offline fixture")
    assert isinstance(card, SongDetailsView)
    before = list(public.payloads)
    changed = interaction(ctx)
    changed.original_response = AsyncMock(return_value=private)
    changed.edit_original_response = AsyncMock(side_effect=private.edit)
    assert not ctx.user_config.privacy_mode
    from chuni_penguin.database import UserConfig

    await ctx.bot.database.writer.merge(
        UserConfig(
            discord_id=ctx.author.id, privacy_mode=True, synthesis_alt_jacket="default"
        )
    )
    from chuni_penguin.services.song_details import SongDetailsData, SongScores
    from chuni_penguin.types import (
        Chart,
        ClearLamp,
        ComboLamp,
        Difficulty,
        PersonalBest,
        Song,
    )

    personal = PersonalBest(
        song=Song(id=card.songs[0].id, title="Offline fixture"),
        chart=Chart(difficulty=Difficulty(card.state.difficulty)),
        score=987654,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.none,
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def load(*args):
        entered.set()
        await release.wait()
        return SongScores("Test source", [personal])

    fetch = AsyncMock(side_effect=load)
    monkeypatch.setattr(SongDetailsData, "scores", fetch)
    selection = asyncio.create_task(
        card.transition(cast(discord.Interaction, changed), tab="scores", page=0)
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        duplicate = interaction(ctx)
        await card.transition(
            cast(discord.Interaction, duplicate), tab="scores", page=0
        )
        duplicate.response.send_message.assert_awaited_once()
        assert duplicate.response.send_message.call_args.kwargs["ephemeral"]
    finally:
        release.set()
        await selection
    fetch.assert_awaited_once()
    changed.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
    assert card.state.tab == "overview" and session_for(ctx).active_view is card
    assert public.payloads == before
    child = private.payloads[-1]["view"]
    assert (
        child.state.tab == "scores" and child.state.difficulty == card.state.difficulty
    )
    assert "987,654" in private.payloads[-1]["embeds"][0].description
    assert child.ctx.panel_session is not ctx.panel_session
    assert child.message is private
    await child.deactivate()
    await card.deactivate()


@pytest.mark.asyncio
async def test_snapshot_suppresses_unknown_clear_and_retains_mapping_and_time(ctx):
    import json

    from chuni_penguin.database import PersonalBest as DBPersonalBest
    from chuni_penguin.database import PlayerSource
    from chuni_penguin.ui.song_details import SongDetailsState

    canonical = (await ctx.find_songs("Offline fixture")).songs[0]
    source = "chunirec:jp2"
    await ctx.bot.database.writer.add(
        PlayerSource(
            discord_id=ctx.author.id,
            source=source,
            synced_at="2026-09-01T00:00:00+00:00",
            payload=json.dumps(
                {
                    "unmapped": 1,
                    "records": [
                        {"song_id": canonical.id, "diff": "MAS", "score": 975000},
                        {
                            "song_id": None,
                            "id": str(canonical.id),
                            "diff": "MAS",
                            "score": 1010000,
                            "is_clear": True,
                        },
                    ],
                }
            ),
        )
    )
    await ctx.bot.database.writer.add(
        DBPersonalBest(
            discord_id=ctx.author.id,
            network=source,
            song_id=canonical.id,
            difficulty="MAS",
            score=975000,
            clear_lamp=0,
            combo_lamp=0,
        )
    )
    card = await show_song(ctx, canonical.title)
    assert isinstance(card, SongDetailsView)
    frame = await card.render(SongDetailsState(tab="scores", network=source))
    embed = frame.pages[0][0]
    assert embed.description is not None
    assert "975,000" in embed.description and "1,010,000" not in embed.description
    assert "FAILED" not in embed.description and "CLEAR" not in embed.description
    assert "快照儲存於 <t:" in embed.description and "Chunirec jp2" in embed.description
    await card.deactivate()
