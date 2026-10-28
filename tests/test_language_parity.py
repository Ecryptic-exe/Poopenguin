"""Exercise visible text and controls through both real command entry points."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chuni_penguin.config import Config, use_config
from chuni_penguin.ui.entrypoints import (
    account_entry,
    chart_entry,
    score_entry,
    tools_entry,
)
from chuni_penguin.ui.panel import prepare_response, show_panel
from chuni_penguin.ui.song_details import TABS, SongDetailsState, SongDetailsView
from chuni_penguin.utils.chunithm_i18n import get_chunithm_language, ui_text
from tests.test_panels import TestContext
from tests.test_panels import ctx as context_fixture
from tests.upstream.test_song_details import make_view

ctx = context_fixture


def visible_controls(view):
    if view is None:
        return []
    return [
        (
            type(item).__name__,
            getattr(item, "label", None),
            getattr(item, "placeholder", None),
            [
                (option.label, option.value, option.description, option.default)
                for option in getattr(item, "options", ())
            ],
            item.disabled,
        )
        for item in view.children
    ]


def presentation(payload):
    embeds = payload.get("embeds") or (
        [payload["embed"]] if payload.get("embed") else []
    )
    return (
        payload.get("content"),
        [embed.to_dict() for embed in embeds],
        visible_controls(payload.get("view")),
    )


@pytest.fixture(autouse=True)
def no_saved_language(monkeypatch):
    monkeypatch.setattr(
        "chuni_penguin.cogs.poopenguin.config.load_settings", lambda: {"language": {}}
    )


@pytest.mark.parametrize("language", ["english", "chinese"])
@pytest.mark.parametrize("slash", [False, True])
def test_language_override_is_shared_by_context_and_interaction(
    monkeypatch, language, slash
):
    from chuni_penguin.cogs.poopenguin.i18n import get_guild_language

    preferences = {"language": {"33": language}}
    monkeypatch.setattr(
        "chuni_penguin.cogs.poopenguin.config.load_settings", lambda: preferences
    )
    context = SimpleNamespace(
        guild=SimpleNamespace(id=33), interaction=object() if slash else None
    )
    interaction = SimpleNamespace(guild_id=33)
    with use_config(Config.from_mapping({"fun": {"language": "english"}})):
        assert (
            get_chunithm_language(context)
            == get_chunithm_language(interaction)
            == language
        )
        assert get_guild_language(preferences, 33) == language


def test_default_and_configured_fallback_are_shared():
    from chuni_penguin.cogs.poopenguin.i18n import get_guild_language

    with use_config(Config.from_mapping({})):
        assert get_chunithm_language() == get_guild_language({}, None) == "english"
    with use_config(Config.from_mapping({"fun": {"language": "chinese"}})):
        assert get_chunithm_language() == get_guild_language({}, None) == "chinese"


def test_interface_templates_preserve_inserted_text_and_formatting():
    title = "主選單 Error {0} 日本語"
    assert ui_text("english", "搜尋：{0}", title) == "Search: " + title
    assert ui_text("chinese", "Search: {0}", title) == "搜尋：" + title
    assert ui_text("english", "第 {0}/{1} 頁", 2, 3) == "Page 2/3"


def test_all_translation_templates_preserve_format_arguments():
    from string import Formatter

    from chuni_penguin.utils.ui_messages import MESSAGES

    def fields(text):
        return sorted(
            (key, spec, conversion)
            for _, key, spec, conversion in Formatter().parse(text)
            if key is not None
        )

    for chinese, english in MESSAGES.items():
        assert fields(chinese) == fields(english), chinese


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "chinese"])
async def test_login_and_page_modals_keep_the_selected_language(language):
    from chuni_penguin.ui._pagination import JumpToPageModal
    from chuni_penguin.ui.login import LoginFlowPageSource, SegaIDLoginModal

    with use_config(Config.from_mapping({"fun": {"language": language}})):
        source = LoginFlowPageSource("123456", "https://example.com")
        first = (await source.get_page(0))[0]
        assert ("步驟" in first.description) == (language == "chinese")
        login = SegaIDLoginModal("123456")
        assert login.title == ui_text(language, "Login with SEGA ID")
        password_label = next(
            label for label in login.children if label.component is login.password
        )
        assert password_label.text == ui_text(language, "SEGA ID password")
        page = JumpToPageModal(SimpleNamespace(ctx=None))
        assert page.title == ui_text(language, "Jump to page")
        assert page.page.text == ui_text(language, "Page")


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", [tab for tab, _ in TABS])
async def test_english_song_tabs_and_controls_do_not_contain_chinese_ui(
    tab, monkeypatch
):
    from tests.upstream.test_song_details import make_song

    with use_config(Config.from_mapping({})):
        view, data = make_view(make_song(), initial_tab=tab)
        data.scores = AsyncMock(side_effect=NotImplementedError)
        data.leaderboard = AsyncMock(side_effect=NotImplementedError)
        monkeypatch.setattr(
            "chuni_penguin.services.chart_reviews.ChartReviewService.public_summary",
            AsyncMock(return_value={"available": False, "groups": {}}),
        )
        # The service constructor only retains this object; no real database is used.
        view.ctx.bot.database = SimpleNamespace()
        rendered = await view._safe_render(view.state)
        payload = view._kwargs(rendered, editing=False)
        visible = str([embed.to_dict() for embed in payload["embeds"]]) + str(
            visible_controls(view)
        )
        assert re.search(r"[\u4e00-\u9fff]", visible) is None, visible
        await view.deactivate()


@pytest.mark.asyncio
async def test_song_title_and_artist_are_never_translated():
    from tests.upstream.test_song_details import make_song

    song, charts = make_song()
    song.title = "Error"
    song.artist = "曲師 日本語"
    with use_config(Config.from_mapping({"fun": {"language": "chinese"}})):
        view, _ = make_view((song, charts))
        rendered = await view.render(SongDetailsState(tab="overview"))
        embed = rendered.pages[0][0]
        assert embed.title == "Error"
        assert "曲師 日本語" in embed.description
        assert "**曲師**" in embed.description
        await view.deactivate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "section", ["home", "tools", "chart", "score", "account", "account_settings", "fun"]
)
async def test_panels_are_fully_english_when_english_is_selected(ctx, section):
    with use_config(Config.from_mapping({})):
        await show_panel(ctx, section)
        visible = str(presentation(ctx.message.payloads[-1]))
        assert re.search(r"[\u4e00-\u9fff]", visible) is None, visible


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "chinese"])
@pytest.mark.parametrize(
    "name,entry",
    [
        ("chart", chart_entry),
        ("score", score_entry),
        ("tools", tools_entry),
        ("account", account_entry),
    ],
)
async def test_prefix_and_slash_use_identical_embeds_and_controls(
    ctx, monkeypatch, language, name, entry
):
    from chuni_penguin.context import PenguinContext

    prefix_ctx = TestContext(ctx.bot)
    prefix_ctx.interaction = None
    prefix_ctx.command = ctx.bot.get_command(name)
    prefix_ctx.invoked_with = name
    prefix_ctx.user_config = ctx.user_config
    slash_ctx = TestContext(ctx.bot)
    slash_ctx.command = ctx.bot.tree.get_command(name)
    slash_ctx.user_config = ctx.user_config
    monkeypatch.setattr(
        PenguinContext, "from_interaction", AsyncMock(return_value=slash_ctx)
    )
    with use_config(Config.from_mapping({"fun": {"language": language}})):
        command = prefix_ctx.command
        kwargs = {"query": "Offline fixture"} if name in ("chart", "score") else {}
        if command.cog is not None:
            await command.callback(command.cog, prefix_ctx, **kwargs)
        else:
            await command.callback(prefix_ctx, **kwargs)
        await entry.callback(slash_ctx.interaction, **kwargs)
        assert presentation(prefix_ctx.message.payloads[-1]) == presentation(
            slash_ctx.message.payloads[-1]
        )
        for context in (prefix_ctx, slash_ctx):
            active = context.panel_session.active_view
            if isinstance(active, SongDetailsView):
                await active.deactivate()


@pytest.mark.asyncio
async def test_plain_hybrid_responses_have_the_same_slash_embed_wrapper(ctx):
    ctx.command = ctx.bot.get_command("calculate")
    with use_config(Config.from_mapping({})):
        ctx.interaction = None
        prefix_content, prefix_kwargs = prepare_response(ctx, "A result", {})
        ctx.interaction = SimpleNamespace(namespace=None)
        slash_content, slash_kwargs = prepare_response(ctx, "A result", {})
        assert prefix_content == slash_content is None
        assert presentation(prefix_kwargs) == presentation(slash_kwargs)


@pytest.mark.asyncio
async def test_translated_review_options_keep_canonical_storage_values():
    from chuni_penguin.services.chart_reviews import ChartReviewService, _profile
    from chuni_penguin.ui.chart_reviews import ReviewerProfileModal, ReviewPrivateView
    from tests.upstream.test_song_details import make_song

    _, charts = make_song()
    with use_config(Config.from_mapping({})):
        view = ReviewPrivateView(ChartReviewService(SimpleNamespace()), 11, charts[0])
        modal = ReviewerProfileModal(view)
        assert modal.rating.options[-1].label == "Not provided"
        assert modal.rating.options[-1].value == "未提供"
        assert _profile(view.draft)["rating_bucket"] == "未提供"


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "chinese"])
async def test_calculator_results_and_border_labels_follow_language(ctx, language):
    ctx.command = ctx.bot.get_command("calculate")
    ctx.invoked_with = "calculate"
    with use_config(Config.from_mapping({"fun": {"language": language}})):
        cog = ctx.bot.get_cog("Calculators")
        await cog.calculate.callback(cog, ctx, 1009000, 13.6)
        text = str(presentation(ctx.message.payloads[-1]))
        assert ("可獲得" in text) == (language == "chinese")
        await cog.border.callback(cog, ctx, "1103")
        field_names = [field.name for field in ctx.message.payloads[-1]["embed"].fields]
        assert ("Score Deduction" in field_names) == (language == "english")


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["english", "chinese"])
@pytest.mark.parametrize(
    "kind", ["maintenance", "network", "authentication", "argument"]
)
async def test_prefix_and_slash_errors_use_the_same_localized_text(language, kind):
    from discord.ext import commands

    from chuni_penguin.adapters.errors import (
        AuthenticationError,
        MaintenanceError,
        NetworkError,
    )
    from chuni_penguin.context import PenguinContext
    from chuni_penguin.services.command_errors import CommandErrorService

    errors = {
        "maintenance": MaintenanceError(),
        "network": NetworkError(),
        "authentication": AuthenticationError(),
        "argument": commands.BadArgument("Notecount should be larger than 0."),
    }
    settings = Config.from_mapping({"fun": {"language": language}})
    bot = SimpleNamespace(settings=settings, prefixes={})
    service = CommandErrorService(bot)
    context = object.__new__(PenguinContext)
    context.message = SimpleNamespace(guild=None)
    context.prefix = "!"
    context.interaction = None
    with use_config(settings):
        prefix_embed, _ = await service.construct_error_embed(
            context, "border", errors[kind]
        )
        context.interaction = SimpleNamespace(guild_id=None, client=bot)
        slash_embed, _ = await service.construct_error_embed(
            context, "border", errors[kind]
        )
        assert prefix_embed.to_dict() == slash_embed.to_dict()
        assert prefix_embed.title == ("Error" if language == "english" else "錯誤")
        assert ("登入" in (prefix_embed.description or "")) == (
            language == "chinese" and kind in ("network", "authentication")
        )
