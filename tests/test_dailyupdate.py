import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import httpx
import msgspec
import pytest
from discord.ext import commands
from PIL import Image
from selectolax.lexbor import LexborHTMLParser

from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.errors import (
    AuthenticationError,
    MaintenanceError,
    NetworkError,
)
from chuni_penguin.cogs.chunithm.dailyupdate import AUTO_FETCH_TIME, DailyUpdateCog
from chuni_penguin.config import Config, use_config
from chuni_penguin.renderers.dailyupdate import PAGE_SIZE, render_dailyupdate
from chuni_penguin.services.accounts import AccountService
from chuni_penguin.services.dailyupdate import (
    HK,
    History,
    LegacyHistory,
    MissingSnapshotError,
    SnapshotStorageError,
    SnapshotStore,
    WindowSnapshots,
    changed_charts,
    fetch_snapshot,
    historical_pair,
    make_snapshot,
    window_start,
)
from chuni_penguin.types import (
    Chart,
    ClearLamp,
    ComboLamp,
    Difficulty,
    OverPower,
    PersonalBest,
    Profile,
    RatingSystem,
    RatingType,
    Song,
)
from chuni_penguin.ui.dailyupdate import DailyUpdateView

NOW = datetime(2026, 10, 28, 17, 32, tzinfo=HK)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    state = {"now": NOW}

    monkeypatch.setattr(
        "chuni_penguin.services.dailyupdate.current_time", lambda: state["now"]
    )
    monkeypatch.setattr(
        "chuni_penguin.cogs.chunithm.dailyupdate.current_time", lambda: state["now"]
    )
    with use_config(Config.from_mapping({})):
        yield lambda moment: state.update(now=moment)


def snapshot(
    day=28, *, hour=6, minute=5, score=1_000_000, second=1_000_000, lamp=ComboLamp.none
):
    profile = Profile(
        username="AZATHOTH · 輪廻玲々",
        level=14,
        reincarnation_stars=6,
        rating_systems=[RatingSystem(type=RatingType.in_game, value=17.12)],
        over_power=OverPower(
            value=(score + second) / 10_000, percentage=(score + second) / 20_200
        ),
    )
    records = [
        PersonalBest(
            song=Song(id=index, title=title),
            chart=Chart(difficulty=Difficulty.master, level="13+"),
            score=value,
            clear_lamp=ClearLamp.clear,
            combo_lamp=lamp if index == 1 else ComboLamp.none,
        )
        for index, title, value in ((1, "L9", score), (2, "輪廻玲々", second))
    ]
    return make_snapshot(
        profile, records, datetime(2026, 10, day, hour, minute, tzinfo=HK)
    )


def make_cog(tmp_path):
    cookies = defaultdict(lambda: "synthetic-cookie")

    async def get_cookie(user_id):
        return SimpleNamespace(cookie=cookies[user_id])

    accounts = SimpleNamespace(
        database=SimpleNamespace(
            cookies=SimpleNamespace(get_by_discord_id=AsyncMock(side_effect=get_cookie))
        )
    )
    bot = SimpleNamespace(
        offline=True,
        services=SimpleNamespace(accounts=accounts),
        wait_until_ready=AsyncMock(),
    )
    cog = DailyUpdateCog(bot)
    cog.store = SnapshotStore(tmp_path)
    cog._session_users = AsyncMock(return_value=[123])
    return cog, cookies


def context(user_id=123):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        defer=AsyncMock(),
        respond_or_edit=AsyncMock(),
    )


def mock_views(monkeypatch):
    factory = Mock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(
            start=AsyncMock(), stop=Mock()
        )
    )
    monkeypatch.setattr(
        "chuni_penguin.cogs.chunithm.dailyupdate.DailyUpdateView", factory
    )
    return factory


def test_hong_kong_boundary_and_utc_conversion():
    assert window_start(datetime(2026, 10, 28, 3, 59, tzinfo=HK)).day == 27
    assert window_start(datetime(2026, 10, 28, 4, 0, tzinfo=HK)).day == 28
    assert window_start(datetime(2026, 10, 27, 20, 0, tzinfo=UTC)).day == 28
    with pytest.raises(ValueError):
        window_start(datetime(2026, 10, 28, 4))  # noqa: DTZ001 - reject naive input


def test_baseline_is_fixed_and_latest_advances(tmp_path):
    store = SnapshotStore(tmp_path)
    baseline = snapshot()
    store.save(123, History(), baseline)
    original = msgspec.json.decode((tmp_path / "123.json").read_bytes())["windows"][
        "2026-10-28"
    ]["baseline"]
    first = snapshot(hour=12, score=1_009_000, lamp=ComboLamp.full_combo)
    store.save(123, store.load(123), first)
    latest = snapshot(
        hour=17,
        minute=32,
        score=1_009_000,
        second=1_007_500,
        lamp=ComboLamp.all_justice,
    )
    saved = store.save(123, store.load(123), latest)
    assert saved.baseline == baseline
    assert saved.latest == latest
    assert len(changed_charts(saved.baseline, first)) == 1
    assert len(changed_charts(saved.baseline, saved.latest)) == 2
    assert latest.total_score - baseline.total_score == 16500
    assert latest.over_power - baseline.over_power == pytest.approx(1.65)
    assert latest.rank_counts["SSS"] - baseline.rank_counts["SSS"] == 2
    assert latest.lamp_counts == {"FC": 1, "AJ": 1}
    encoded = msgspec.json.decode((tmp_path / "123.json").read_bytes())
    assert encoded["schema_version"] == 2
    assert encoded["windows"]["2026-10-28"]["baseline"] == original
    with pytest.raises(AttributeError):
        saved.baseline = latest


def test_migration_uses_available_observation_and_waits_for_successful_save(tmp_path):
    store = SnapshotStore(tmp_path)
    overwritten = snapshot(hour=17, minute=32)
    path = tmp_path / "123.json"
    path.write_bytes(msgspec.json.encode(LegacyHistory(snapshots=[overwritten])))
    original = path.read_bytes()
    history = store.load(123)
    assert history.windows["2026-10-28"] == WindowSnapshots(
        baseline=overwritten, latest=overwritten
    )
    assert path.read_bytes() == original
    later = snapshot(hour=18, score=1_009_000)
    saved = store.save(123, history, later)
    assert saved.baseline.captured_at.hour == 17
    assert saved.latest == later
    assert msgspec.json.decode(path.read_bytes())["schema_version"] == 2


def test_migration_chooses_earliest_if_legacy_file_contains_multiple_observations(
    tmp_path,
):
    store = SnapshotStore(tmp_path)
    early, late = snapshot(), snapshot(hour=17, score=1_009_000)
    (tmp_path / "123.json").write_bytes(
        msgspec.json.encode(LegacyHistory(snapshots=[late, early]))
    )
    saved = store.load(123).windows["2026-10-28"]
    assert (saved.baseline, saved.latest) == (early, late)


def test_retention_is_thirty_windows_without_an_older_anchor(tmp_path):
    store = SnapshotStore(tmp_path)
    initial = snapshot()
    for offset in range(65):
        moment = initial.captured_at + timedelta(days=offset)
        boundary = window_start(moment)
        current = msgspec.structs.replace(
            initial,
            captured_at=moment,
            window_start=boundary,
            window_end=boundary + timedelta(days=1),
        )
        store.save(123, store.load(123, now=moment), current)
    history = store.load(123, now=moment)
    assert len(history.windows) == 30
    assert min(history.windows) == (boundary.date() - timedelta(days=29)).isoformat()
    assert not list(tmp_path.glob("*.tmp"))


def test_historical_pair_uses_one_windows_baseline_and_latest(tmp_path):
    store = SnapshotStore(tmp_path)
    first, latest = snapshot(27), snapshot(27, hour=21, score=1_009_000)
    store.save(123, History(), first)
    store.save(123, store.load(123), latest)
    assert historical_pair(store.load(123), date(2026, 10, 27)) == (first, latest)
    with pytest.raises(MissingSnapshotError):
        historical_pair(store.load(123), date(2026, 10, 26))


def test_corrupt_history_is_not_silently_replaced(tmp_path):
    path = tmp_path / "123.json"
    path.write_bytes(b'{"broken":')
    with pytest.raises(SnapshotStorageError):
        SnapshotStore(tmp_path).load(123)
    assert path.read_bytes() == b'{"broken":'


def test_failed_atomic_replace_keeps_original_history(tmp_path, monkeypatch):
    from pathlib import Path

    store = SnapshotStore(tmp_path)
    store.save(123, History(), snapshot())
    original = (tmp_path / "123.json").read_bytes()
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError()))
    with pytest.raises(OSError):
        store.save(123, store.load(123), snapshot(hour=17, score=1_009_000))
    assert (tmp_path / "123.json").read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_auto_baseline_then_no_changes_one_song_and_two_cumulative_songs(
    tmp_path, monkeypatch, clock
):
    cog, _ = make_cog(tmp_path)
    baseline = snapshot()
    after_one = snapshot(hour=12, score=1_009_000)
    after_two = snapshot(hour=17, minute=32, score=1_009_000, second=1_007_500)
    fetch = AsyncMock(side_effect=[baseline, baseline, after_one, after_two, after_two])
    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot", fetch)
    views = mock_views(monkeypatch)
    clock(baseline.captured_at)
    await cog.auto_fetch()
    ctx = context()
    await cog.dailyupdate.callback(cog, ctx)
    assert ctx.respond_or_edit.call_args.args == (
        "No changes since 2026-10-28 06:05 GMT+8.",
    )
    views.assert_not_called()
    for expected, current in ((1, after_one), (2, after_two), (2, after_two)):
        clock(current.captured_at)
        await cog.dailyupdate.callback(cog, context())
        previous, latest = views.call_args.args[1:]
        assert previous == baseline and latest == current
        assert len(changed_charts(previous, latest)) == expected
    assert cog.store.load(123).windows["2026-10-28"].baseline == baseline


@pytest.mark.asyncio
async def test_missing_today_baseline_starts_now_even_if_yesterday_exists(
    tmp_path, monkeypatch
):
    cog, _ = make_cog(tmp_path)
    cog.store.save(123, History(), snapshot(27))
    first = snapshot(hour=14, minute=50)
    second = snapshot(hour=17, minute=32, score=1_009_000)
    monkeypatch.setattr(
        "chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot",
        AsyncMock(side_effect=[first, second]),
    )
    views = mock_views(monkeypatch)
    ctx = context()
    await cog.dailyupdate.callback(cog, ctx)
    assert (
        "Tracking starts now (2026-10-28 14:50" in ctx.respond_or_edit.call_args.args[0]
    )
    await cog.dailyupdate.callback(cog, context())
    assert views.call_args.args[1:] == (first, second)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError("private-cookie"),
        MaintenanceError(),
        NetworkError("private-cookie"),
        TimeoutError(),
    ],
)
async def test_fetch_failure_does_not_rewrite_snapshots_or_migrate_legacy(
    tmp_path, monkeypatch, error
):
    cog, _ = make_cog(tmp_path)
    path = tmp_path / "123.json"
    path.write_bytes(msgspec.json.encode(LegacyHistory(snapshots=[snapshot()])))
    original = path.read_bytes()
    monkeypatch.setattr(
        "chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot",
        AsyncMock(side_effect=error),
    )
    ctx = context()
    await cog.dailyupdate.callback(cog, ctx)
    assert path.read_bytes() == original
    assert "private-cookie" not in ctx.respond_or_edit.call_args.args[0]
    if isinstance(error, AuthenticationError):
        assert cog.store.session_expired(123, "synthetic-cookie")
        assert b"synthetic-cookie" not in (tmp_path / "sessions/123.json").read_bytes()


@pytest.mark.asyncio
async def test_automatic_failure_isolated_and_login_change_allows_recovery(
    tmp_path, monkeypatch, clock
):
    cog, cookies = make_cog(tmp_path)
    cog._session_users = AsyncMock(return_value=[123, 456])
    safe_log = AsyncMock()
    monkeypatch.setattr(
        "chuni_penguin.cogs.chunithm.dailyupdate.logger.awarning", safe_log
    )
    sleep = AsyncMock()
    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.asyncio.sleep", sleep)
    fetch = AsyncMock(
        side_effect=[AuthenticationError("DO-NOT-LOG-COOKIE"), snapshot()]
    )
    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot", fetch)
    await cog.auto_fetch()
    assert "2026-10-28" in cog.store.load(456).windows
    assert not (tmp_path / "123.json").exists()
    assert "DO-NOT-LOG-COOKIE" not in repr(safe_log.call_args_list)
    sleep.assert_awaited_once_with(3.0)
    ctx = context()
    await cog.dailyupdate.callback(cog, ctx)
    assert "Run /login again" in ctx.respond_or_edit.call_args.args[0]
    assert fetch.await_count == 2
    cookies[123] = "new-login-cookie"
    fetch.side_effect = None
    fetch.return_value = snapshot(hour=17, minute=32)
    ctx = context()
    await cog.dailyupdate.callback(cog, ctx)
    assert "Tracking starts now" in ctx.respond_or_edit.call_args.args[0]
    assert not cog.store._session_path(123).exists()


@pytest.mark.asyncio
async def test_network_failure_before_first_capture_uses_first_manual_success(
    tmp_path, monkeypatch
):
    cog, _ = make_cog(tmp_path)
    fetch = AsyncMock(side_effect=[MaintenanceError(), snapshot(hour=14, minute=50)])
    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot", fetch)
    await cog.auto_fetch()
    assert not (tmp_path / "123.json").exists()
    ctx = context()
    await cog.dailyupdate.callback(cog, ctx)
    assert "Tracking starts now" in ctx.respond_or_edit.call_args.args[0]
    assert cog.store.load(123).windows["2026-10-28"].baseline.captured_at.hour == 14


@pytest.mark.asyncio
async def test_existing_early_baseline_survives_0605_and_restart_skips_it(
    tmp_path, monkeypatch
):
    cog, _ = make_cog(tmp_path)
    early = snapshot(hour=5)
    cog.store.save(123, History(), early)
    fetch = AsyncMock(return_value=snapshot(score=1_009_000))
    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot", fetch)
    await cog.auto_fetch()
    saved = cog.store.load(123).windows["2026-10-28"]
    assert saved.baseline == early
    assert saved.latest.charts["1:MAS"].score == 1_009_000
    await cog._fetch_accounts(only_missing=True)
    fetch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "moment,due",
    [
        (datetime(2026, 10, 28, 6, 4, tzinfo=HK), False),
        (datetime(2026, 10, 28, 6, 5, tzinfo=HK), True),
        (NOW, True),
        (datetime(2026, 10, 29, 2, 0, tzinfo=HK), True),
        (datetime(2026, 10, 29, 5, 0, tzinfo=HK), False),
    ],
)
async def test_restart_compensation_uses_current_day_window(
    tmp_path, clock, moment, due
):
    clock(moment)
    cog, _ = make_cog(tmp_path)
    cog._fetch_accounts = AsyncMock()
    await cog.before_auto_fetch()
    cog.bot.wait_until_ready.assert_awaited_once()
    if due:
        cog._fetch_accounts.assert_awaited_once_with(only_missing=True)
    else:
        cog._fetch_accounts.assert_not_awaited()
    assert cog.auto_fetch.time == [AUTO_FETCH_TIME]
    assert AUTO_FETCH_TIME.tzinfo is HK


@pytest.mark.asyncio
async def test_next_day_builds_new_baseline_and_batch_cannot_cross_into_new_window(
    tmp_path, monkeypatch, clock
):
    cog, _ = make_cog(tmp_path)
    cog.store.save(123, History(), snapshot())
    tomorrow = snapshot(29)
    clock(tomorrow.captured_at)
    fetch = AsyncMock(return_value=tomorrow)
    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot", fetch)
    await cog.auto_fetch()
    assert cog.store.load(123).windows["2026-10-29"].baseline == tomorrow
    next_window = window_start(tomorrow.captured_at)
    crossed = snapshot(30, hour=4, minute=1)
    fetch.return_value = crossed
    original = (tmp_path / "123.json").read_bytes()
    await cog._capture(123, 123, expected_window=next_window)
    assert (tmp_path / "123.json").read_bytes() == original


@pytest.mark.asyncio
async def test_manual_and_automatic_fetches_share_per_user_lock(tmp_path, monkeypatch):
    cog, _ = make_cog(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def fetch(accounts, ctx):
        calls.append(ctx)
        if len(calls) == 1:
            started.set()
            await release.wait()
            return snapshot()
        return snapshot(hour=17, minute=32, score=1_009_000)

    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot", fetch)
    views = mock_views(monkeypatch)
    automatic = asyncio.create_task(cog._fetch_accounts())
    await started.wait()
    manual = asyncio.create_task(cog.dailyupdate.callback(cog, context()))
    await asyncio.sleep(0)
    assert len(calls) == 1
    release.set()
    await asyncio.gather(automatic, manual)
    saved = cog.store.load(123).windows["2026-10-28"]
    assert saved.baseline == snapshot()
    assert len(changed_charts(saved.baseline, saved.latest)) == 1
    views.assert_called_once()


@pytest.mark.asyncio
async def test_historical_command_reads_saved_pair_without_network_or_writes(
    tmp_path, monkeypatch
):
    cog, _ = make_cog(tmp_path)
    first, latest = snapshot(27), snapshot(27, hour=22, score=1_009_000)
    cog.store.save(123, History(), first)
    cog.store.save(123, cog.store.load(123), latest)
    original = (tmp_path / "123.json").read_bytes()
    fetch = AsyncMock()
    monkeypatch.setattr("chuni_penguin.cogs.chunithm.dailyupdate.fetch_snapshot", fetch)
    views = mock_views(monkeypatch)
    await cog.dailyupdate.callback(cog, context(), date="2026-10-27")
    assert views.call_args.args[1:] == (first, latest)
    fetch.assert_not_awaited()
    assert (tmp_path / "123.json").read_bytes() == original
    ctx = context()
    await cog.dailyupdate.callback(cog, ctx, date="2026-10-26")
    assert "missing" in ctx.respond_or_edit.call_args.args[0]


@pytest.mark.asyncio
async def test_cog_unload_cancels_before_ready_task(tmp_path):
    cog, _ = make_cog(tmp_path)
    cog.bot.offline = False
    ready = asyncio.Event()
    cog.bot.wait_until_ready = ready.wait
    await cog.cog_load()
    assert cog.auto_fetch.is_running()
    await cog.cog_unload()
    assert cog.auto_fetch.get_task().done()


@pytest.mark.asyncio
async def test_account_service_accepts_background_owner_id_with_reader_tracking():
    record = SimpleNamespace(cookie="synthetic-cookie")
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: record)
        )
    )

    @asynccontextmanager
    async def read():
        yield session

    users = SimpleNamespace(
        fetch_user_config=AsyncMock(return_value=SimpleNamespace(privacy_mode=True))
    )
    accounts = AccountService(
        SimpleNamespace(read_sessionmaker=read),
        users,
        Config.from_mapping({}),
        lambda: None,
        offline=True,
    )
    client = object()

    @asynccontextmanager
    async def network(user_id, cookie):
        assert (user_id, cookie) == (123, "synthetic-cookie")
        yield client

    accounts.chunithm_net = network
    async with accounts.network(123, chunithm_net=True) as connected:
        assert connected is client and accounts.active_users[123] == 1
    assert accounts.active_users[123] == 0


@pytest.mark.asyncio
async def test_background_candidates_exclude_friend_code_only_accounts(tmp_path):
    from chuni_penguin.application.offline import create_fixture_database
    from chuni_penguin.database import Cookie
    from chuni_penguin.services.database import DatabaseService

    url = await create_fixture_database(tmp_path / "candidates.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    try:
        for user_id, cookie in (
            (123, "synthetic-one"),
            (456, "synthetic-two"),
            (789, ""),
        ):
            await database.writer.add(
                Cookie(
                    discord_id=user_id,
                    cookie=cookie,
                    kamaitachi_token=None,
                    is_contributor=False,
                    is_supporter=False,
                )
            )
        cog = DailyUpdateCog(
            SimpleNamespace(
                services=SimpleNamespace(accounts=SimpleNamespace(database=database)),
                offline=True,
            )
        )
        candidates = await cog._session_users()
        assert {123, 456} <= set(candidates)
        assert 789 not in candidates
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_pagination_starts_at_eleven_changed_charts():
    previous = snapshot()
    template = previous.charts["1:MAS"]
    charts = {
        f"{index}:MAS": msgspec.structs.replace(
            template, song_id=index, score=1_009_000
        )
        for index in range(1, 12)
    }
    current = msgspec.structs.replace(snapshot(hour=17), charts=charts)
    view = DailyUpdateView(context(), previous, current)
    assert PAGE_SIZE == 10 and view.page_count == 2
    view.stop()


class FakeAdapter(ChunithmNetAdapter):
    def __init__(self, *, invalid=False):
        self.invalid = invalid
        self.requests = []

    @property
    def _token(self):
        return "synthetic-test-token"

    async def _request_as_soup(self, method, url, **kwargs):
        self.requests.append((method, url))
        if self.invalid:
            return LexborHTMLParser("<html><body>Maintenance</body></html>")
        return LexborHTMLParser(
            '<html><body><select name="genre"></select></body></html>'
        )

    async def _get_hidden_personal_bests(self, **kwargs):
        return []

    async def _process_records(self, records):
        return records


@pytest.mark.asyncio
async def test_adapter_fetches_six_difficulty_pages_with_pacing(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(
        "chuni_penguin.adapters.chunithm_net.adapter.asyncio.sleep", sleep
    )
    adapter = FakeAdapter()
    assert (
        await adapter.get_all_personal_bests(request_delay=0.35, validate_pages=True)
        == []
    )
    assert len(adapter.requests) == sleep.await_count == 6
    assert adapter.requests[-1][1] == "/mobile/record/worldsEndList"
    with pytest.raises(NetworkError):
        await FakeAdapter(invalid=True).get_all_personal_bests(validate_pages=True)


@pytest.mark.asyncio
async def test_pagination_owner_and_page_attachment_replacement():
    with use_config(Config.from_mapping({})):
        ctx = SimpleNamespace(author=SimpleNamespace(id=123))
        view = DailyUpdateView(ctx, snapshot(19, score=1_000_000), snapshot())
        event = SimpleNamespace(
            user=SimpleNamespace(id=999),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        )
        assert not await view.interaction_check(event)
        event.user.id = 123
        assert await view.interaction_check(event)
        view.page_count = 2
        view._image = AsyncMock(return_value=b"synthetic-image")
        view.edit_message = AsyncMock()
        await view._move(event, 1)
        assert view.page == 1
        assert len(view.edit_message.call_args.kwargs["attachments"]) == 1
        view.stop()


@pytest.mark.asyncio
async def test_pagination_finishing_during_render_cannot_resurrect_view():
    with use_config(Config.from_mapping({})):
        view = DailyUpdateView(
            SimpleNamespace(author=SimpleNamespace(id=123)), snapshot(19), snapshot()
        )
        view.page_count = 2

        async def image(page):
            await view.deactivate()
            return b"synthetic-image"

        view._image = image
        view.edit_message = AsyncMock()
        await view._move(
            SimpleNamespace(response=SimpleNamespace(defer=AsyncMock())), 1
        )
        view.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_forces_login_session_and_existing_bulk_parser(monkeypatch):
    client = FakeAdapter()
    client.get_minimal_profile = AsyncMock(
        return_value=Profile(
            username="Player",
            level=1,
            rating_systems=[RatingSystem(type=RatingType.in_game, value=1.0)],
            over_power=OverPower(value=0.0, percentage=0.0),
        )
    )
    client.get_all_personal_bests = AsyncMock(return_value=[])
    calls = []

    @asynccontextmanager
    async def network(ctx, user_id, *, chunithm_net=False):
        calls.append((user_id, chunithm_net))
        yield client

    monkeypatch.setattr("chuni_penguin.services.dailyupdate.asyncio.sleep", AsyncMock())
    result = await fetch_snapshot(
        SimpleNamespace(network=network),
        SimpleNamespace(author=SimpleNamespace(id=123)),
    )
    assert calls == [(123, True)]
    client.get_all_personal_bests.assert_awaited_once_with(
        request_delay=0.35, validate_pages=True
    )
    assert result.player_name == "Player"


@pytest.mark.asyncio
async def test_strict_requests_reject_http_errors():
    adapter = FakeAdapter()
    adapter._client = SimpleNamespace(
        request=AsyncMock(
            return_value=httpx.Response(
                403,
                request=httpx.Request(
                    "GET", "https://chunithm-net-eng.com/mobile/record/"
                ),
                text="Forbidden",
            )
        )
    )
    with pytest.raises(httpx.HTTPStatusError):
        await ChunithmNetAdapter._request_as_soup(
            adapter, "GET", "/mobile/record/", check_status=True
        )


@pytest.mark.asyncio
async def test_cog_registers_shared_hybrid_command_and_optional_date():
    from chuni_penguin.cogs import DEFAULT_EXTENSIONS

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.services = SimpleNamespace(accounts=object())
    bot.offline = True
    try:
        await bot.add_cog(DailyUpdateCog(bot))
        prefix = bot.get_command("dailyupdate")
        slash = bot.tree.get_command("dailyupdate")
        assert isinstance(prefix, commands.HybridCommand)
        assert slash.wrapped is prefix
        assert slash.parameters[0].name == "date"
        assert not slash.parameters[0].required
        spec = next(
            spec for spec in DEFAULT_EXTENSIONS if spec.key == "chunithm.dailyupdate"
        )
        assert spec.default_enabled
        assert spec.required_services == ("accounts",)
    finally:
        await bot.close()


def test_renderer_creates_cjk_image_with_missing_jacket(tmp_path):
    previous, current = snapshot(19, score=1_000_000), snapshot()
    # Use the font delivered beside the source package.
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = render_dailyupdate(
        previous,
        current,
        changed_charts(previous, current),
        0,
        1,
        live=True,
        font_path=root / "assets/dailyupdate/NotoSansCJKjp-Regular.otf",
        jacket_dir=tmp_path,
    )
    with Image.open(BytesIO(data)) as image:
        assert image.width == 1000
        assert image.height > 680
