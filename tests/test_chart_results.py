# ruff: noqa: RUF001 - Traditional Chinese UI punctuation
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chuni_penguin.database import Chart, Song
from chuni_penguin.ui.chart_results import ChartResultSelect, ChartResultsView
from chuni_penguin.ui.panel import attach_navigation, session_for


class FakeMessage:
    def __init__(self, *, private=True):
        self.id = 101
        self.flags = SimpleNamespace(ephemeral=private)
        self.payloads = []

    async def edit(self, **kwargs):
        self.payloads.append(kwargs)
        return self


class FakeResponse:
    def __init__(self):
        self.done = False
        self.messages = []

    def is_done(self):
        return self.done

    async def defer(self, **_kwargs):
        self.done = True

    async def send_message(self, *args, **kwargs):
        self.done = True
        self.messages.append((args, kwargs))

    async def edit_message(self, **kwargs):
        self.done = True
        self.messages.append(((), kwargs))


class FakeContext:
    def __init__(self, *, section="find"):
        self.author = SimpleNamespace(id=11)
        self.command = SimpleNamespace(qualified_name=section)
        self.interaction = SimpleNamespace(namespace=None)
        self.response = self.message = FakeMessage()
        self.respond_or_edit = AsyncMock(return_value=self.response)


def fake_interaction(ctx, *, user_id=11):
    response = FakeResponse()
    message = ctx.response
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        message=message,
        original_response=AsyncMock(return_value=message),
        edit_original_response=AsyncMock(side_effect=message.edit),
    )


def charts(count=25):
    result = []
    for chart_id in range(1, count + 1):
        song = Song(
            id=1000 + chart_id,
            title=f"Fixture song {chart_id}",
            chunithm_catcode=0,
            genre="ORIGINAL",
            artist="Fixture artist",
            version="X-VERSE",
            available=chart_id % 2 == 1,
            removed=chart_id % 5 == 0,
        )
        result.append(
            Chart(
                id=chart_id,
                song_id=song.id,
                difficulty="MAS",
                level="14+",
                const=14.5,
                version="X-VERSE",
                song=song,
            )
        )
    return result


def item_by_custom_id(view, custom_id):
    return next(item for item in view.children if item.custom_id == custom_id)


def test_results_render_current_page_with_canonical_ids_and_human_labels():
    ctx = FakeContext()
    entries = charts(23)
    view = ChartResultsView(
        ctx,
        entries,
        title="篩選譜面",
        query_label="14+ · tags: jack",
        page=1,
    )

    select = next(item for item in view.children if isinstance(item, ChartResultSelect))
    assert [int(option.value) for option in select.options] == [
        chart.id for chart in entries[10:20]
    ]
    embed = view._kwargs()["embed"]
    assert "11. **14+ · MASTER** Fixture song 11" in embed.description
    assert "收錄版本：X-VERSE · 狀態：可遊玩" in embed.description
    assert "12. **14+ · MASTER** Fixture song 12" in embed.description
    assert "國際版未開放" in embed.description
    assert embed.footer.text == "14+ · tags: jack · 第 2/3 頁"


@pytest.mark.asyncio
async def test_select_opens_the_chart_with_the_matching_canonical_id(monkeypatch):
    from chuni_penguin.ui import panel

    ctx = FakeContext()
    entries = charts(12)
    view = ChartResultsView(ctx, entries, title="搜尋結果", page=1)
    select = next(item for item in view.children if isinstance(item, ChartResultSelect))
    expected = entries[10]
    select._values = [str(expected.id)]
    opener = AsyncMock()
    monkeypatch.setattr(panel, "open_chart_result", opener, raising=False)

    interaction = fake_interaction(ctx)
    await select.callback(interaction)

    opener.assert_awaited_once_with(ctx, interaction, expected, origin_view=view)


@pytest.mark.asyncio
async def test_invalid_or_off_page_chart_id_is_reported_without_opening(monkeypatch):
    from chuni_penguin.ui import panel

    ctx = FakeContext()
    entries = charts(12)
    view = ChartResultsView(ctx, entries, title="搜尋結果", page=1)
    opener = AsyncMock()
    monkeypatch.setattr(panel, "open_chart_result", opener, raising=False)
    interaction = fake_interaction(ctx)

    await view.select_chart(interaction, str(entries[0].id))

    opener.assert_not_awaited()
    assert interaction.response.messages[0][0][0] == "這個譜面結果已失效，請重新搜尋。"
    assert interaction.response.messages[0][1]["ephemeral"] is True


@pytest.mark.asyncio
async def test_pagination_edits_the_same_message_and_rebuilds_current_page_options():
    ctx = FakeContext()
    entries = charts(23)
    view = ChartResultsView(ctx, entries, title="搜尋結果")
    view.message = ctx.response
    session_for(ctx).message = ctx.response
    interaction = fake_interaction(ctx)

    await view.change_page(interaction, 1)

    assert view.page == 1
    assert interaction.edit_original_response.await_count == 1
    edited = ctx.response.payloads[-1]
    assert edited["view"] is view
    assert [int(option.value) for option in next(
        item for item in view.children if isinstance(item, ChartResultSelect)
    ).options] == [chart.id for chart in entries[10:20]]
    assert edited["embed"].footer.text == "第 2/3 頁"


def test_components_stay_within_discord_limits_and_own_their_navigation():
    ctx = FakeContext()
    view = ChartResultsView(ctx, charts(27), title="搜尋結果")

    attach_navigation(ctx, view)
    components = view.to_components()

    assert view._panel_own_navigation is True
    assert len(components) <= 5
    assert all(len(row["components"]) <= 5 for row in components)
    assert all(len(item.get("options", [])) <= 25 for row in components for item in row["components"])
    assert len([item for item in view.children if isinstance(item, ChartResultSelect)]) == 1
    assert [int(option.value) for option in next(
        item for item in view.children if isinstance(item, ChartResultSelect)
    ).options] == list(range(1, 11))


@pytest.mark.asyncio
async def test_owner_and_inactive_guards_report_stale_controls():
    ctx = FakeContext()
    view = ChartResultsView(ctx, charts(1), title="搜尋結果")

    unauthorized = fake_interaction(ctx, user_id=22)
    assert await view.interaction_check(unauthorized) is False
    assert unauthorized.response.messages

    stale = fake_interaction(ctx)
    view._panel_inactive = True
    assert await view.interaction_check(stale) is False
    assert stale.response.messages[0][0][0] == "此譜面結果頁已失效，請重新搜尋。"
    assert stale.response.messages[0][1]["ephemeral"] is True


@pytest.mark.asyncio
async def test_expired_controls_are_reported():
    ctx = FakeContext()
    view = ChartResultsView(ctx, charts(1), title="搜尋結果")
    view.stop()
    interaction = fake_interaction(ctx)

    assert await view.interaction_check(interaction) is False
    assert interaction.response.messages[0][0][0] == "此譜面結果頁已失效，請重新搜尋。"


@pytest.mark.asyncio
async def test_modify_and_return_buttons_delegate_to_panel_routes(monkeypatch):
    from chuni_penguin.ui import panel

    ctx = FakeContext(section="tags")
    view = ChartResultsView(ctx, charts(1), title="標籤結果")
    navigate = AsyncMock()
    monkeypatch.setattr(panel, "navigate", navigate)
    interaction = fake_interaction(ctx)

    await item_by_custom_id(view, "chart_results:modify").callback(interaction)
    navigate.assert_awaited_once_with(ctx, interaction, "tags")

    navigate.reset_mock()
    await item_by_custom_id(view, "chart_results:song_home").callback(interaction)
    navigate.assert_awaited_once_with(ctx, interaction, "song_home")
