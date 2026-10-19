import struct
import zlib
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from chuni_penguin.database import Chart
from chuni_penguin.ui.panel import session_for, show_song
from chuni_penguin.ui.song_details import (
    SongDetailsView,
    SongDifficultySelect,
    SongMoreSelect,
)
from tests.test_panels import Message, assert_rows, interaction
from tests.test_panels import ctx as context_fixture

ctx = context_fixture


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    + _png_chunk(b"IEND", b"")
)


def _capture_attachments(message: Message) -> None:
    original_edit = message.edit
    message.attachment_snapshots = []

    async def edit(**kwargs):
        files = kwargs.get("attachments", kwargs.get("files", ()))
        captured = []
        for file in files:
            stream = getattr(file, "fp", None)
            if stream is None:
                continue
            position = stream.tell()
            stream.seek(0)
            captured.append((file.filename, stream.read()))
            stream.seek(position)
        message.attachment_snapshots.append(captured)
        return await original_edit(**kwargs)

    message.edit = edit


async def _public_song(ctx):
    public = Message(private=False)
    _capture_attachments(public)
    ctx.message = ctx.response = public
    session_for(ctx).message = public
    view = await show_song(ctx, "Offline fixture")
    assert isinstance(view, SongDetailsView)
    return public, view


async def _select_more(view, ctx, public, value):
    menu = next(item for item in view.children if isinstance(item, SongMoreSelect))
    menu._values = [value]
    changed = interaction(ctx)
    changed.message = public
    await menu.callback(changed)
    return next(item for item in view.children if isinstance(item, SongMoreSelect))


def _assert_valid_png(filename: str, content: bytes) -> None:
    assert filename == "score-distribution.png"
    with Image.open(BytesIO(content)) as image:
        assert image.format == "PNG"
        image.verify()


def _origin() -> dict:
    return {
        "players": 73,
        "observed_at": "2026-10-18T12:00:00Z",
        "source_url": "https://chunirec.net/",
        "source_kind": "live",
    }


@pytest.mark.asyncio
async def test_distribution_select_renders_canonical_chart_png_on_same_public_message(
    ctx, monkeypatch
):
    extras = ctx.bot.phase1_charts
    analysis = AsyncMock(return_value=(_origin(), PNG))
    monkeypatch.setattr(extras, "analysis", analysis)
    monkeypatch.setattr(extras, "statistic_for", Mock(return_value=None))
    public, view = await _public_song(ctx)

    menu = next(item for item in view.children if isinstance(item, SongMoreSelect))
    assert any(option.value == "tab:rating" for option in menu.options)
    menu = await _select_more(view, ctx, public, "tab:rating")

    chart = analysis.await_args.args[0]
    assert isinstance(chart, Chart)
    assert chart.id > 0
    assert chart.song_id == 900000001
    assert chart.difficulty == "MAS"
    assert chart.song.title == "Offline fixture"
    assert view.state.tab == "rating"
    assert session_for(ctx).message is public
    assert view.message is public

    payload = public.payloads[-1]
    embed = payload["embeds"][0]
    assert embed.image.url == "attachment://score-distribution.png"
    fields = {field.name: field.value for field in embed.fields}
    assert "73" in fields["樣本與日期"]
    assert "2026-10-18" in fields["樣本與日期"]
    assert "https://chunirec.net/" in fields["統計來源"]
    assert len(payload["attachments"]) == 1
    assert payload["attachments"][0].filename == "score-distribution.png"
    _assert_valid_png(*public.attachment_snapshots[-1][0])
    assert any(
        option.value == "tab:rating" and option.default for option in menu.options
    )
    assert_rows(view)
    await view.deactivate()


@pytest.mark.asyncio
async def test_distribution_difficulty_change_loads_second_chart_without_bundle(
    ctx, monkeypatch
):
    extras = ctx.bot.phase1_charts
    analysis = AsyncMock(return_value=(_origin(), PNG))
    statistic_for = Mock(return_value=None)
    monkeypatch.setattr(extras, "analysis", analysis)
    monkeypatch.setattr(extras, "statistic_for", statistic_for)
    public, view = await _public_song(ctx)
    await _select_more(view, ctx, public, "tab:rating")

    difficulty = next(
        item for item in view.children if isinstance(item, SongDifficultySelect)
    )
    assert any(option.value == "ULT" for option in difficulty.options)
    difficulty._values = ["ULT"]
    changed = interaction(ctx)
    changed.message = public
    await difficulty.callback(changed)

    assert analysis.await_count == 2
    first_chart, second_chart = [call.args[0] for call in analysis.await_args_list]
    assert [
        (chart.song_id, chart.difficulty) for chart in (first_chart, second_chart)
    ] == [
        (900000001, "MAS"),
        (900000001, "ULT"),
    ]
    assert all(
        chart.id > 0 and chart.song.title == "Offline fixture"
        for chart in (first_chart, second_chart)
    )
    assert statistic_for.return_value is None
    assert view.state.tab == "rating"
    assert view.state.difficulty == "ULT"
    assert session_for(ctx).message is public
    payload = public.payloads[-1]
    assert payload["embeds"][0].image.url == "attachment://score-distribution.png"
    _assert_valid_png(*public.attachment_snapshots[-1][0])
    assert_rows(view)
    await view.deactivate()


@pytest.mark.asyncio
async def test_switching_from_distribution_to_overview_clears_image_attachment(
    ctx, monkeypatch
):
    extras = ctx.bot.phase1_charts
    monkeypatch.setattr(extras, "analysis", AsyncMock(return_value=(_origin(), PNG)))
    monkeypatch.setattr(extras, "statistic_for", Mock(return_value=None))
    public, view = await _public_song(ctx)
    await _select_more(view, ctx, public, "tab:rating")
    await _select_more(view, ctx, public, "tab:overview")

    payload = public.payloads[-1]
    assert view.state.tab == "overview"
    assert session_for(ctx).message is public
    assert not payload["embeds"][0].image.url
    assert payload["attachments"] == []
    assert public.attachment_snapshots[-1] == []
    assert_rows(view)
    await view.deactivate()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "error"])
async def test_unavailable_distribution_has_next_step_and_no_fake_image(
    ctx, monkeypatch, failure
):
    extras = ctx.bot.phase1_charts
    private_detail = "private-source-error-details"

    async def unavailable(chart):
        if failure == "error":
            raise RuntimeError(private_detail)

    monkeypatch.setattr(extras, "analysis", AsyncMock(side_effect=unavailable))
    monkeypatch.setattr(extras, "statistic_for", Mock(return_value=None))
    public, view = await _public_song(ctx)
    await _select_more(view, ctx, public, "tab:rating")

    payload = public.payloads[-1]
    embed = payload["embeds"][0]
    assert view.state.tab == "rating"
    assert not embed.image.url
    assert payload["attachments"] == []
    assert public.attachment_snapshots[-1] == []
    notice = "\n".join(f"{field.name} {field.value}" for field in embed.fields)
    assert any(action in notice for action in ("重試", "再試", "切換"))
    assert private_detail not in notice
    assert_rows(view)
    await view.deactivate()
