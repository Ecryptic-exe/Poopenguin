import asyncio
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import discord
import httpx
import pytest
from discord.ext.commands import Context

from chuni_penguin.adapters.base import NetworkAdapter
from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.database import PlayerSource
from chuni_penguin.services.native_scores import PartialScoreSync
from chuni_penguin.services.player_sources import PlayerSourceService, SourceError
from chuni_penguin.types import (
    Chart,
    ClearLamp,
    ComboLamp,
    Difficulty,
    Profile,
    RecentScore,
    Song,
)
from chuni_penguin.ui.account_state import partial_sync_notice
from chuni_penguin.ui.b30n20 import B30N20View
from chuni_penguin.ui.imported_b50 import imported_b50_pages
from chuni_penguin.ui.panel import PanelMenu, PanelRoute, SourceSelect, session_for
from chuni_penguin.ui.recent import RecentRecordsView
from tests.test_panels import assert_rows
from tests.test_panels import ctx as ctx_fixture
from tests.test_panels import interaction as panel_interaction
from tests.test_phase1 import database as database_fixture

database = database_fixture
ctx = ctx_fixture


def play(index, *, track=None, rating=None):
    return RecentScore(
        song=Song(id=index + 1, title=f"Title {index} " + "*長" * 2500, jacket_url=""),
        chart=Chart(difficulty=Difficulty.master, level="14", internal_level=14),
        score=1_000_000 + index,
        track_no=track,
        rating=rating,
        combo_lamp=ComboLamp.none,
        clear_lamp=ClearLamp.clear,
    )


def interaction():
    click = create_autospec(discord.Interaction, instance=True)
    click.response = SimpleNamespace(is_done=lambda: False, defer=AsyncMock())
    return click


def context():
    ctx = create_autospec(Context, instance=True)
    ctx.guild = None
    return ctx


def recent_view(scores):
    manager = create_autospec(AbstractAsyncContextManager, instance=True)
    client = create_autospec(NetworkAdapter, instance=True)
    client.NAME, client.SUPPORTS_DETAILED_RECENT_SCORE = "NET fixture", True
    view = RecentRecordsView(
        context(), 1, scores, client, manager, Profile(username="*P*")
    )
    return view, client, manager


@pytest.mark.asyncio
async def test_empty_recent_closes_account_and_has_no_invalid_select():
    view, _, manager = recent_view([])
    payload = await view._before_start()
    embed = payload.get("embed")
    assert embed is not None and embed.description
    assert "NET fixture" in embed.description and "no recent plays" in embed.description
    assert view.dropdown not in view.children
    await view.close_network()
    manager.__aexit__.assert_awaited_once()
    view.stop()


@pytest.mark.asyncio
async def test_old_detail_cannot_replace_next_credit_or_repeat_fetch(monkeypatch):
    records = [play(i, track=track) for i, track in enumerate((2, 1, 3, 2, 1))]
    view, client, manager = recent_view(records)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(record):
        entered.set()
        await release.wait()
        return record

    client.get_detailed_recent_score = AsyncMock(side_effect=fetch)
    edit = AsyncMock()
    monkeypatch.setattr(view, "edit_message", edit)
    assert len(view.dropdown.options[0].label) == 100
    assert view.dropdown.options[0].description == "MASTER · 1,000,000"
    view.dropdown._values = ["0"]
    click = asyncio.create_task(view.dropdown.callback(interaction()))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await view.dropdown.callback(interaction())
    await view.show_page(interaction(), 1)
    release.set()
    await click
    client.get_detailed_recent_score.assert_awaited_once_with(records[0])
    assert view.current_page == 1
    assert edit.await_args is not None
    output = edit.await_args.kwargs
    assert (
        len(output["embeds"]) == 3
        and "NET fixture" in output["content"]
        and r"\*P\*" in output["content"]
    )
    assert sum(map(len, output["embeds"])) <= 6000
    assert [o.value for o in view.dropdown.options] == ["2", "3", "4"]
    view.dropdown._values = ["3"]
    await view.dropdown.callback(interaction())
    assert not view.back_to_plays.disabled
    await view.back_to_plays.callback(interaction())
    assert view.current_page == 1 and view.back_to_plays.disabled
    await view.close_network()
    manager.__aexit__.assert_awaited_once()
    view.stop()


@pytest.mark.asyncio
async def test_classic_frames_restore_page_controls_and_preserve_unknowns(monkeypatch):
    view = B30N20View(
        context(), [play(i, rating=Decimal("10")) for i in range(8)], [play(9)]
    )
    edit = AsyncMock()
    monkeypatch.setattr(view, "edit_message", edit)
    assert view.jump_to_page not in view.children
    await view.show_page(interaction(), 0, frame=view.best30)
    assert view.jump_to_page in view.children and view.jump_to_page.label == "1/3"
    await view.show_page(interaction(), 2)
    assert (
        edit.await_args is not None and "Best 30" in edit.await_args.kwargs["content"]
    )
    assert (
        view.best30.average == Decimal("2.6666")
        and view.new20.average is None
        and view.rating is None
    )
    await view.show_page(interaction(), 0, frame=view.new20)
    assert view.jump_to_page not in view.children and view.show_new20.disabled
    empty = B30N20View(context(), [play(0, rating=Decimal("10"))], [])
    assert empty.source is empty.best30 and empty.rating == Decimal("0.20")
    empty.stop()
    view.stop()


def test_imported_b50_same_rating_order_snapshot_and_zero():
    ranked = [
        {
            "title": f"{i:02d} **title** @everyone",
            "score": 1_000_000,
            "diff": "MAS",
            "source_const": 14.5,
        }
        for i in range(51)
    ]
    ranked.insert(
        0, {"title": "No verified constant", "score": 1_010_000, "diff": "ULT"}
    )
    pages = imported_b50_pages(
        "english", {"records": ranked, "rating": 0}, "chunirec:jp", "2026-01-01"
    )
    assert len(pages) == 10
    assert all(
        "Source rating: 0" in (p.description or "")
        and "chunirec:jp" in (p.footer.text or "")
        and len(p) <= 6000
        for p in pages
    )
    assert "1. 00" in (pages[0].description or "") and r"\*\*title\*\*" in (
        pages[0].description or ""
    )
    assert "50. 49" in (pages[-1].description or "") and "Page 10/10" in (
        pages[-1].footer.text or ""
    )
    assert all(
        "No verified" not in (p.description or "")
        and "@everyone" not in (p.description or "")
        for p in pages
    )
    assert (
        imported_b50_pages("english", {"records": ranked[:1]}, "chunirec:jp", None)
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_detail", [True, False])
async def test_native_partial_sync_reports_completed_work_and_preserves_last_success(
    database, tmp_path, fail_detail
):
    service = PlayerSourceService(database, vault_path=tmp_path / "key")
    await database.writer.add(
        PlayerSource(
            discord_id=11, source="net", status="synced", synced_at="previous success"
        )
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def detail(record):
        if record == 2 and fail_detail:
            msg = "secret-url"
            raise OSError(msg)

    async def recent():
        entered.set()
        await release.wait()
        return [1, 2, 3]

    client = SimpleNamespace(
        NAME="test source",
        SUPPORTS_DETAILED_RECENT_SCORE=True,
        get_recent_scores=recent,
        get_minimal_profile=AsyncMock(),
        get_detailed_recent_score=detail,
        get_all_personal_bests=AsyncMock(side_effect=OSError("secret-url")),
    )

    @asynccontextmanager
    async def connection():
        yield client

    task = asyncio.create_task(service.sync_native(11, "net", connection()))
    await asyncio.wait_for(entered.wait(), timeout=5)
    with pytest.raises(SourceError, match="already in progress"):
        await service.sync_native(11, "net", connection())
    release.set()
    with pytest.raises(PartialScoreSync) as err:
        await task
    full = partial_sync_notice("english", err.value)
    assert (
        "Details completed for 1/3" in full
        if fail_detail
        else "Personal bests could not be fully retrieved" in full
    )
    assert "secret-url" not in full
    assert client.get_all_personal_bests.await_count == (0 if fail_detail else 1)
    stored = await service.get(11, "net")
    assert (
        stored.status == "partial; recent scores read"
        and stored.synced_at == "previous success"
    )
    assert not service.native_sync_active(11)


@pytest.mark.asyncio
async def test_recent_http_error_cannot_be_shown_as_empty_history():
    adapter = ChunithmNetAdapter.__new__(ChunithmNetAdapter)
    async with httpx.AsyncClient(
        base_url="https://fixture.invalid",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, text="unavailable")
        ),
    ) as client:
        adapter._client = client
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await adapter.get_recent_scores()
    assert caught.value.response.status_code == 503


@pytest.mark.asyncio
async def test_recent_detail_keeps_account_navigation_and_private_message(ctx):
    session_for(ctx).route = PanelRoute("recent", source="net")
    scores = [play(i) for i in range(4)]
    manager = create_autospec(AbstractAsyncContextManager, instance=True)
    client = create_autospec(NetworkAdapter, instance=True)
    client.NAME = "Fixture network"
    client.SUPPORTS_DETAILED_RECENT_SCORE = False
    view = RecentRecordsView(
        ctx, 11, scores, client, manager, Profile(username="Owner")
    )
    await view.start(ephemeral=True)
    view.dropdown._values = ["1"]
    click = cast(discord.Interaction, panel_interaction(ctx))
    assert await view.interaction_check(click)
    await view.dropdown.callback(click)
    assert view.message is ctx.message and ctx.message.flags.ephemeral
    assert not view.back_to_plays.disabled
    assert any(isinstance(control, SourceSelect) for control in view.children)
    assert any(isinstance(control, PanelMenu) for control in view.children)
    assert_rows(view)
    manager.__aexit__.assert_awaited_once()
    view.stop()
