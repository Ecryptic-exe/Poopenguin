import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from chuni_penguin.application.offline import create_fixture_database, deny_network
from chuni_penguin.bot import ChuniBot
from chuni_penguin.config import Config, use_config
from chuni_penguin.database import Song
from chuni_penguin.ui.panel import (
    FORM_COMMANDS,
    PanelMenu,
    PanelModal,
    PanelRoute,
    PanelView,
    group_for,
    invoke_existing,
    navigate,
    prepare_response,
    session_for,
    show_account,
    show_panel,
    waiting_for_login,
)
from chuni_penguin.ui.song_details import TABS, SongDetailsView


class Message:
    def __init__(self, *, private=True):
        self.id = 101
        self.flags = SimpleNamespace(ephemeral=private)
        self.payloads = []
        self.content = ""
        self.created_at = datetime.now(UTC)

    async def edit(self, **kwargs):
        self.payloads.append(kwargs)
        return self


class TestContext:
    __test__ = False

    def __init__(self, bot):
        self.bot = bot
        self.author = self.user = SimpleNamespace(
            id=11, name="Fixture", display_name="Fixture"
        )
        self.command = bot.tree.get_command("help")
        self.response = self.message = Message()
        self.guild = None
        self.channel = SimpleNamespace(id=22)
        self.clean_prefix = self.prefix = "!"
        self.kwargs = {}
        self.args = []
        self.command_failed = False
        self.invoked_parents = []
        self.interaction = interaction(self)
        self.new_private_messages = []

    async def respond_or_edit(self, content=None, **kwargs):
        content, kwargs = prepare_response(self, content, kwargs)
        kwargs.pop("ephemeral", None)
        if self.response is None:
            self.response = Message()
            self.new_private_messages.append(self.response)
        if content is not None:
            kwargs["content"] = content
        await self.response.edit(**kwargs)
        if kwargs.get("view"):
            kwargs["view"].message = self.response
        return self.response

    async def reply(self, content=None, **kwargs):
        return await self.respond_or_edit(content, **kwargs)

    async def send(self, content=None, **kwargs):
        return await self.respond_or_edit(content, **kwargs)

    @asynccontextmanager
    async def typing(self, **kwargs):
        yield

    async def find_songs(self, query, **kwargs):
        async with self.bot.begin_db_read() as db:
            song = await db.scalar(select(Song).options(selectinload(Song.aliases)))
        return SimpleNamespace(songs=[song])


@pytest.mark.asyncio
async def test_abbreviation_opens_song_and_alias_offer_is_private_and_separate(ctx):
    from chuni_penguin.database import Alias
    from chuni_penguin.ui.panel import show_song
    from chuni_penguin.ui.song_search import AliasOfferView

    public = Message(private=False)
    ctx.response = ctx.message = public
    ctx.guild = SimpleNamespace(id=33)
    session_for(ctx).route = PanelRoute("chart")
    session_for(ctx).message = public
    view = await show_song(ctx, "of")
    assert view.songs[0].title == "Offline fixture"
    assert session_for(ctx).message is public
    prompt = ctx.interaction.followup.send.call_args.kwargs
    assert prompt["ephemeral"] and isinstance(prompt["view"], AliasOfferView)
    private = Message(private=True)
    changed = interaction(ctx)
    changed.message = private
    changed.original_response = AsyncMock(return_value=private)
    changed.edit_original_response = AsyncMock(side_effect=private.edit)
    before = list(public.payloads)
    await prompt["view"].add_alias.callback(changed)
    assert public.payloads == before
    assert private.payloads[-1]["embed"].title == "別名結果"
    async with ctx.bot.begin_db_read() as db:
        alias = await db.scalar(select(Alias).where(Alias.alias == "of"))
        assert (alias.song_id, alias.guild_id, alias.owner_id) == (900000001, 33, 11)
    ctx.interaction.followup.send.reset_mock()
    await show_song(ctx, "of")
    ctx.interaction.followup.send.assert_not_awaited()
    await session_for(ctx).active_view.deactivate()


@pytest.mark.asyncio
async def test_slow_panel_renders_processing_and_result_on_same_public_message(
    ctx, monkeypatch
):
    from contextlib import asynccontextmanager

    import chuni_penguin.ui.processing as processing

    original = processing.processing_response

    @asynccontextmanager
    async def immediate(context, label):
        async with original(context, label, delay=0):
            yield

    monkeypatch.setattr(processing, "processing_response", immediate)
    await show_account(ctx)
    public = ctx.response
    public.flags.ephemeral = False

    async def slow(*args):
        await asyncio.sleep(0.01)
        return "同步完成"

    monkeypatch.setattr(ctx.bot.get_cog("V2Players"), "sync_native", slow)
    changed = interaction(ctx)
    changed.message = public
    changed.original_response = AsyncMock(return_value=public)
    await navigate(ctx, changed, "run_sync")
    assert any(
        "處理中" in (payload.get("embed").description or "")
        for payload in public.payloads
        if payload.get("embed")
    )
    assert public.payloads[-1]["embed"].title == "主資料介面"
    assert "同步完成" in public.payloads[-1]["embed"].description
    assert session_for(ctx).message.id == public.id
    changed.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_delete_removes_own_chart_reviews_and_profile(ctx):
    from chuni_penguin.database import Chart, ChartPlayerReview, ChartReviewerProfile
    from chuni_penguin.services.chart_reviews import ChartReviewService

    async with ctx.bot.begin_db_read() as db:
        chart_id = await db.scalar(select(Chart.id))
    service = ChartReviewService(ctx.bot.database)
    profile = {
        "rating_bucket": "未定級",
        "primary_level": "14",
        "experience_bucket": "1–3 年",
        "strengths": [],
        "weaknesses": [],
    }
    review = {
        "enjoyment": 4,
        "perceived_difficulty": 0,
        "familiarity": "2–10 次",
        "goal": "SSS",
        "comment": "Private test note",
    }
    await service.submit(11, chart_id, profile, review)
    await service.submit(22, chart_id, profile, review)
    await ctx.bot.get_cog("V2Players").delete_own_data(11)
    async with ctx.bot.begin_db_read() as db:
        assert await db.get(ChartReviewerProfile, 11) is None
        assert await db.get(ChartReviewerProfile, 22) is not None
        assert (
            await db.scalar(
                select(ChartPlayerReview).where(ChartPlayerReview.discord_id == 22)
            )
            is not None
        )


@pytest.mark.asyncio
async def test_review_uses_selected_source_without_background_step_and_publishes_first(
    ctx,
):
    import json

    from sqlalchemy.orm import joinedload

    from chuni_penguin.database import Chart, PlayerSource
    from chuni_penguin.services.chart_reviews import ChartReviewService
    from chuni_penguin.ui.chart_reviews import ReviewEntryButton

    await ctx.bot.database.writer.merge(
        PlayerSource(
            discord_id=11,
            source="chunirec:jp2",
            credential=None,
            payload=json.dumps({"rating": 16.6}),
            synced_at=None,
            status="synced",
        )
    )
    async with ctx.bot.begin_db_read() as db:
        chart = await db.scalar(select(Chart).options(joinedload(Chart.song)))
    changed = interaction(ctx)
    changed.edit_original_response = AsyncMock()
    public_before = list(ctx.response.payloads)
    await ReviewEntryButton(chart, ctx, source="chunirec:jp2").callback(changed)
    private = changed.edit_original_response.call_args.kwargs["view"]
    assert private.draft["rating_bucket"] == "16.5–16.99"
    assert private.draft["experience_bucket"] == "未提供"
    fill = interaction(ctx)
    await private.continue_review.callback(fill)
    form = fill.response.send_modal.call_args.args[0]
    form.enjoyment._values = ["5"]
    form.difficulty._values = ["符合定數"]
    form.familiarity._values = ["初次遊玩"]
    form.goal._values = ["通關"]
    form.comment._value = "私人測試評論"
    submitted = interaction(ctx)
    submitted.edit_original_response = AsyncMock()
    await form.on_submit(submitted)
    summary = await ChartReviewService(ctx.bot.database).public_summary(chart.id)
    assert summary["available"] and summary["count"] == 1
    assert summary["groups"]["rating_bucket"][0]["label"] == "16.5–16.99"
    assert not summary["groups"]["experience_bucket"]
    assert "私人測試評論" not in json.dumps(summary, ensure_ascii=False)
    assert ctx.response.payloads == public_before
    private.stop()
    form.stop()


@pytest.mark.asyncio
async def test_private_clone_during_processing_cannot_inherit_public_responder(ctx):
    from chuni_penguin.ui.panel import sensitive_context
    from chuni_penguin.ui.processing import processing_response

    public = Message(private=False)
    ctx.response = ctx.message = public
    session_for(ctx).message = public
    before = list(public.payloads)
    async with processing_response(ctx, "載入", delay=999):
        private = sensitive_context(ctx, interaction(ctx, private=False))
        await private.respond_or_edit("private-only payload", ephemeral=True, view=None)
        assert private.response.flags.ephemeral
        assert private.response is not public
    assert public.payloads == before
    assert not ctx._processing_response_active


@pytest.mark.asyncio
async def test_song_reviews_use_canonical_chart_and_same_public_message(ctx):
    from chuni_penguin.ui.chart_reviews import ReviewEntrySelect
    from chuni_penguin.ui.panel import show_song

    public = Message(private=False)
    ctx.message = ctx.response = public
    session_for(ctx).message = public
    view = await show_song(ctx, "Offline fixture")
    changed = interaction(ctx)
    changed.message = public
    await view.transition(changed, tab="reviews")
    assert session_for(ctx).message is public
    embed = public.payloads[-1]["embeds"][0]
    assert "玩家評價" in embed.title
    assert any(field.name == "尚無評價" for field in embed.fields)
    button = next(item for item in view.children if isinstance(item, ReviewEntrySelect))
    assert button.chart.song_id == 900000001 and button.chart.id > 0
    components = view.to_components()
    assert len(components) <= 5
    assert all(len(row["components"]) <= 5 for row in components)
    assert not any(
        getattr(item, "custom_id", "") == "song_details:network"
        for item in view.children
    )
    await view.deactivate()


@pytest.mark.asyncio
async def test_manual_guild_alias_uses_same_normalized_transaction_as_private_offer(
    ctx,
):
    from chuni_penguin.database import Alias

    await ctx.bot.database.writer.add(
        Song(
            id=123,
            title="Manual unique fixture",
            chunithm_catcode=0,
            artist="Fixture artist",
            genre="ORIGINAL",
            version="X-VERSE",
            available=True,
            removed=False,
        )
    )
    ctx.guild = SimpleNamespace(id=33)
    cog = ctx.bot.get_cog("Aliases")
    ctx.invoked_with = "alias add"
    await cog.addalias.callback(cog, ctx, "Manual unique fixture", "  ＭＹ Alias  ")
    with pytest.raises(commands.BadArgument, match="已存在"):
        await cog.addalias.callback(cog, ctx, "Manual unique fixture", "my alias")
    async with ctx.bot.begin_db_read() as db:
        rows = list(await db.scalars(select(Alias).where(Alias.guild_id == 33)))
    assert [(row.alias, row.song_id, row.owner_id) for row in rows] == [
        ("MY Alias", 123, 11)
    ]


def interaction(ctx, *, user_id=11, private=True):
    message = ctx.message
    if not private:
        message = Message(private=False)
    state = {"done": False}

    async def defer(**kwargs):
        state["done"] = True

    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        client=ctx.bot,
        message=message,
        namespace=None,
        created_at=datetime.now(UTC),
        guild_id=None,
        response=SimpleNamespace(
            is_done=lambda: state["done"],
            defer=AsyncMock(side_effect=defer),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        original_response=AsyncMock(return_value=ctx.message if private else Message()),
        edit_original_response=AsyncMock(side_effect=ctx.message.edit),
    )


@pytest_asyncio.fixture
async def ctx(tmp_path, monkeypatch):
    import chuni_penguin.config as config_module

    url = await create_fixture_database(tmp_path / "panel.sqlite3")
    settings = Config.from_mapping({"bot": {"db_connection_string": url}})
    attempts = []
    monkeypatch.setattr(config_module, "_default_settings", settings)
    with deny_network(attempts):
        async with ChuniBot(
            settings, offline=True, cache_dir=tmp_path / "cache"
        ) as bot:
            bot.owner_id = 11
            with use_config(settings):
                await bot.setup_hook()
            result = TestContext(bot)
            result.user_config = await bot.services.users.fetch_user_config(11)
            yield result
    assert attempts == []


def assert_rows(view):
    components = view.to_components()
    assert len(components) <= 5
    ids = []
    for row in components:
        assert sum(5 if c["type"] != 2 else 1 for c in row["components"]) <= 5
        for item in row["components"]:
            if item.get("custom_id"):
                ids.append(item["custom_id"])
            assert len(item.get("options", [])) <= 25
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_grouped_panel_modal_calculation_and_back_edit_the_same_message(ctx):
    await show_panel(ctx)
    home = ctx.message.payloads[-1]["view"]
    assert {item.label for item in home.children} == {
        "Song",
        "主資料介面",
        "Poopenguin",
        "計算工具",
        "關閉",
    }
    await navigate(ctx, interaction(ctx), "tools")
    assert session_for(ctx).route.section == "tools"
    await navigate(ctx, interaction(ctx), "calculate")
    modal = interaction(ctx)
    await navigate(ctx, modal, "calculate")
    form = modal.response.send_modal.call_args.args[0]
    assert isinstance(form, PanelModal)
    form.inputs["score"]._value = "1007500"
    form.inputs["chart_constant"]._value = "14.7"
    await form.on_submit(interaction(ctx))
    payload = ctx.message.payloads[-1]
    assert (
        "16.7" in payload["embed"].description
        or "16.70" in payload["embed"].description
    )
    assert any(
        getattr(item, "label", "") == "修改參數" for item in payload["view"].children
    )
    assert ctx.new_private_messages == []
    await navigate(ctx, interaction(ctx), "back")
    assert session_for(ctx).route.section == "tools"
    assert_rows(ctx.message.payloads[-1]["view"])


@pytest.mark.asyncio
async def test_navigation_rejects_other_users_and_public_menus_edit_same_message(ctx):
    await show_panel(ctx)
    count = len(ctx.message.payloads)
    outsider = interaction(ctx, user_id=22)
    await navigate(ctx, outsider, "account")
    outsider.response.send_message.assert_awaited_once()
    assert len(ctx.message.payloads) == count
    ctx.message.flags.ephemeral = False
    session_for(ctx).message = ctx.message
    public = interaction(ctx)
    await navigate(ctx, public, "account")
    assert "CHUNITHM-NET" in ctx.message.payloads[-1]["embed"].fields[0].value
    assert session_for(ctx).message is ctx.message
    assert not ctx.message.flags.ephemeral
    public.response.defer.assert_awaited_once_with(thinking=False, ephemeral=False)


@pytest.mark.asyncio
async def test_panel_permissions_rechecked_and_private_comparison_does_not_fetch(
    ctx, monkeypatch
):
    players = ctx.bot.get_cog("V2Players")
    settings = await ctx.bot.services.users.fetch_user_config(22)
    settings.privacy_mode = True
    await ctx.bot.services.users.save_user_config(settings, is_new=True)
    rows = AsyncMock()
    monkeypatch.setattr(players, "matching_rows", rows)
    await navigate(
        ctx, interaction(ctx), "compare", values={"query": "fixture", "user_id": "22"}
    )
    assert "尚未允許分享" in ctx.message.payloads[-1]["embed"].description
    rows.assert_not_awaited()
    monkeypatch.setattr(
        ctx.bot.services.permissions,
        "check",
        AsyncMock(side_effect=commands.CheckFailure("測試權限已停用")),
    )
    await navigate(ctx, interaction(ctx), "b50")
    assert "權限已停用" in ctx.message.payloads[-1]["embed"].description


@pytest.mark.asyncio
async def test_form_inventory_matches_real_commands_and_layout_limits(ctx):
    for action, (_, name, fields) in FORM_COMMANDS.items():
        command = ctx.bot.get_command(name)
        assert command is not None, name
        assert {key for key, _, _ in fields} <= command.clean_params.keys(), action
    for section in (
        "home",
        "account",
        "sync",
        "chart",
        "score",
        "b50",
        "recent",
        "compare",
        "tools",
        "fun",
        *FORM_COMMANDS,
    ):
        session_for(ctx).route = PanelRoute(section)
        assert_rows(PanelView(ctx))
    source = await ctx.find_songs("fixture")
    for tab, _ in TABS:
        view = SongDetailsView(ctx, source.songs, initial_tab=tab)
        if tab == "scores":
            from chuni_penguin.services.song_details import SongScores

            view.data.scores = AsyncMock(return_value=SongScores("fixture", ()))
        if tab == "leaderboard":
            from chuni_penguin.services.song_details import SongLeaderboard
            from chuni_penguin.types import Leaderboard

            view.data.leaderboard = AsyncMock(
                return_value=SongLeaderboard(
                    "fixture", Leaderboard(ranking=[], updated_at=datetime.now(UTC))
                )
            )
        await view._before_start()
        assert_rows(view)
        await view.deactivate()


@pytest.mark.asyncio
async def test_source_switch_status_privacy_and_leaving_recent_release_reader(ctx):
    await navigate(ctx, interaction(ctx), "account")
    before = await ctx.bot.services.users.fetch_user_config(11)
    assert before.privacy_mode is False
    await navigate(ctx, interaction(ctx), "privacy")
    after = await ctx.bot.services.users.fetch_user_config(11)
    assert after.privacy_mode is True
    assert "僅本人可查詢" in ctx.message.payloads[-1]["embed"].description
    recent = SimpleNamespace(close_network=AsyncMock(), stop=Mock())
    session_for(ctx).active_view = recent
    await navigate(ctx, interaction(ctx), "home")
    recent.close_network.assert_awaited_once()
    recent.stop.assert_called_once()


@pytest.mark.asyncio
async def test_calculator_results_are_reusable_embeds(ctx):
    for action, values in (
        ("calculate", {"score": "1009000", "chart_constant": "14.5"}),
        ("const", {"chart_constant": "14.5"}),
        ("rating", {"rating": "16.5"}),
        ("anmitsu", {"bpm": "180", "note_density": "16"}),
        ("border", {"difficulty_or_notecount": "1000"}),
    ):
        session_for(ctx).route = PanelRoute(action)
        await invoke_existing(ctx, FORM_COMMANDS[action][1], values)
        payload = ctx.message.payloads[-1]
        assert payload.get("embed") is not None or payload.get("embeds")
        assert_rows(payload["view"])
    content, kwargs = prepare_response(
        ctx, "kept legacy", {"view": discord.ui.LayoutView()}
    )
    assert content == "kept legacy" and "embed" not in kwargs


@pytest.mark.asyncio
async def test_selected_poopenguin_tools_preserve_original_cog_checks(ctx, monkeypatch):
    with pytest.raises(commands.NoPrivateMessage):
        await invoke_existing(ctx, "pick", {"choices": "A B"})
    ctx.guild = SimpleNamespace(id=33)
    monkeypatch.setattr(ctx.bot.get_cog("general"), "_lang", lambda _: "zh-TW")
    for action, values in (
        ("ask", {"question": "測試問題"}),
        ("pick", {"choices": '"選項 A" "選項 B"'}),
        ("rng", {"min_val": "5", "max_val": "5"}),
        ("rcg", {}),
    ):
        session_for(ctx).route = PanelRoute(action)
        await invoke_existing(ctx, action, values)
        payload = ctx.message.payloads[-1]
        assert payload.get("embed") is not None
        assert any(isinstance(item, PanelMenu) for item in payload["view"].children)
        assert_rows(payload["view"])


@pytest.mark.asyncio
async def test_gacha_rebuilt_controls_keep_related_navigation(ctx):
    from chuni_penguin.legacy_b.gacha_cog import GachaMenuView

    ctx.command = ctx.bot.get_command("gacha")
    session_for(ctx).route = PanelRoute("gacha")
    manager = SimpleNamespace(
        get_pool=lambda: {"three_star": ["角色 A", "角色 B"]},
        get_user_target=lambda _: None,
    )
    view = GachaMenuView(SimpleNamespace(manager=manager), ctx, "zh-TW")
    for build in (
        view._build_main,
        view._build_target,
        view._build_target_confirm,
        view._build_browse,
        view._build_record,
        view._build_delete_confirm,
    ):
        build()
        menus = [item for item in view.children if isinstance(item, PanelMenu)]
        assert len(menus) == 1
        assert "pick" in {item.value for item in menus[0].options}
        assert_rows(view)
    outsider = interaction(ctx, user_id=22)
    assert not await view._check_owner(outsider)
    outsider.response.send_message.assert_awaited_once()
    view.stop()


@pytest.mark.asyncio
async def test_original_sync_cooldown_applies_to_panel(ctx, monkeypatch):
    players = ctx.bot.get_cog("V2Players")
    sync = AsyncMock(return_value="同步完成")
    monkeypatch.setattr(players, "sync_native", sync)
    for _ in range(3):
        await navigate(ctx, interaction(ctx), "run_sync")
    assert sync.await_count == 2
    assert "太頻繁" in ctx.message.payloads[-1]["embed"].description


@pytest.mark.asyncio
async def test_leaving_song_during_score_load_does_not_overwrite_new_panel(ctx):
    from chuni_penguin.services.song_details import SongScores

    songs = await ctx.find_songs("fixture")
    view = SongDetailsView(ctx, songs.songs)
    await view.start(ephemeral=True)
    started, release = asyncio.Event(), asyncio.Event()

    async def scores(*args):
        started.set()
        await release.wait()
        return SongScores("fixture", ())

    view.data.scores = scores
    old = asyncio.create_task(view.transition(interaction(ctx), tab="scores"))
    await started.wait()
    await navigate(ctx, interaction(ctx), "chart", values={"query": "another"})
    size = len(ctx.message.payloads)
    release.set()
    await old
    assert len(ctx.message.payloads) == size
    assert session_for(ctx).route.section == "chart" and view._expired
    await view.edit_message(interaction(ctx), content="stale recent-style callback")
    assert len(ctx.message.payloads) == size


@pytest.mark.asyncio
async def test_leaving_login_cancels_only_waiting_prompt_and_keeps_panel_usable(
    ctx, monkeypatch
):
    import chuni_penguin.ui.panel as panel

    started = asyncio.Event()

    async def login(updated, command):
        assert command == "login"
        await show_panel(updated, "login", description="等待登入")
        async with waiting_for_login(updated):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(panel, "invoke_existing", login)
    task = asyncio.create_task(navigate(ctx, interaction(ctx), "login"))
    await started.wait()
    await navigate(ctx, interaction(ctx), "home")
    assert task.cancelled()
    session = session_for(ctx)
    assert session.route.section == "account" and not session.busy
    assert session.pending_login is None


@pytest.mark.asyncio
async def test_real_context_send_and_reply_update_existing_private_panel():
    from chuni_penguin.context import PenguinContext

    message = Message()
    proxy = SimpleNamespace(
        _panel_edit=True,
        response=message,
        respond_or_edit=AsyncMock(return_value=message),
    )
    assert await PenguinContext.send(proxy, "first") is message
    assert await PenguinContext.reply(proxy, "second") is message
    assert proxy.respond_or_edit.await_count == 2


@pytest.mark.asyncio
async def test_panel_comparison_keeps_summary_and_matches_only_stable_source_ids(
    ctx, monkeypatch
):
    settings = await ctx.bot.services.users.fetch_user_config(22)
    settings.privacy_mode = False
    await ctx.bot.services.users.save_user_config(settings, is_new=True)
    mine = [{"id": "stable-1", "diff": "MAS", "title": "Song", "score": 1007500}]
    theirs = [
        {
            "id": "stable-1",
            "diff": "MAS",
            "title": "Other source title",
            "score": 1007000,
        },
        {"id": "different-id", "diff": "MAS", "title": "Song", "score": 1009000},
    ]
    rows = AsyncMock(side_effect=[mine, theirs])
    monkeypatch.setattr(ctx.bot.get_cog("V2Players"), "matching_rows", rows)
    await navigate(
        ctx, interaction(ctx), "compare", values={"query": "Song", "user_id": "22"}
    )
    embed = ctx.message.payloads[-1]["embeds"][0]
    assert "共同譜面 1 張" in embed.description
    assert "領先 1／平手 0／落後 0" in embed.description
    assert "+500" in embed.description and "1,009,000" not in embed.description


@pytest.mark.asyncio
async def test_chunirec_modal_result_returns_to_account_without_retaining_token(
    ctx, monkeypatch
):
    from chuni_penguin.cogs.v2_players import ChunirecLinkModal

    cog = ctx.bot.get_cog("V2Players")
    sync = AsyncMock(return_value={"records": [], "mapped": 0, "unmapped": 0})
    monkeypatch.setattr(cog.sources, "sync_chunirec", sync)
    previous = await show_account(ctx)
    modal = ChunirecLinkModal(cog, 11, "jp2", ctx=ctx)
    modal.token._value = "synthetic-test-credential"
    submitted = interaction(ctx)
    submitted.data = {
        "components": [
            {"components": [{"type": 4, "value": "synthetic-test-credential"}]}
        ]
    }
    await modal.on_submit(submitted)
    result = ctx.message.payloads[-1]
    assert previous.is_finished() and previous._panel_inactive
    submitted.followup.send.assert_not_awaited()
    assert result["embed"].title == "主資料介面"
    assert modal.token.value == ""
    assert "synthetic-test-credential" not in str(submitted.data)
    assert session_for(ctx).route.section == "account"
    assert session_for(ctx).route.source == "chunirec:jp2"
    assert result["view"].ctx.interaction is not submitted
    assert "synthetic-test-credential" not in str(result["embed"].to_dict())
    result["view"].stop()


@pytest.mark.asyncio
async def test_modal_without_source_message_edits_original_using_real_context(ctx):
    from chuni_penguin.context import PenguinContext

    class RoutedContext(TestContext):
        respond_or_edit = PenguinContext.respond_or_edit
        reply = PenguinContext.reply
        send = PenguinContext.send

    ctx.__class__ = RoutedContext
    await show_panel(ctx, "tools")
    message_id = ctx.message.id
    modal = PanelModal(ctx, "calculate", "Rating 試算", FORM_COMMANDS["calculate"][2])
    modal.inputs["score"]._value = "1007500"
    modal.inputs["chart_constant"]._value = "14.7"
    submitted = interaction(ctx)
    submitted.message = None
    await modal.on_submit(submitted)
    assert session_for(ctx).message.id == message_id
    assert "16.7" in ctx.message.payloads[-1]["embeds"][0].description
    submitted.response.defer.assert_awaited_once_with(thinking=False, ephemeral=True)
    submitted.followup.send.assert_not_awaited()
    assert ctx.new_private_messages == []


@pytest.mark.asyncio
async def test_direct_link_modal_creates_one_initial_private_response(ctx, monkeypatch):
    from chuni_penguin.cogs.v2_players import ChunirecLinkModal

    cog = ctx.bot.get_cog("V2Players")
    monkeypatch.setattr(
        cog.sources,
        "sync_chunirec",
        AsyncMock(return_value={"records": [], "mapped": 0, "unmapped": 0}),
    )
    ctx.response = None
    session_for(ctx).message = None
    modal = ChunirecLinkModal(cog, 11, "jp2", ctx=ctx)
    modal.token._value = "synthetic-test-credential"
    submitted = interaction(ctx)
    submitted.message = None
    response = Message()
    response.id = 202
    submitted.original_response.return_value = response
    await modal.on_submit(submitted)
    assert session_for(ctx).message.id == 202
    assert len(response.payloads) == 1 and not ctx.message.payloads
    submitted.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
    submitted.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_landing_shows_accounts_and_direct_controls_without_cross_links(ctx):
    from chuni_penguin.database import Cookie, PlayerSource

    async def seed(db):
        db.add(Cookie(discord_id=11, cookie="#LWP-Cookies-2.0 fixture-cookie"))
        db.add(
            PlayerSource(
                discord_id=11,
                source="chunirec:jp2",
                credential="fixture-encrypted-value",
                status="synced",
                synced_at="2026-10-15T01:00:00+08:00",
            )
        )

    await ctx.bot.database.writer.execute_fn(seed)
    view = await show_account(ctx)
    embed = ctx.message.payloads[-1]["embed"]
    fields = {field.name: field.value for field in embed.fields}
    assert "**CHUNITHM-NET**：已連結" in fields["帳號連結"]
    assert "**Chunirec**：jp2 已連結" in fields["帳號連結"]
    assert "Kamaitachi" not in fields["帳號連結"]
    assert "**Chunirec jp**" not in fields["帳號連結"]
    assert "fixture-cookie" not in str(embed.to_dict())
    assert "fixture-encrypted-value" not in str(embed.to_dict())
    assert {
        item.action for item in view.children if getattr(item, "row", None) == 0
    } == {"run_sync", "account_settings"}
    menu = next(item for item in view.children if isinstance(item, PanelMenu))
    assert {item.value for item in menu.options} == {
        "b50",
        "recent",
        "source_scores",
    }
    assert all("IP" not in getattr(item, "label", "") for item in view.children)
    assert_rows(view)


@pytest.mark.asyncio
async def test_category_menus_stay_separate_and_song_contains_only_single_song_actions(
    ctx,
):
    for section in (
        "account",
        "b50",
        "recent",
        "chart",
        "score",
        "compare",
        "fun",
        "gacha",
    ):
        session_for(ctx).route = PanelRoute(section)
        menu = PanelMenu(ctx)
        assert all(
            option.value == "back" or group_for(option.value) == group_for(section)
            for option in menu.options
        )
    songs = await ctx.find_songs("fixture")
    view = SongDetailsView(ctx, songs.songs)
    await view._before_start()
    from chuni_penguin.ui.song_details import SongMoreSelect

    menu = next(item for item in view.children if isinstance(item, SongMoreSelect))
    assert {
        option.value for option in menu.options if option.value.startswith("panel:")
    } == {"panel:alias"}
    assert "主選單" not in {getattr(item, "label", "") for item in view.children}
    await view.deactivate()


@pytest.mark.asyncio
async def test_cross_category_action_does_not_query_song_or_send_new_message(
    ctx, monkeypatch
):
    await show_account(ctx)
    find = AsyncMock()
    monkeypatch.setattr(ctx, "find_songs", find)
    changed = interaction(ctx)
    await navigate(ctx, changed, "chart", values={"query": "fixture"})
    assert session_for(ctx).route.section == "account"
    assert "其他類別" in ctx.message.payloads[-1]["embed"].description
    find.assert_not_awaited()
    changed.followup.send.assert_not_awaited()
    assert ctx.new_private_messages == []


@pytest.mark.asyncio
async def test_sync_returns_status_to_same_data_landing_message(ctx, monkeypatch):
    await show_account(ctx)
    sync = AsyncMock(return_value="同步完成")
    monkeypatch.setattr(ctx.bot.get_cog("V2Players"), "sync_native", sync)
    changed = interaction(ctx)
    await navigate(ctx, changed, "run_sync")
    assert session_for(ctx).route.section == "account"
    assert session_for(ctx).message.id == ctx.message.id
    assert ctx.message.payloads[-1]["embed"].title == "主資料介面"
    assert "同步完成" in ctx.message.payloads[-1]["embed"].description
    changed.followup.send.assert_not_awaited()
    assert ctx.new_private_messages == []


@pytest.mark.asyncio
async def test_slash_account_result_edits_existing_private_response(ctx, monkeypatch):
    from chuni_penguin.context import PenguinContext

    ctx.command = ctx.bot.panel_commands["account status"]
    ctx.interaction.command = ctx.command
    ctx.respond_or_edit = MethodType(PenguinContext.respond_or_edit, ctx)
    ctx.reply = AsyncMock(side_effect=AssertionError("must edit the existing response"))
    monkeypatch.setattr(PenguinContext, "from_interaction", AsyncMock(return_value=ctx))
    await ctx.bot.get_cog("V2Players").send_result(ctx.interaction, "狀態已更新")
    assert ctx.message.payloads[-1]["embeds"][0].title == "主資料介面"
    ctx.interaction.followup.send.assert_not_awaited()
    ctx.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_poopenguin_result_stays_in_the_same_public_message(
    ctx, monkeypatch
):
    ctx.command = ctx.bot.get_command("rng")
    ctx.guild = SimpleNamespace(id=33)
    monkeypatch.setattr(ctx.bot.get_cog("general"), "_lang", lambda _: "zh-TW")
    session_for(ctx).route = PanelRoute("fun")
    public = Message(private=False)
    ctx.response = public
    session_for(ctx).message = public
    changed = interaction(ctx, private=False)
    changed.message = public
    changed.original_response.return_value = public
    await navigate(ctx, changed, "rng", values={"min_val": "5", "max_val": "5"})
    assert session_for(ctx).message is public and not public.flags.ephemeral
    assert "5" in public.payloads[-1]["embed"].description
    changed.response.defer.assert_awaited_once_with(thinking=False, ephemeral=False)
    changed.followup.send.assert_not_awaited()
    assert ctx.new_private_messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_real_discord_ephemeral_component_uses_webhook_not_channel_edit(
    ctx, monkeypatch, private
):
    from discord.webhook.async_ import async_context

    ctx.message.flags.ephemeral = private
    await show_panel(ctx)
    author = {
        "id": "42",
        "username": "FixtureBot",
        "discriminator": "0001",
        "avatar": None,
        "bot": True,
    }
    ctx.bot._connection.user = discord.ClientUser(
        state=ctx.bot._connection, data=author
    )
    raw_message = {
        "id": "101",
        "channel_id": "22",
        "author": author,
        "content": "",
        "timestamp": datetime.now(UTC).isoformat(),
        "edited_timestamp": None,
        "attachments": [],
        "embeds": [],
        "components": [],
        "pinned": False,
        "type": 0,
        "flags": 64 if private else 0,
    }
    payload = {
        "id": "999",
        "application_id": "42",
        "attachment_size_limit": 10485760,
        "type": 3,
        "token": "synthetic-discord-interaction-token",
        "version": 1,
        "locale": "zh-TW",
        "channel_id": "22",
        "channel": {"id": "22", "type": 1},
        "user": {
            "id": "11",
            "username": "Fixture",
            "discriminator": "0001",
            "avatar": None,
        },
        "message": raw_message,
        "data": {"custom_id": "penguin:panel:account:0", "component_type": 2},
    }
    component = discord.Interaction(data=payload, state=ctx.bot._connection)
    assert type(component.message) is discord.Message
    forbidden = AsyncMock(
        side_effect=AssertionError("channel API cannot edit ephemeral messages")
    )
    monkeypatch.setattr(ctx.bot.http, "edit_message", forbidden)
    adapter = SimpleNamespace(
        create_interaction_response=AsyncMock(
            return_value={"interaction": {"id": "999"}}
        ),
        get_original_interaction_response=AsyncMock(return_value=raw_message),
        edit_original_interaction_response=AsyncMock(return_value=raw_message),
    )
    token = async_context.set(adapter)
    try:
        await navigate(ctx, component, "account")
    finally:
        async_context.reset(token)
    adapter.create_interaction_response.assert_awaited_once()
    assert (
        adapter.create_interaction_response.call_args.kwargs["params"].payload["type"]
        == 6
    )
    adapter.edit_original_interaction_response.assert_awaited_once()
    sent = adapter.edit_original_interaction_response.call_args.kwargs["payload"]
    assert sent["embeds"][0]["title"] == "主資料介面"
    assert isinstance(session_for(ctx).message, discord.InteractionMessage)
    assert session_for(ctx).message.id == 101
    forbidden.assert_not_awaited()
    session_for(ctx).active_view.stop()


@pytest.mark.asyncio
async def test_reduced_slash_entries_retain_poopenguin_and_original_compare(ctx):
    names = {
        command.name
        for command in ctx.bot.tree.get_commands()
        if not isinstance(command, discord.app_commands.ContextMenu)
    }
    assert names == {
        "help",
        "account",
        "chart",
        "score",
        "tools",
        "compare",
        "permissions",
        "ask",
        "pick",
        "rng",
        "rcg",
        "gacha",
        "copypasta",
        "keyword",
    }
    assert ctx.bot.tree.get_command("sync") is None
    assert ctx.bot.panel_commands["sync"].checks
    assert ctx.bot.panel_commands["account delete"].callback
    from chuni_penguin.cogs.chunithm.score_queries import ScoreQueriesCog

    assert ctx.bot.get_command("compare").callback is ScoreQueriesCog.compare.callback
    assert ctx.bot.tree.get_command(
        "View your score", type=discord.AppCommandType.message
    )
    compare = ctx.bot.tree.get_command("compare")
    assert {p.name for p in compare.parameters} == {"user", "kamaitachi"}
    assert all(not p.required for p in compare.parameters)


@pytest.mark.asyncio
async def test_compare_slash_calls_original_recent_message_flow(ctx, monkeypatch):
    from chuni_penguin.context import PenguinContext
    from chuni_penguin.ui.entrypoints import compare_entry

    original = AsyncMock()
    monkeypatch.setattr(ctx.bot.panel_score_queries, "_compare_inner", original)
    monkeypatch.setattr(PenguinContext, "from_interaction", AsyncMock(return_value=ctx))
    await compare_entry.callback(ctx.interaction, None, False)
    original.assert_awaited_once_with(ctx, None, kamaitachi=False)
    ctx.interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_token_action_uses_private_overlay_without_editing_public_menu(
    ctx, monkeypatch
):
    ctx.message.flags.ephemeral = False
    ctx.response = ctx.message
    session_for(ctx).message = ctx.message
    session_for(ctx).route = PanelRoute("account_settings")
    token_value = AsyncMock(return_value="synthetic-owner-only-secret")
    monkeypatch.setattr(ctx.bot.get_cog("Auth"), "token_value", token_value)
    submitted = interaction(ctx)
    private_response = Message()
    private_response.id = 202
    submitted.original_response.return_value = private_response
    await navigate(ctx, submitted, "token")
    token_value.assert_awaited_once_with(11)
    submitted.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
    assert ctx.message.payloads == []
    assert "synthetic-owner-only-secret" in private_response.payloads[-1]["content"]
    assert private_response.flags.ephemeral
    assert not session_for(ctx).busy
    assert session_for(ctx).message is ctx.message


@pytest.mark.asyncio
async def test_import_upload_checks_owner_and_size_before_reading_or_importing(
    ctx, monkeypatch
):
    from chuni_penguin.ui.panel import ImportPlayerModal

    importer = AsyncMock()
    monkeypatch.setattr(
        ctx.bot.get_cog("V2Players").sources, "import_chunilib", importer
    )
    modal = ImportPlayerModal(ctx)
    attachment = SimpleNamespace(size=5 * 1024 * 1024 + 1, read=AsyncMock())
    modal.upload._values = [attachment]
    stranger = interaction(ctx, user_id=22)
    await modal.on_submit(stranger)
    stranger.response.send_message.assert_awaited_once()
    stranger.response.defer.assert_not_awaited()
    await modal.on_submit(interaction(ctx))
    attachment.read.assert_not_awaited()
    importer.assert_not_awaited()
    assert "5 MiB" in ctx.message.payloads[-1]["embed"].description
    modal.stop()


@pytest.mark.asyncio
async def test_import_upload_is_reachable_and_imports_only_owner_into_same_private_result(
    ctx, monkeypatch
):
    from chuni_penguin.ui.panel import ImportPlayerModal

    session_for(ctx).route = PanelRoute("account")
    clicked = interaction(ctx)
    await navigate(ctx, clicked, "import")
    modal = clicked.response.send_modal.call_args.args[0]
    assert isinstance(modal, ImportPlayerModal)
    importer = AsyncMock(return_value={"records": [], "mapped": 0, "unmapped": 0})
    monkeypatch.setattr(
        ctx.bot.get_cog("V2Players").sources, "import_chunilib", importer
    )
    attachment = SimpleNamespace(size=2, read=AsyncMock(return_value=b"{}"))
    modal.upload._values = [attachment]
    submitted = interaction(ctx)
    await modal.on_submit(submitted)
    importer.assert_awaited_once_with(11, b"{}")
    assert session_for(ctx).route.source == "chunilib:export"
    assert ctx.message.payloads[-1]["embed"].title == "主資料介面"
    submitted.followup.send.assert_not_awaited()
    modal.stop()


@pytest.mark.asyncio
async def test_account_source_scores_preserve_unmapped_imported_records(
    ctx, monkeypatch
):
    session_for(ctx).route = PanelRoute("account", source="chunilib:export")
    lookup = AsyncMock(
        return_value=[
            {
                "id": "chunilib:export:local-only",
                "title": "Unmapped source title",
                "diff": "MAS",
                "score": 1009000,
            }
        ]
    )
    monkeypatch.setattr(ctx.bot.get_cog("V2Players"), "matching_rows", lookup)
    await navigate(
        ctx,
        interaction(ctx),
        "source_scores",
        values={"query": "Unmapped source title"},
    )
    lookup.assert_awaited_once()
    assert lookup.call_args.args[1:] == ("Unmapped source title", "chunilib:export")
    assert session_for(ctx).route.section == "source_scores"
    assert "1,009,000" in ctx.message.payloads[-1]["embeds"][0].description
    assert ctx.message.payloads[-1]["embeds"][0].title == "Unmapped source title"


@pytest.mark.asyncio
async def test_three_account_sources_chunilib_homepage_and_chunirec_region(ctx):
    from chuni_penguin.database import PlayerSource
    from chuni_penguin.ui.account_state import CHUNILIB_URL
    from chuni_penguin.ui.panel import ChunirecRegionSelect, SourceSelect

    await ctx.bot.database.writer.add(
        PlayerSource(
            discord_id=11,
            source="chunirec:jp",
            credential="fixture-value",
            status="synced",
        )
    )
    await show_account(ctx)
    statuses = ctx.response.payloads[-1]["embed"].fields[0].value
    assert "**Chunirec**：jp2 未連結；jp 已連結" in statuses
    anchor = ctx.response
    source = next(
        c for c in session_for(ctx).active_view.children if isinstance(c, SourceSelect)
    )
    assert [c.label for c in source.options] == [
        "CHUNITHM-NET",
        "Chunirec",
        "Chunilib 匯出檔",
    ]
    source._values = ["chunilib:export"]
    await source.callback(interaction(ctx))
    assert ctx.response is anchor
    view = session_for(ctx).active_view
    assert any(
        isinstance(c, discord.ui.Button) and c.url == CHUNILIB_URL
        for c in view.children
    )
    assert CHUNILIB_URL == "https://chuni.tsaibee.org/"
    assert any(getattr(c, "action", None) == "import" for c in view.children)
    assert_rows(view)
    source = next(c for c in view.children if isinstance(c, SourceSelect))
    source._values = ["chunirec:jp2"]
    await source.callback(interaction(ctx))
    region = next(
        c
        for c in session_for(ctx).active_view.children
        if isinstance(c, ChunirecRegionSelect)
    )
    assert [c.value for c in region.options] == ["chunirec:jp2", "chunirec:jp"]
    region._values = ["chunirec:jp"]
    await region.callback(interaction(ctx))
    assert session_for(ctx).route.source == "chunirec:jp"
    assert ctx.response is anchor
    view = session_for(ctx).active_view
    source = next(c for c in view.children if isinstance(c, SourceSelect))
    assert len(source.options) == 3
    assert next(o for o in source.options if o.default).value == "chunirec:jp"
    assert_rows(view)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,tab", [("chart", "view"), ("score", "scores")])
async def test_song_entry_preserves_requested_initial_view_and_optional_search(
    ctx, monkeypatch, entry, tab
):
    import chuni_penguin.ui.entrypoints as entries
    import chuni_penguin.ui.panel as panel

    monkeypatch.setattr(
        entries.PenguinContext, "from_interaction", AsyncMock(return_value=ctx)
    )
    show = AsyncMock()
    monkeypatch.setattr(panel, "show_song", show)
    command = ctx.bot.tree.get_command(entry)
    assert command.get_parameter("query").required is False
    assert command.get_parameter("query").autocomplete
    await command.callback(interaction(ctx), "gengaozo")
    show.assert_awaited_once_with(ctx, "gengaozo", tab=tab)
    await command.callback(interaction(ctx))
    assert session_for(ctx).route.section == entry
    view = session_for(ctx).active_view
    assert [c.label for c in view.children if isinstance(c, discord.ui.Button)] == [
        "關閉"
    ]
    assert_rows(view)
