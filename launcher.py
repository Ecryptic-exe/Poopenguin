# ruff: noqa: E402
import warnings

from chuni_penguin.utils import monkey

monkey.patch_all()

import argparse
import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path
from types import FrameType

import aiohttp
import discord

from chuni_penguin.application.credentials import (
    LOCAL_AI_KEYS,
    TokenConfigError,
    read_env_file,
    settings_from_env,
)
from chuni_penguin.bot import ChuniBot
from chuni_penguin.config import get_config, use_config
from chuni_penguin.logging import logger
from chuni_penguin.utils import get_loop_factory

STARTUP_DNS_RETRY_DELAYS = (2, 4, 8, 16)


class KeyboardInterruptHandler:
    def __init__(self):
        self.bot: "ChuniBot | None" = None
        self.loop = asyncio.get_running_loop()
        self.stopping = asyncio.Event()
        self._pending: bool = False

    def __call__(
        self,
        signal: int | None = None,
        frame: FrameType | None = None,
    ):
        if self._pending:
            raise KeyboardInterrupt

        self._pending = True
        self.loop.call_soon_threadsafe(self.stopping.set)
        if self.bot is not None:
            self.loop.call_soon_threadsafe(self.loop.create_task, self.bot.close())


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the CHUNITHM Discord Bot.")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit literal .env file; overrides supported bot.ini settings.",
    )
    return parser.parse_args(args)


async def startup(*, env_file: Path | None = None):
    try:
        settings = get_config()
        values = read_env_file(env_file) if env_file is not None else {}
        if env_file is not None:
            settings = settings_from_env(settings, values)
        token = settings.bot.token
    except TokenConfigError as exc:
        logger.error("Cannot load Discord token", reason=str(exc))
        sys.exit(1)
    if not token:
        logger.error("Token not found. Set bot.ini bot.token or pass --env-file.")
        sys.exit(1)

    try:
        # Only the local AI module uses process environment; it is still opt-in.
        for key in LOCAL_AI_KEYS:
            if value := values.get(key):
                os.environ[key] = value
        with use_config(settings):
            await run_bot(settings, token)
    except discord.LoginFailure:
        logger.error(
            "Discord rejected the token. Check the selected credential source."
        )
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        logger.error(
            "Message Content Intent not enabled, go to 'https://discord.com/developers/applications' and enable the Message Content Intent."
        )
        sys.exit(1)
    except aiohttp.ClientConnectorDNSError:
        logger.error(
            "Cannot resolve Discord after startup retries. Check your DNS/network "
            "connection, then run the same launch command again."
        )
        sys.exit(1)


async def _wait_for_dns_retry(stopping: asyncio.Event, delay: float) -> bool:
    """Wait for retry time or return True when shutdown was requested."""
    try:
        async with asyncio.timeout(delay):
            await stopping.wait()
    except TimeoutError:
        return False
    return True


async def run_bot(settings, token):
    handler = KeyboardInterruptHandler()
    try:
        handler.loop.add_signal_handler(signal.SIGINT, handler)
        handler.loop.add_signal_handler(signal.SIGTERM, handler)
    except NotImplementedError:
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    attempts = len(STARTUP_DNS_RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        if handler.stopping.is_set():
            return
        try:
            # static_login creates an HTTP session. Always close a failed client
            # and create a fresh one, rather than reusing its connector/session.
            async with ChuniBot(settings) as bot:
                handler.bot = bot
                if handler.stopping.is_set():
                    return
                await bot.start(token, reconnect=True)
        except aiohttp.ClientConnectorDNSError:
            if handler.stopping.is_set():
                return
            if attempt == attempts:
                raise
        else:
            return
        finally:
            handler.bot = None

        delay = STARTUP_DNS_RETRY_DELAYS[attempt - 1]
        await logger.awarning(
            "Cannot resolve Discord; retrying startup.",
            tag="startup_dns_retry",
            attempt=attempt,
            max_attempts=attempts,
            retry_in_seconds=delay,
        )
        if await _wait_for_dns_retry(handler.stopping, delay):
            return


if __name__ == "__main__":
    args = parse_args()
    warnings.filterwarnings("ignore", r"'asyncio.iscoroutinefunction' is deprecated")

    with (
        contextlib.suppress(KeyboardInterrupt),
        asyncio.Runner(loop_factory=get_loop_factory()) as runner,
    ):
        runner.run(startup(env_file=args.env_file))
