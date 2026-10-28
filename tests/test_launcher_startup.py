import asyncio
import importlib
import socket
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import discord
import pytest
import pytest_asyncio
from aiohttp.client_reqrep import ConnectionKey


def dns_failure():
    key = ConnectionKey(
        host="discord.com",
        port=443,
        is_ssl=True,
        ssl=True,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )
    return aiohttp.ClientConnectorDNSError(
        key, socket.gaierror(11001, "getaddrinfo failed")
    )


@pytest_asyncio.fixture
async def launch(monkeypatch):
    from chuni_penguin.utils import monkey

    # Import the entry point without changing shared Discord library hooks.
    with patch.object(monkey, "patch_all"):
        module = importlib.import_module("launcher")
    outcomes = []
    instances = []
    events = []

    class Bot:
        def __init__(self, settings):
            self.index = len(instances)
            self.settings = settings
            self.close = AsyncMock()
            instances.append(self)

        async def __aenter__(self):
            events.append(("enter", self.index))
            return self

        async def __aexit__(self, *args):
            await self.close()
            events.append(("close", self.index))

        async def start(self, token, *, reconnect):
            assert token == "synthetic-token" and reconnect is True
            events.append(("start", self.index))
            outcome = outcomes.pop(0)
            if outcome is not None:
                raise outcome

    async def warning(message, **kwargs):
        events.append(("warning", kwargs["attempt"]))

    async def wait(stopping, delay):
        events.append(("wait", delay))
        return False

    monkeypatch.setattr(module, "ChuniBot", Bot)
    monkeypatch.setattr(
        module,
        "logger",
        SimpleNamespace(error=Mock(), awarning=AsyncMock(side_effect=warning)),
    )
    monkeypatch.setattr(module, "_wait_for_dns_retry", AsyncMock(side_effect=wait))
    signal_handler = Mock()
    monkeypatch.setattr(
        asyncio.get_running_loop(), "add_signal_handler", signal_handler
    )
    monkeypatch.setattr(module.signal, "signal", Mock())
    return SimpleNamespace(
        module=module,
        outcomes=outcomes,
        instances=instances,
        events=events,
        signal_handler=signal_handler,
    )


@pytest.mark.asyncio
async def test_first_dns_failure_retries_with_a_new_closed_client(launch):
    launch.outcomes[:] = [dns_failure(), None]
    await launch.module.run_bot("settings", "synthetic-token")
    assert launch.events == [
        ("enter", 0),
        ("start", 0),
        ("close", 0),
        ("warning", 1),
        ("wait", 2),
        ("enter", 1),
        ("start", 1),
        ("close", 1),
    ]
    assert len(launch.instances) == 2
    for bot in launch.instances:
        bot.close.assert_awaited_once()
    assert launch.signal_handler.call_count == 2


@pytest.mark.asyncio
async def test_persistent_dns_failure_stops_after_five_attempts(launch):
    launch.outcomes[:] = [dns_failure() for _ in range(5)]
    with pytest.raises(aiohttp.ClientConnectorDNSError):
        await launch.module.run_bot("settings", "synthetic-token")
    assert len(launch.instances) == 5
    assert [value for name, value in launch.events if name == "wait"] == [2, 4, 8, 16]
    assert launch.module.logger.awarning.await_count == 4
    for bot in launch.instances:
        bot.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [discord.LoginFailure("fixture"), RuntimeError("fixture"), TimeoutError()],
)
async def test_non_dns_errors_are_not_retried(launch, failure):
    launch.outcomes[:] = [failure]
    with pytest.raises(type(failure)) as raised:
        await launch.module.run_bot("settings", "synthetic-token")
    assert raised.value is failure
    assert len(launch.instances) == 1
    launch.module._wait_for_dns_retry.assert_not_awaited()
    launch.instances[0].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_start_does_not_retry(launch):
    launch.outcomes[:] = [None]
    await launch.module.run_bot("settings", "synthetic-token")
    assert len(launch.instances) == 1
    launch.module._wait_for_dns_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_ctrl_c_during_retry_wait_prevents_another_login(launch, monkeypatch):
    launch.outcomes[:] = [dns_failure(), None]

    async def stop(stopping, delay):
        handler = launch.signal_handler.call_args.args[1]
        handler()
        await stopping.wait()
        return True

    monkeypatch.setattr(launch.module, "_wait_for_dns_retry", stop)
    await launch.module.run_bot("settings", "synthetic-token")
    assert len(launch.instances) == 1


@pytest.mark.asyncio
async def test_windows_signal_fallback_uses_the_same_shutdown_handler(launch):
    launch.signal_handler.side_effect = NotImplementedError
    launch.outcomes[:] = [None]
    await launch.module.run_bot("settings", "synthetic-token")
    calls = launch.module.signal.signal.call_args_list
    assert len(calls) == 2
    assert calls[0].args[1] is calls[1].args[1]


@pytest.mark.asyncio
async def test_dns_exhaustion_has_a_clear_message_and_exit_code(launch, monkeypatch):
    settings = SimpleNamespace(bot=SimpleNamespace(token="synthetic-token"))
    monkeypatch.setattr(launch.module, "get_config", lambda: settings)
    monkeypatch.setattr(launch.module, "use_config", lambda value: nullcontext())
    launch.outcomes[:] = [dns_failure() for _ in range(5)]
    with pytest.raises(SystemExit) as raised:
        await launch.module.startup()
    assert raised.value.code == 1
    message = launch.module.logger.error.call_args.args[0]
    assert "DNS/network" in message
    assert "synthetic-token" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_requested", [False, True])
async def test_real_wait_helper_can_expire_or_stop(stop_requested):
    from chuni_penguin.utils import monkey

    with patch.object(monkey, "patch_all"):
        module = importlib.import_module("launcher")
    stopping = asyncio.Event()
    if stop_requested:
        stopping.set()
    assert await module._wait_for_dns_retry(stopping, 0) is stop_requested


@pytest.mark.asyncio
async def test_interrupt_closes_active_bot_and_second_interrupt_remains_immediate():
    from chuni_penguin.utils import monkey

    with patch.object(monkey, "patch_all"):
        module = importlib.import_module("launcher")
    handler = module.KeyboardInterruptHandler()
    handler.bot = SimpleNamespace(close=AsyncMock())
    handler()
    with pytest.raises(KeyboardInterrupt):
        handler()
    await handler.stopping.wait()
    await asyncio.sleep(0)
    handler.bot.close.assert_awaited_once()
