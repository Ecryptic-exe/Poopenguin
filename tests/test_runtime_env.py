from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from chuni_penguin.application.credentials import (
    TokenConfigError,
    read_env_file,
    read_token_file,
    settings_from_env,
)
from chuni_penguin.config import Config
from penguin.discord_check import allowed_request, check_sources


def test_literal_settings_are_explicit_and_do_not_mutate_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KAMAITACHI_API_KEY", "process-value")
    path = tmp_path / ".env"
    path.write_text(
        '\ufeffTOKEN = "synthetic.token-value" # comment\n'
        'BOT_PREFIX="! "\n'
        'KAMAITACHI_API_KEY="${UNCHANGED}#literal"\n'
        "BOT_COGS_EXTRA=chuni_penguin.cogs.chunithm.kamaitachi\n"
        "BOT_COGS_DISABLED=\n"
        "DB_CONNECTION_STRING=sqlite:///not-authorized.sqlite3\n"
        "PATH=ignored\n",
        encoding="utf-8",
    )
    values = read_env_file(path)
    base = Config.from_mapping(
        {"bot": {"db_connection_string": "sqlite:///isolated.sqlite3"}}
    )
    settings = settings_from_env(base, values)
    assert read_token_file(path) == "synthetic.token-value"
    assert settings.bot.default_prefix == "! "
    assert settings.credentials.kamaitachi_api_key == "${UNCHANGED}#literal"
    assert settings.cogs.extra == {"chuni_penguin.cogs.chunithm.kamaitachi"}
    assert settings.bot.db_connection_string == base.bot.db_connection_string
    assert base.bot.token == "" and base.credentials.kamaitachi_api_key is None
    assert "PATH" not in values and "DB_CONNECTION_STRING" not in values
    import os

    assert os.environ["KAMAITACHI_API_KEY"] == "process-value"


@pytest.mark.parametrize(
    "contents,reason",
    [
        ("TOKEN=sensitive\nTOKEN=sensitive\n", "env_duplicate_supported_key"),
        ('TOKEN="sensitive\n', "env_invalid_literal"),
        ("TOKEN=$(sensitive)\n", "env_token_must_be_nonempty_literal"),
        ("TOKEN=\n", "env_token_must_be_nonempty_literal"),
        ("# sensitive\n", "env_token_missing_or_duplicate"),
        ("TOKEN=sensitive\n" + "#" * 65536, "env_size_limit"),
    ],
    ids=["duplicate", "quote", "substitution", "blank", "missing", "size"],
)
def test_invalid_env_has_safe_reason_only(tmp_path, contents, reason):
    path = tmp_path / "sensitive.env"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(TokenConfigError) as error:
        read_token_file(path)
    assert str(error.value) == reason
    assert "sensitive" not in str(error.value) and str(path) not in str(error.value)


def test_example_has_only_supported_options_and_no_credentials():
    values = read_env_file(Path(".env.example"))
    assert values["TOKEN"] == ""
    assert values["CHUNIREC_REGION"] == "jp2"
    assert all(not value for key, value in values.items() if key != "CHUNIREC_REGION")


def test_discord_check_network_boundary():
    assert allowed_request("GET", "https://discord.com/api/v10/users/@me", None)
    assert allowed_request("GET", "wss://gateway.discord.gg/?v=10&encoding=json", None)
    assert allowed_request(
        "GET", "https://discord.com/api/v10/applications/1/guilds/2/commands", 1, 2
    )
    assert not allowed_request(
        "PUT", "https://discord.com/api/v10/applications/1/commands", 1
    )
    assert not allowed_request(
        "POST", "https://discord.com/api/v10/channels/2/messages", 1
    )
    assert not allowed_request(
        "GET", "https://discord.com/api/v10/channels/2/messages", 1
    )
    assert not allowed_request(
        "GET", "https://discord.com/api/v10/applications/1/guilds/3/commands", 1, 2
    )
    assert not allowed_request(
        "GET", "https://discord.com.evil.invalid/api/v10/users/@me", None
    )
    assert not allowed_request("GET", "http://discord.com/api/v10/users/@me", None)


@pytest.mark.asyncio
async def test_source_probe_retains_no_player_data_or_errors():
    from chuni_penguin.services.player_sources import PlayerSourceService, SourceError

    assert (await check_sources({}))["state"] == "not_configured"
    requests = []
    original = PlayerSourceService._fetch

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.params["token"] == "synthetic-owner-token"
        assert "user_id" not in request.url.params
        if request.url.path.endswith("profile.json"):
            return httpx.Response(200, json={"user_id": 7, "name": "private-name"})
        return httpx.Response(
            200,
            json={
                "records": [
                    {"id": "id", "diff": "MAS", "title": "title", "score": 1009000}
                ]
            },
        )

    async def fetch(service, token, region):
        service.transport = httpx.MockTransport(handler)
        return await original(service, token, region)

    with patch.object(PlayerSourceService, "_fetch", fetch):
        result = await check_sources({"CHUNIREC_PLAYER_TOKEN": "synthetic-owner-token"})
    assert result == {
        "state": "verified",
        "region": "jp2",
        "record_count": 1,
        "data_saved": False,
    }
    assert len(requests) == 2

    async def fail(*args):
        raise SourceError("synthetic-owner-token")

    with patch.object(PlayerSourceService, "_fetch", fail):
        result = await check_sources({"CHUNIREC_PLAYER_TOKEN": "synthetic-owner-token"})
    assert result == {
        "state": "source_read_failed",
        "region": "jp2",
        "data_saved": False,
    }
