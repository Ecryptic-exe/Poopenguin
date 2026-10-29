from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chuni_penguin.config import Config, use_config
from chuni_penguin.services.chart_reviews import DIFFICULTY_LABELS
from chuni_penguin.ui.chart_reviews import (
    ChartReviewModal,
    ReviewEntryButton,
    ReviewerProfileModal,
    ReviewPrivateView,
    summary_embed,
)


@pytest.fixture(autouse=True)
def chinese_ui():
    with use_config(Config.from_mapping({"fun": {"language": "chinese"}})):
        yield


def chart():
    return SimpleNamespace(
        id=5, difficulty="MAS", level="14", song=SimpleNamespace(title="Fixture")
    )


def profile():
    return {
        "rating_bucket": "16–16.49",
        "primary_level": "14",
        "experience_bucket": "1–3 年",
        "strengths": ["交互"],
        "weaknesses": [],
    }


def interaction(user=11):
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        response=SimpleNamespace(
            is_done=lambda: False,
            defer=AsyncMock(),
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
        edit_original_response=AsyncMock(),
        message=None,
    )


@pytest.mark.asyncio
async def test_entry_opens_a_separate_private_response_and_never_edits_public_parent():
    database = object()
    ctx = SimpleNamespace(
        author=SimpleNamespace(id=11),
        bot=SimpleNamespace(database=database),
        response=SimpleNamespace(edit=AsyncMock()),
    )
    button = ReviewEntryButton(chart(), ctx, source="chunirec:jp2")
    changed = interaction()
    from unittest.mock import patch

    service = SimpleNamespace(
        profile=AsyncMock(return_value=profile()),
        own_review=AsyncMock(return_value=None),
    )
    with (
        patch(
            "chuni_penguin.ui.chart_reviews.ChartReviewService", return_value=service
        ),
        patch(
            "chuni_penguin.services.review_background.load_review_background",
            new=AsyncMock(return_value=profile()),
        ) as loader,
    ):
        await button.callback(changed)
    loader.assert_awaited_once_with(ctx, saved=profile(), source="chunirec:jp2")
    changed.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
    ctx.response.edit.assert_not_awaited()
    assert isinstance(
        changed.edit_original_response.call_args.kwargs["view"], ReviewPrivateView
    )
    changed.edit_original_response.call_args.kwargs["view"].stop()


@pytest.mark.asyncio
async def test_two_modals_fit_discord_limits_and_prefill_private_background():
    view = ReviewPrivateView(object(), 11, chart(), profile(), draft=profile())
    background = ReviewerProfileModal(view)
    review = ChartReviewModal(view)
    for modal in (background, review):
        assert len(modal.to_components()) == 5
        assert all(item["type"] == 18 for item in modal.to_components())
    assert background.rating.options[6].default
    assert not any(
        key in str(background.to_components()) for key in ("discord_id", "owner_id")
    )
    assert len(review.difficulty.options) == len(DIFFICULTY_LABELS)
    view.stop()
    background.stop()
    review.stop()


@pytest.mark.asyncio
async def test_other_user_cannot_open_or_submit_another_review():
    service = SimpleNamespace(submit=AsyncMock())
    view = ReviewPrivateView(service, 11, chart(), profile(), draft=profile())
    changed = interaction(22)
    assert not await view.interaction_check(changed)
    modal = ChartReviewModal(view)
    await modal.on_submit(changed)
    service.submit.assert_not_awaited()
    view.stop()
    modal.stop()


def test_public_embed_shows_empty_state_without_a_five_review_prompt():
    embed = summary_embed(chart(), {"available": False, "minimum": 1})
    rendered = str(embed.to_dict())
    assert "尚無評價" in rendered
    assert "至少" not in rendered and "5 位" not in rendered
    assert "16–16.49" not in rendered
    assert "discord_id" not in rendered


def test_public_embed_shows_only_selected_aggregate_dimension():
    summary = {
        "available": True,
        "count": 10,
        "enjoyment": 4.2,
        "difficulty": {0: 10},
        "groups": {
            "rating_bucket": [{"label": "16–16.49", "count": 5, "enjoyment": 4.0}],
            "primary_level": [{"label": "14", "count": 10, "enjoyment": 4.2}],
        },
    }
    embed = summary_embed(chart(), summary, "rating_bucket")
    assert "依 Rating 區間" in {field.name for field in embed.fields}
    assert "依主力等級" not in {field.name for field in embed.fields}


@pytest.mark.asyncio
async def test_profile_confirmation_then_review_submission_preserves_owner_chart_and_revision():
    service = SimpleNamespace(submit=AsyncMock(return_value=3))
    own = {
        "revision": 2,
        "enjoyment": 4,
        "perceived_difficulty": 0,
        "familiarity": "2–10 次",
        "goal": "SSS",
        "comment": "先前的私人評論",
    }
    original = ReviewPrivateView(service, 11, chart(), profile(), own)
    modal = ReviewerProfileModal(original)
    modal.rating._values = ["16–16.49"]
    modal.level._values = ["14"]
    modal.experience._values = ["1–3 年"]
    modal.strengths._values = ["交互"]
    modal.weaknesses._values = []
    changed = interaction()
    await modal.on_submit(changed)
    service.submit.assert_not_awaited()
    confirmed = changed.response.edit_message.call_args.kwargs["view"]
    assert confirmed.draft == profile()
    await confirmed.continue_review.callback(changed)
    review = changed.response.send_modal.call_args.args[0]
    review.enjoyment._values = ["5"]
    review.difficulty._values = ["略難"]
    review.familiarity._values = ["11 次以上"]
    review.goal._values = ["AJ"]
    review.comment._value = "只有本人可以查看這則評論"
    await review.on_submit(changed)
    args, kwargs = service.submit.call_args
    assert args[:3] == (11, 5, profile())
    assert args[3]["perceived_difficulty"] == 1
    assert args[3]["comment"] == "只有本人可以查看這則評論"
    assert kwargs == {"expected_revision": 2}
    result_view = changed.edit_original_response.call_args.kwargs["view"]
    assert [button.label for button in result_view.children] == ["關閉"]
    assert (
        "帳號與文字評論不公開"
        in changed.edit_original_response.call_args.kwargs["embed"].description
    )
    modal.stop()
    review.stop()
    original.stop()
    confirmed.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [None, profile()])
async def test_review_can_submit_directly_without_background_confirmation(saved):
    service = SimpleNamespace(submit=AsyncMock(return_value=1))
    view = ReviewPrivateView(service, 11, chart(), saved)
    assert view.continue_review in view.children
    assert view.background.style != view.continue_review.style
    changed = interaction()
    await view.continue_review.callback(changed)
    modal = changed.response.send_modal.call_args.args[0]
    assert isinstance(modal, ChartReviewModal)
    modal.enjoyment._values = ["4"]
    modal.difficulty._values = ["符合定數"]
    modal.familiarity._values = ["初次遊玩"]
    modal.goal._values = ["通關"]
    modal.comment._value = "直接沿用玩家資料"
    await modal.on_submit(changed)
    submitted = service.submit.call_args.args
    assert submitted[:2] == (11, 5)
    if saved is not None:
        assert submitted[2] == saved
    else:
        assert all(
            submitted[2][key] == "未提供"
            for key in ("rating_bucket", "primary_level", "experience_bucket")
        )
    view.stop()
    modal.stop()
