import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from discord.ext import commands
from sqlalchemy import select

from chuni_penguin.application.offline import (
    create_fixture_database,
    deny_network,
    run_check,
)
from chuni_penguin.config import Config, use_config
from chuni_penguin.database import PersonalBest, PlayerSource, Song, UserConfig
from chuni_penguin.privacy import private_command, private_text_check
from chuni_penguin.services.database import DatabaseService
from chuni_penguin.services.phase1_charts import Phase1Charts
from chuni_penguin.services.player_sources import (
    PlayerSourceService,
    SourceError,
    clean_records,
)


@pytest_asyncio.fixture
async def database(tmp_path):
    url = await create_fixture_database(tmp_path / "test.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    await database.writer.execute_fn(lambda session: _set_external_id(session))
    try:
        yield database
    finally:
        await database.close()


async def _set_external_id(session):
    song = await session.get(Song, 900000001)
    song.chunirec_id = "official-id"


def record(**changes):
    return {
        "id": "official-id",
        "diff": "MAS",
        "title": "Different source title",
        "score": 1009000,
        "const": 14.7,
        "is_const_unknown": False,
        "is_clear": True,
        **changes,
    }


def transport(*, status=200, rows=None, account_id=7):
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.chunirec.net"
        assert (
            "user_id" not in request.url.params
            and "user_name" not in request.url.params
        )
        assert request.url.params["region"] in ("jp", "jp2")
        if request.url.path.endswith("profile.json"):
            return httpx.Response(
                status,
                json={"user_id": account_id, "rating": 16.1, "token": "NEVER_STORE"},
            )
        return httpx.Response(
            status, json={"records": rows if rows is not None else [record()]}
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_sync_owner_ids_encryption_and_source_isolation(database, tmp_path):
    service = PlayerSourceService(
        database,
        vault_path=tmp_path / "key",
        transport=transport(
            rows=[record(), record(id="unknown", title="Offline fixture")]
        ),
    )
    result = await service.sync_chunirec(11, token="user-secret-token")
    assert (result["mapped"], result["unmapped"]) == (1, 1)
    row = await service.get(11, "chunirec:jp2")
    assert "user-secret-token" not in row.credential + row.payload
    assert "NEVER_STORE" not in row.payload
    assert service.vault.open(row.credential) == "user-secret-token"
    async with database.read_sessionmaker() as session:
        pb = (await session.scalars(select(PersonalBest))).one()
        assert (pb.discord_id, pb.network, pb.song_id) == (
            11,
            "chunirec:jp2",
            900000001,
        )
    assert await service.get(12, "chunirec:jp2") is None
    await service.sync_chunirec(11, token="another-secret", region="jp")
    await service.unlink(11, "chunirec:jp2")
    assert await service.get(11, "chunirec:jp2") is None
    assert await service.get(11, "chunirec:jp") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
async def test_failed_sync_preserves_snapshot_and_redacts_token(
    database, tmp_path, status
):
    service = PlayerSourceService(
        database, vault_path=tmp_path / "key", transport=transport()
    )
    await service.sync_chunirec(11, token="private-token")
    old = await service.get(11, "chunirec:jp2")
    service.transport = transport(status=status)
    with pytest.raises(SourceError) as error:
        await service.sync_chunirec(11)
    assert "private-token" not in str(error.value)
    new = await service.get(11, "chunirec:jp2")
    assert new.payload == old.payload and new.synced_at == old.synced_at
    assert new.status.startswith("failed")


@pytest.mark.asyncio
async def test_token_account_swap_and_invalid_rows_do_not_replace(database, tmp_path):
    service = PlayerSourceService(
        database, vault_path=tmp_path / "key", transport=transport()
    )
    await service.sync_chunirec(11, token="first-token")
    old = (await service.get(11, "chunirec:jp2")).payload
    for bad in (transport(account_id=99), transport(rows=[record(score=1010001)])):
        service.transport = bad
        with pytest.raises(SourceError):
            await service.sync_chunirec(11, token="second-token")
        assert (await service.get(11, "chunirec:jp2")).payload == old


@pytest.mark.asyncio
async def test_import_chunilib_keeps_namespace_and_ignores_secrets(database, tmp_path):
    service = PlayerSourceService(database, vault_path=tmp_path / "key")
    payload = {
        "score": [
            {
                "idx": 900000001,
                "difficulty": "Master",
                "title": "Offline fixture",
                "score": 1000000,
                "isAllJustice": True,
                "token": "SECRET",
            }
        ],
        "cookie": "SECRET",
    }
    result = await service.import_chunilib(22, json.dumps(payload).encode())
    assert result["mapped"] == 0 and result["unmapped"] == 1
    assert result["records"][0]["is_alljustice"] is True
    row = await service.get(22, "chunilib:export")
    assert "SECRET" not in row.payload and row.credential is None
    assert not (tmp_path / "key").exists()
    async with database.read_sessionmaker() as session:
        assert not (await session.scalars(select(PersonalBest))).all()


@pytest.mark.asyncio
async def test_unlink_waits_for_inflight_sync_without_resurrection(database, tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    service = PlayerSourceService(
        database, vault_path=tmp_path / "key", transport=transport()
    )
    original = service._fetch

    async def delayed(token, region):
        started.set()
        await release.wait()
        return await original(token, region)

    service._fetch = delayed
    sync = asyncio.create_task(service.sync_chunirec(11, token="token"))
    await started.wait()
    unlink = asyncio.create_task(service.unlink(11, "chunirec:jp2"))
    await asyncio.sleep(0)
    assert not unlink.done()
    release.set()
    await asyncio.gather(sync, unlink)
    assert await service.get(11, "chunirec:jp2") is None


@pytest.mark.parametrize(
    "changes",
    [{"score": -1}, {"score": True}, {"diff": "WE"}, {"id": None}, {"title": ""}],
)
def test_import_validation(changes):
    with pytest.raises(SourceError):
        clean_records([record(**changes)])


@pytest.mark.asyncio
async def test_private_default_and_comparison_access(database, tmp_path):
    from chuni_penguin.cogs.v2_players import PlayersCog
    from chuni_penguin.services.catalog import UserSettingsService

    users = UserSettingsService(database, Config.from_mapping({}), {})
    assert (await users.fetch_user_config(500)).privacy_mode is False
    await users.save_user_config(
        UserConfig(discord_id=500, synthesis_alt_jacket="default", privacy_mode=True),
        is_new=True,
    )
    cog = PlayersCog.__new__(PlayersCog)
    cog.bot = SimpleNamespace(services=SimpleNamespace(users=users))
    cog.sources = SimpleNamespace(
        records=AsyncMock(return_value=({"records": []}, None))
    )
    ctx = SimpleNamespace(author=SimpleNamespace(id=1))
    with pytest.raises(SourceError, match="分享"):
        await cog.source_rows(ctx, "chunirec:jp2", SimpleNamespace(id=500))
    cog.sources.records.assert_not_awaited()
    settings = await users.fetch_user_config(500)
    settings.privacy_mode = False
    await users.save_user_config(settings, is_new=False)
    assert await cog.source_rows(ctx, "chunirec:jp2", SimpleNamespace(id=500)) == []
    cog.sources.records.assert_awaited_once_with(500, "chunirec:jp2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["sync", "login", "logout", "account delete", "account unlink", "token"],
)
async def test_private_commands_reject_public_prefix(name):
    command = SimpleNamespace(qualified_name=name)
    assert private_command(command)
    ctx = SimpleNamespace(command=command, guild=object(), interaction=None)
    with pytest.raises(commands.CheckFailure):
        await private_text_check(ctx)
    ctx.interaction = object()
    assert await private_text_check(ctx)


def test_bindings_require_hash_evidence_and_difficulty(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    manual = snapshots / "manual.json"
    manual.write_text(
        json.dumps(
            {"charts": [{"title": "Source", "difficulty": "Master", "tags": ["trill"]}]}
        ),
        encoding="utf-8",
    )
    binding = {
        "song_id": 42,
        "difficulty": "MAS",
        "row": 0,
        "reviewer": "operator",
        "evidence": "inspected original chart",
        "source_sha256": hashlib.sha256(manual.read_bytes()).hexdigest(),
    }
    (snapshots / "manual_bindings.json").write_text(
        json.dumps([binding]), encoding="utf-8"
    )
    chart = SimpleNamespace(song_id=42, difficulty="MAS")
    assert Phase1Charts(tmp_path, legacy_annotations=True).tags_for(chart) == {"trill"}
    chart.difficulty = "ULT"
    assert Phase1Charts(tmp_path, legacy_annotations=True).tags_for(chart) == set()
    manual.write_text('{"charts": []}', encoding="utf-8")
    chart.difficulty = "MAS"
    assert Phase1Charts(tmp_path, legacy_annotations=True).tags_for(chart) == set()


def test_all_bundled_statistics_hashes_and_official_ids():
    service = Phase1Charts()
    songs = json.loads(
        (service.root / "chuni_penguin/database/seeds/songs.json").read_text(
            encoding="utf-8"
        )
    )
    by_id = {song["id"]: song for song in songs}
    assert len(service.statistics) == 12
    for entry in service.statistics:
        assert entry["chunirec_id"] == by_id[entry["song_id"]]["chunirec_id"]
        assert entry["source_url"].endswith("/" + entry["chunirec_id"])
        for name, digest in entry["hashes"].items():
            assert (
                hashlib.sha256(
                    (
                        service.root / "snapshots/statistics" / entry["folder"] / name
                    ).read_bytes()
                ).hexdigest()
                == digest
            )
    assert service.manual == {"charts": []}  # runtime annotations come from SQLite
    assert service.bindings == [] and service.semantics == []


def test_b_storage_corruption_and_atomic_backup(tmp_path):
    from chuni_penguin.legacy_b.config import _load, _save

    path = str(tmp_path / "settings.json")
    _save(path, {"old": 1})
    _save(path, {"new": 2})
    assert _load(path, {}) == {"new": 2}
    assert _load(path + ".bak", {}) == {"old": 1}
    Path(path).write_text("broken", encoding="utf-8")
    with pytest.raises(RuntimeError):
        _save(path, {"discarded": True})
    assert Path(path).read_text() == "broken"


@pytest.mark.asyncio
async def test_startup_required_commands_and_safe_defaults():
    result = await run_check()
    assert result["complete"] and result["database_ready"]
    assert not result["network_attempts"] and not result["pending_tasks"]
    assert result["http_client_closed"]
    assert {
        "chart",
        "compare",
        "account",
        "tools",
        "gacha",
        "pick",
        "copypasta",
        "keyword",
    } <= set(result["slash_commands"])
    assert (
        "info" not in result["slash_commands"]
        and "scores" not in result["slash_commands"]
    )
    for name in ("messages", "vote", "admin", "feed", "ai"):
        assert (
            result["extensions"][f"chuni_penguin.cogs.poopenguin.{name}_cog"]["state"]
            == "disabled"
        )


@pytest.mark.asyncio
async def test_optional_b_modules_load_without_registration_or_network(
    tmp_path, monkeypatch
):
    from chuni_penguin.bot import ChuniBot

    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LM_STUDIO_MODEL", "local-test-model")
    url = await create_fixture_database(tmp_path / "optional.sqlite3")
    extra = [
        f"chuni_penguin.legacy_b.{name}_cog"
        for name in ("messages", "vote", "admin", "feed", "ai")
    ]
    settings = Config.from_mapping(
        {"bot": {"db_connection_string": url}, "cogs": {"extra": ",".join(extra)}}
    )
    attempts = []
    with use_config(settings), deny_network(attempts):
        async with ChuniBot(
            settings, offline=True, cache_dir=tmp_path / "cache"
        ) as bot:
            await bot.setup_hook()
            for name in extra:
                assert bot.startup_report.extensions[name].state == "loaded"
            assert (
                bot.get_command("sync") is None
            )  # B's command-registration sync was removed.
            assert bot.tree.get_command("sync") is None
            assert bot.panel_commands["sync"] is not None
            assert bot.tree.get_command("account") is not None
    assert attempts == []


def fake_interaction(user_id=11):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_core_source_commands_deliver_private_scores_b50_and_history_limit(
    database, tmp_path, monkeypatch
):
    import chuni_penguin.cogs.v2_players as module
    from chuni_penguin.cogs.v2_players import PlayersCog
    from chuni_penguin.context import PenguinContext
    from chuni_penguin.services.catalog import UserSettingsService

    service = PlayerSourceService(
        database, vault_path=tmp_path / "key", transport=transport()
    )
    await service.sync_chunirec(11, token="private-token")
    users = UserSettingsService(database, Config.from_mapping({}), {})
    cog = PlayersCog.__new__(PlayersCog)
    cog.sources = service
    cog.bot = SimpleNamespace(services=SimpleNamespace(users=users))
    ctx = SimpleNamespace(author=SimpleNamespace(id=11))
    monkeypatch.setattr(PenguinContext, "from_interaction", AsyncMock(return_value=ctx))
    delivered = []

    class Pagination:
        def __init__(self, ctx, embeds):
            delivered.append(embeds)

        async def start(self, *, ephemeral):
            assert ephemeral is True

    monkeypatch.setattr(module, "EmbedPaginationView", Pagination)
    score = fake_interaction()
    await PlayersCog.score.callback(
        cog, score, query="source title", difficulty="MAS", source="chunirec:jp2"
    )
    score.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert "1,009,000" in delivered[0][0].description
    best = fake_interaction()
    await PlayersCog.b50.callback(cog, best, source="chunirec:jp2")
    assert "Best 50" in delivered[1][0].title
    assert "16.85" in delivered[1][0].description
    recent = fake_interaction()
    await PlayersCog.recent.callback(cog, recent, source="chunirec:jp2")
    assert recent.followup.send.call_args.kwargs["ephemeral"] is True
    assert (
        "不能當作最近遊玩" in recent.followup.send.call_args.kwargs["embed"].description
    )
    other = fake_interaction()
    await users.save_user_config(
        UserConfig(discord_id=22, synthesis_alt_jacket="default", privacy_mode=True),
        is_new=True,
    )
    await PlayersCog.compare.callback(
        cog,
        other,
        user=SimpleNamespace(id=22),
        query="source title",
        source="chunirec:jp2",
    )
    assert "尚未允許分享" in other.followup.send.call_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_native_sync_selects_net_explicitly_and_awaits_cache(
    database, tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager

    from chuni_penguin.cogs.v2_players import PlayersCog
    from chuni_penguin.context import PenguinContext

    client = SimpleNamespace(
        NAME="NET fixture",
        SUPPORTS_DETAILED_RECENT_SCORE=False,
        get_minimal_profile=AsyncMock(),
        get_recent_scores=AsyncMock(return_value=[]),
        get_all_personal_bests=AsyncMock(return_value=[]),
    )
    captured = []

    @asynccontextmanager
    async def network(ctx, **options):
        captured.append(options)
        yield client

    cog = PlayersCog.__new__(PlayersCog)
    cog.bot = SimpleNamespace(
        services=SimpleNamespace(accounts=SimpleNamespace(network=network)),
        database=database,
    )
    ctx = SimpleNamespace(author=SimpleNamespace(id=11))
    monkeypatch.setattr(PenguinContext, "from_interaction", AsyncMock(return_value=ctx))
    interaction = fake_interaction()
    await PlayersCog.sync.callback(cog, interaction, source="net")
    assert captured == [{"kamaitachi": False, "chunithm_net": True}]
    async with database.read_sessionmaker() as session:
        row = await session.get(PlayerSource, (11, "net"))
        assert row.status == "synced" and row.synced_at
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_account_deletion_preserves_other_users(database, tmp_path, monkeypatch):
    from chuni_penguin.context import PenguinContext

    monkeypatch.setattr(
        PenguinContext,
        "from_interaction",
        AsyncMock(return_value=SimpleNamespace(author=SimpleNamespace(id=11))),
    )
    from chuni_penguin.cogs.v2_players import PlayersCog
    from chuni_penguin.database import Cookie

    source = PlayerSourceService(
        database, vault_path=tmp_path / "key", transport=transport()
    )
    for user_id in (11, 22):
        await source.sync_chunirec(user_id, token=f"private-{user_id}")
        await database.writer.add(
            Cookie(discord_id=user_id, cookie="private cookie", kamaitachi_token=None)
        )
    cog = PlayersCog.__new__(PlayersCog)
    cog.sources = source
    cog.bot = SimpleNamespace(
        database=database,
        services=SimpleNamespace(
            accounts=SimpleNamespace(_requests={}, active_users={})
        ),
    )
    interaction = fake_interaction(11)
    await PlayersCog.delete_account.callback(cog, interaction, confirm=True)
    async with database.read_sessionmaker() as session:
        assert await session.get(Cookie, 11) is None
        assert await session.get(Cookie, 22) is not None
    assert await source.get(11, "chunirec:jp2") is None
    assert await source.get(22, "chunirec:jp2") is not None


@pytest.mark.asyncio
async def test_alias_and_condition_search_use_a_canonical_catalog(database):
    from chuni_penguin.database import Alias
    from chuni_penguin.repositories.chart_search import (
        ChartSearchFilter,
        ChartSearchRepository,
    )
    from chuni_penguin.services.catalog import AliasService, SongSearchService

    await database.writer.add(
        Alias(alias="test alias", guild_id=0, song_id=900000001, owner_id=11)
    )
    aliases = AliasService(database)
    await aliases.reload()
    song, alias, confidence = await SongSearchService(database, aliases).find_song(
        "test alias"
    )
    assert song.id == 900000001 and alias.alias == "test alias" and confidence == 100
    charts = await ChartSearchRepository(database).charts(
        ChartSearchFilter(min_const=14.4, max_const=14.6)
    )
    assert [(c.song_id, c.difficulty) for c in charts] == [(900000001, "ULT")]


def test_review_tool_saves_evidence_and_refuses_duplicate(tmp_path):
    from penguin.manual_review import bind_manual

    snapshots = tmp_path / "snapshots"
    seeds = tmp_path / "chuni_penguin/database/seeds"
    snapshots.mkdir()
    seeds.mkdir(parents=True)
    (snapshots / "manual.json").write_text(
        json.dumps({"charts": [{"difficulty": "Master", "tags": ["trill"]}]})
    )
    (snapshots / "manual_bindings.json").write_text("[]")
    (seeds / "songs.json").write_text(
        json.dumps([{"id": 42, "charts": [{"difficulty": "MAS"}]}])
    )
    args = {
        "row": 0,
        "song_id": 42,
        "difficulty": "MAS",
        "reviewer": "operator",
        "evidence": "Verified original source and chart notation",
    }
    bind_manual(tmp_path, **args)
    assert Phase1Charts(tmp_path, legacy_annotations=True).tags_for(
        SimpleNamespace(song_id=42, difficulty="MAS")
    ) == {"trill"}
    with pytest.raises(ValueError, match="already bound"):
        bind_manual(tmp_path, **args)
    assert json.loads((snapshots / "manual_bindings.json.bak").read_text()) == []


def test_real_b50_renderer_has_required_assets(tmp_path):
    from decimal import Decimal

    from PIL import Image

    from chuni_penguin.renderers.b50 import render_b30
    from chuni_penguin.types import (
        Chart,
        ClearLamp,
        ComboLamp,
        Difficulty,
        PersonalBest,
        Song,
    )

    row = PersonalBest(
        song=Song(id=1, title="Offline fixture", jacket_url=""),
        chart=Chart(difficulty=Difficulty.master, level="14+", internal_level=14.7),
        score=1007500,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.all_justice,
        rating=Decimal("16.70"),
    )
    path = tmp_path / "b50.png"
    with path.open("wb") as stream:
        render_b30(
            player_name="Offline fixture",
            output=stream,
            records=[row],
            new_records=[],
            current_rating=16.70,
        )
    with Image.open(path) as result:
        assert result.width == 1872 and result.height > 2000


@pytest.mark.asyncio
async def test_native_views_share_reads_and_release_counts_on_error():
    from collections import defaultdict
    from contextlib import asynccontextmanager

    from chuni_penguin.services.accounts import AccountService

    account = AccountService.__new__(AccountService)
    account.user_locks = defaultdict(asyncio.Lock)
    account.active_users = defaultdict(int)

    @asynccontextmanager
    async def raw(ctx, target, **options):
        yield object()

    account._network_unlocked = raw
    ctx = SimpleNamespace(user=SimpleNamespace(id=11))
    first, second = account.network(ctx), account.network(ctx)
    await first.__aenter__()
    try:
        await asyncio.wait_for(second.__aenter__(), 1)
        assert account.active_users[11] == 2
        assert not account.user_locks[11].locked()
        await second.__aexit__(None, None, None)
        assert account.active_users[11] == 1
    finally:
        await first.__aexit__(None, None, None)
    assert account.active_users[11] == 0
    with pytest.raises(ValueError):
        async with account.network(ctx):
            raise ValueError("fixture")
    assert account.active_users[11] == 0


@pytest.mark.asyncio
async def test_delete_refuses_an_active_recent_view(database, tmp_path, monkeypatch):
    from chuni_penguin.context import PenguinContext

    monkeypatch.setattr(
        PenguinContext,
        "from_interaction",
        AsyncMock(return_value=SimpleNamespace(author=SimpleNamespace(id=11))),
    )
    from chuni_penguin.cogs.v2_players import PlayersCog

    service = PlayerSourceService(
        database, vault_path=tmp_path / "key", transport=transport()
    )
    await service.sync_chunirec(11, token="private-token")
    cog = PlayersCog.__new__(PlayersCog)
    cog.sources = service
    cog.bot = SimpleNamespace(
        database=database,
        services=SimpleNamespace(accounts=SimpleNamespace(active_users={11: 1})),
    )
    interaction = fake_interaction()
    await PlayersCog.delete_account.callback(cog, interaction, confirm=True)
    assert await service.get(11, "chunirec:jp2") is not None
    assert "先關閉" in interaction.followup.send.call_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_native_failure_is_recorded_without_false_success(
    database, tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager

    from chuni_penguin.cogs.v2_players import PlayersCog
    from chuni_penguin.context import PenguinContext

    @asynccontextmanager
    async def unavailable(ctx, **options):
        raise commands.CommandError("fixture source unavailable")
        yield

    cog = PlayersCog.__new__(PlayersCog)
    cog.sources = PlayerSourceService(database, vault_path=tmp_path / "key")
    cog.bot = SimpleNamespace(
        database=database,
        services=SimpleNamespace(accounts=SimpleNamespace(network=unavailable)),
    )
    monkeypatch.setattr(
        PenguinContext,
        "from_interaction",
        AsyncMock(return_value=SimpleNamespace(author=SimpleNamespace(id=11))),
    )
    interaction = fake_interaction()
    with pytest.raises(commands.CommandError):
        await PlayersCog.sync.callback(cog, interaction, source="net")
    row = await cog.sources.get(11, "net")
    assert row.status.startswith("failed") and row.synced_at is None
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_filters_difficulty_version_and_reviewed_tags(monkeypatch):
    from contextlib import asynccontextmanager

    import chuni_penguin.cogs.chunithm.search as module
    from chuni_penguin.cogs.chunithm.search import SearchCog
    from chuni_penguin.converters import Level

    charts = [
        SimpleNamespace(
            id=11,
            song_id=1,
            difficulty="MAS",
            version=None,
            song=SimpleNamespace(version="X-VERSE-X"),
        ),
        SimpleNamespace(
            id=12,
            song_id=2,
            difficulty="ULT",
            version=None,
            song=SimpleNamespace(version="X-VERSE-X"),
        ),
    ]
    cog = SearchCog.__new__(SearchCog)
    cog.utils = SimpleNamespace(
        chart_search=SimpleNamespace(charts=AsyncMock(return_value=charts))
    )
    captured = []

    class View:
        def __init__(self, ctx, selected):
            captured.extend(selected)

        async def start(self):
            pass

    monkeypatch.setattr(module, "SonglistView", View)

    @asynccontextmanager
    async def typing():
        yield

    ctx = SimpleNamespace(
        typing=typing,
        reply=AsyncMock(),
        bot=SimpleNamespace(
            phase1_charts=SimpleNamespace(tagged_chart_ids=AsyncMock(return_value={11}))
        ),
    )
    callback = SearchCog.find.callback.__wrapped__
    await callback(
        cog, ctx, Level("14", 14.0), difficulty="MASTER", version="x-verse", tag="trill"
    )
    assert captured == [charts[0]]
    ctx.bot.phase1_charts = None
    await callback(cog, ctx, Level("14", 14.0), tag="trill")
    assert "目前無法使用" in ctx.reply.call_args.args[0]
