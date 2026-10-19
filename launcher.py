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


class KeyboardInterruptHandler:
    def __init__(self, bot: "ChuniBot"):
        self.bot: "ChuniBot" = bot
        self._pending: bool = False

    def __call__(
        self,
        signal: int | None = None,
        frame: FrameType | None = None,
    ):
        if self._pending:
            raise KeyboardInterrupt

        self.bot.loop.call_soon_threadsafe(
            self.bot.loop.create_task,
            self.bot.close(),
        )
        self.bot.loop.call_soon_threadsafe(
            lambda: None
        )  # no-op to wake up loop (important!)
        self._pending = True


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


async def run_bot(settings, token):
    async with ChuniBot(settings) as bot:
        handler = KeyboardInterruptHandler(bot)

        try:
            bot.loop.add_signal_handler(signal.SIGINT, handler)
            bot.loop.add_signal_handler(signal.SIGTERM, handler)
        except NotImplementedError:  # fucking windows
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)

        await bot.start(token, reconnect=True)


if __name__ == "__main__":
    args = parse_args()
    warnings.filterwarnings("ignore", r"'asyncio.iscoroutinefunction' is deprecated")

    with (
        contextlib.suppress(KeyboardInterrupt),
        asyncio.Runner(loop_factory=get_loop_factory()) as runner,
    ):
        runner.run(startup(env_file=args.env_file))
