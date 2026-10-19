# ruff: noqa: RUF001
from types import SimpleNamespace

import pytest
import pytest_asyncio

from chuni_penguin.application.offline import create_fixture_database
from chuni_penguin.config import Config
from chuni_penguin.database import Cookie, PlayerSource
from chuni_penguin.services.database import DatabaseService
from chuni_penguin.ui.account_state import ACCOUNT_SOURCES, load_account_state


@pytest_asyncio.fixture
async def database(tmp_path):
    url = await create_fixture_database(tmp_path / "account-state.sqlite3")
    database = DatabaseService(
        Config.from_mapping({"bot": {"db_connection_string": url}})
    )
    await database.start()
    try:
        yield database
    finally:
        await database.close()


class FakeBot:
    def __init__(self, database, *, kamaitachi_loaded):
        self.database = database
        self.kamaitachi_loaded = kamaitachi_loaded

    def begin_db_read(self):
        return self.database.read_sessionmaker()

    def get_cog(self, name):
        if name == "Kamaitachi" and self.kamaitachi_loaded:
            return object()
        return None


def context(database, *, kamaitachi_loaded=True, user_id=999999999):
    return SimpleNamespace(
        author=SimpleNamespace(id=user_id),
        bot=FakeBot(database, kamaitachi_loaded=kamaitachi_loaded),
    )


@pytest.mark.asyncio
async def test_load_account_state_maps_mixed_sources_without_retaining_secrets(
    database,
):
    await database.writer.merge(
        Cookie(
            discord_id=999999999,
            cookie="#LWP-Cookies-2.0\nnet-secret-cookie",
            kamaitachi_token="kamaitachi-secret-token",
        )
    )
    for row in (
        PlayerSource(
            discord_id=999999999,
            source="net",
            credential=None,
            payload=None,
            synced_at="2026-10-15T01:00:00+08:00",
            status="synced",
        ),
        PlayerSource(
            discord_id=999999999,
            source="kamaitachi",
            credential=None,
            payload=None,
            synced_at="2026-10-14T01:00:00+08:00",
            status="failed; partial cache possible",
        ),
        PlayerSource(
            discord_id=999999999,
            source="chunirec:jp2",
            credential="chunirec-secret-credential",
            payload="chunirec-secret-payload",
            synced_at="2026-10-13T01:00:00+08:00",
            status="failed; previous snapshot retained",
        ),
        PlayerSource(
            discord_id=999999999,
            source="chunirec:jp",
            credential=None,
            payload="chunirec-public-snapshot",
            synced_at="2026-10-11T01:00:00+08:00",
            status="synced",
        ),
        PlayerSource(
            discord_id=999999999,
            source="chunilib:export",
            credential=None,
            payload="chunilib-secret-payload",
            synced_at="2026-10-12T01:00:00+08:00",
            status="synced",
        ),
    ):
        await database.writer.merge(row)

    state = await load_account_state(context(database))

    assert [(source.label, source.key) for source in state.sources] == list(
        ACCOUNT_SOURCES
    )
    net = state.source("net")
    assert net is not None
    assert (net.linked, net.can_sync, net.can_logout, net.can_token) == (
        True,
        True,
        True,
        True,
    )
    assert (net.primary_action, net.status, net.last_sync) == (
        "run_sync",
        "已同步",
        "2026-10-15T01:00:00+08:00",
    )

    kamaitachi = state.source("kamaitachi")
    assert kamaitachi is not None
    assert kamaitachi.can_sync and not kamaitachi.can_unlink
    assert kamaitachi.status == "同步失敗，快取可能部分更新"

    jp2 = state.source("chunirec:jp2")
    assert jp2 is not None
    assert (jp2.linked, jp2.can_sync, jp2.can_unlink) == (True, True, True)
    assert jp2.status == "已連結 · 同步失敗，先前資料已保留"

    jp = state.source("chunirec:jp")
    assert jp is not None
    assert not jp.linked and not jp.can_sync and not jp.can_unlink
    assert jp.primary_action == "link_source"
    assert jp.status == "未連結 · 已同步"

    chunilib = state.source("chunilib:export")
    assert chunilib is not None
    assert chunilib.linked and not chunilib.can_sync and chunilib.can_unlink
    assert chunilib.primary_action == "import"
    assert state.source("unknown") is None

    rendered = repr(state)
    for secret in (
        "net-secret-cookie",
        "kamaitachi-secret-token",
        "chunirec-secret-credential",
        "chunirec-secret-payload",
        "chunirec-public-snapshot",
        "chunilib-secret-payload",
        "999999999",
    ):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_unavailable_kamaitachi_and_malformed_net_cookie_have_safe_actions(
    database,
):
    await database.writer.merge(
        Cookie(
            discord_id=999999999,
            cookie="malformed-net-cookie",
            kamaitachi_token=None,
        )
    )

    state = await load_account_state(context(database, kamaitachi_loaded=False))

    net = state.source("net")
    assert net is not None
    assert net.linked and net.can_logout
    assert not net.can_sync and not net.can_token
    assert net.primary_action == "login"
    assert net.status == "憑證需要更新"
    assert net.notice == "NET 憑證格式無效，請重新登入。"

    kamaitachi = state.source("kamaitachi")
    assert kamaitachi is not None
    assert not kamaitachi.can_sync and not kamaitachi.can_unlink
    assert kamaitachi.primary_action == "refresh"
    assert kamaitachi.notice == "Kamaitachi 需由管理員配置並啟用既有整合。"
