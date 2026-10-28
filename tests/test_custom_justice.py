from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chuni_penguin.calculation import calculate_border
from chuni_penguin.config import Config, use_config
from chuni_penguin.database import Chart, Song
from chuni_penguin.services.song_details import SongDetailsData
from chuni_penguin.types import Rank
from chuni_penguin.ui.custom_justice import CustomJusticeModal
from chuni_penguin.ui.song_details import SongDetailsView


@pytest.mark.parametrize("notes", [1, 20, 100, 1103, 4000])
def test_fixed_borders_obey_exact_budget_and_note_count(notes):
    # Check every allowed JUSTICE count, including both ends of the range.
    original = calculate_border(notes)
    for justice in range(notes + 1):
        for rank, result in calculate_border(notes, justice=justice).items():
            target = 1_009_900 if rank == "99AJ" else rank.min_score
            tolerance = (1_010_000 - target) * notes // 10_000
            if result is None:
                assert justice > tolerance
                continue
            assert result.justice == justice
            assert result.justice_critical >= 0
            assert (
                sum((result.justice_critical, justice, result.attack, result.miss))
                == notes
            )
            assert justice + 51 * result.attack + 101 * result.miss <= tolerance
            assert result.miss <= original[rank].miss
            if rank == "99AJ":
                assert result.attack == result.miss == 0
            elif result.justice_critical:
                assert (
                    justice + 51 * (result.attack + 1) + 101 * result.miss > tolerance
                )


@pytest.mark.parametrize("value", [-1, 1104, 1.5, True])
def test_invalid_fixed_count(value):
    with pytest.raises(ValueError):
        calculate_border(1103, justice=value)


def test_l9_examples_and_unchanged_default():
    default = calculate_border(1103)
    assert [(j.justice, j.attack, j.miss) for j in default.values()] == [
        (11, 0, 0),
        (59, 1, 0),
        (71, 4, 0),
        (93, 7, 1),
        (138, 11, 4),
        (174, 24, 8),
        (207, 34, 19),
    ]
    fixed = calculate_border(1103, justice=50)
    assert fixed["99AJ"] is None
    assert (
        fixed[Rank.sssp].justice,
        fixed[Rank.sssp].attack,
        fixed[Rank.sssp].miss,
    ) == (50, 1, 0)
    assert (fixed[Rank.s].justice, fixed[Rank.s].attack, fixed[Rank.s].miss) == (
        50,
        37,
        19,
    )


def make_view():
    song = Song(
        id=1,
        title="L9",
        artist="paraoka",
        genre="VARIETY",
        version="CHUNITHM",
        available=True,
        removed=False,
        aliases=[],
    )
    charts = [
        Chart(
            song=song,
            song_id=1,
            difficulty=diff,
            level="13+",
            const=13.6,
            maxcombo=notes,
            sdvxin_chart_view=None,
        )
        for diff, notes in (("MAS", 1103), ("EXP", 500))
    ]
    ctx = SimpleNamespace(author=SimpleNamespace(id=123), bot=SimpleNamespace())
    data = SongDetailsData(ctx)
    data._cache[("charts", 1)] = tuple(charts)
    view = SongDetailsView(ctx, [song], data=data, initial_tab="borders")
    view.message = SimpleNamespace(edit=AsyncMock())
    return view


def interaction(user_id=123):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: True
        ),
    )


@pytest.mark.asyncio
async def test_same_embed_contains_custom_section_and_resets_on_difficulty_change():
    with use_config(Config.from_mapping({})):
        view = make_view()
        view.state = replace(view.state, custom_justice=50)
        rendered = await view.render(view.state)
        custom = next(
            f for f in rendered.pages[0][0].fields if "JUSTICE = 50" in f.name
        )
        assert "SSS+ ▸ 50-1-0" in custom.value
        assert "99AJ ▸ N/A" in custom.value
        await view.transition(interaction(), difficulty="EXP")
        assert view.state.custom_justice is None
        assert view.state.difficulty == "EXP"
        view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["-1", "1.5", "abc", "1104", "１２"])  # noqa: RUF001 - intentional invalid input
async def test_modal_invalid_input_does_not_edit_card(raw):
    with use_config(Config.from_mapping({})):
        view = make_view()
        view.transition = AsyncMock()
        modal = CustomJusticeModal(view, 1103)
        modal.count._value = raw
        event = interaction()
        await modal.on_submit(event)
        event.response.send_message.assert_awaited_once()
        view.transition.assert_not_awaited()
        modal.stop()
        view.stop()


@pytest.mark.asyncio
async def test_modal_owner_timeout_stale_and_valid_submission():
    with use_config(Config.from_mapping({})):
        view = make_view()
        view.transition = AsyncMock()
        for condition in ("owner", "stale", "timeout", "valid"):
            view._expired = False
            modal = CustomJusticeModal(view, 1103)
            modal.count._value = "50"
            event = interaction(999 if condition == "owner" else 123)
            if condition == "stale":
                view._revision += 1
            elif condition == "timeout":
                view._expired = True
            await modal.on_submit(event)
            if condition == "valid":
                view.transition.assert_awaited_once_with(
                    event, custom_justice=50, page=0
                )
            else:
                view.transition.assert_not_awaited()
                event.response.send_message.assert_awaited_once()
            modal.stop()
        view.stop()


@pytest.mark.asyncio
async def test_chinese_modal_labels_use_existing_language_setting():
    with use_config(Config.from_mapping({"fun": {"language": "chinese"}})):
        view = make_view()
        modal = CustomJusticeModal(view, 1103)
        assert modal.title == "自訂 JUSTICE"
        modal.stop()
        view.stop()
