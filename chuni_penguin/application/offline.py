"""Offline startup check with an ephemeral fixture DB and blocked network sockets."""

import argparse
import asyncio
import contextlib
import json
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chuni_penguin.config import Config, use_config


async def create_fixture_database(path: Path):
    from chuni_penguin.database import Chart, Prefix, Song
    from chuni_penguin.database.base import Base

    url = "sqlite+aiosqlite:///" + path.as_posix()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # Production databases are already WAL. Prepare it once before the
            # legacy read pool and writer start concurrently against this fixture.
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            session.add(
                Song(
                    id=900000001,
                    title="Offline fixture",
                    artist="Fixture",
                    chunithm_catcode=0,
                    genre="ORIGINAL",
                    version="X-VERSE-X",
                    available=True,
                    removed=False,
                )
            )
            await session.flush()
            for difficulty, level, constant in [
                ("MAS", "14", 14.0),
                ("ULT", "14+", 14.5),
            ]:
                session.add(
                    Chart(
                        song_id=900000001,
                        difficulty=difficulty,
                        level=level,
                        const=constant,
                        available=True,
                    )
                )
            session.add(Prefix(guild_id=900000001, prefix="fixture>"))
            await session.commit()
    finally:
        await engine.dispose()
    return url


@contextlib.contextmanager
def deny_network(attempts: list[str]):
    def blocked(*args, **kwargs):
        attempts.append("blocked")
        msg = "Network access is disabled during the offline startup check."
        raise RuntimeError(msg)

    async def blocked_async(*args, **kwargs):
        blocked(*args, **kwargs)

    # Windows Proactor can call ConnectEx without socket.connect, and numeric IP
    # addresses can bypass DNS. Block asyncio's network entry points as well.
    loop = asyncio.get_running_loop()
    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "getaddrinfo", blocked),
        patch.object(loop, "sock_connect", blocked_async),
        patch.object(loop, "create_connection", blocked_async),
        patch.object(loop, "create_datagram_endpoint", blocked_async),
    ):
        yield


async def run_check() -> dict:
    # Importing Bot/Cogs happens only after explicit settings are bound, so this
    # path never reads a real bot.ini, player credentials or deployment database.
    with TemporaryDirectory(prefix="chuni-offline-") as directory:
        path = Path(directory)
        database_url = await create_fixture_database(path / "fixture.sqlite3")
        settings = Config.from_mapping(
            {
                "bot": {"db_connection_string": database_url},
                "dangerous": {"dev": True},
                "credentials": {"kamaitachi_client_id": "offline-client-id"},
            }
        )
        attempts: list[str] = []
        initial_tasks = set(asyncio.all_tasks())
        with use_config(settings), deny_network(attempts):
            from chuni_penguin.bot import ChuniBot

            async with ChuniBot(
                settings, offline=True, cache_dir=path / "cache"
            ) as bot:
                await bot.setup_hook()
                result = bot.startup_report.as_dict()
                result["prefix_commands"] = sorted(
                    c.qualified_name for c in bot.walk_commands()
                )
                result["slash_commands"] = sorted(
                    c.qualified_name for c in bot.tree.walk_commands()
                )
                # Let tasks perform their first iteration before cleanup assertions.
                await asyncio.sleep(0)
            result["network_attempts"] = len(attempts)
            result["http_client_closed"] = bot.caching_http_client.is_closed
            await asyncio.sleep(0)
            result["pending_tasks"] = sorted(
                task.get_name()
                for task in asyncio.all_tasks()
                if task not in initial_tasks and not task.done()
            )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Write the safe startup report as JSON."
    )
    args = parser.parse_args()
    result = asyncio.run(run_check())
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    raise SystemExit(
        0
        if result["complete"]
        and not result["network_attempts"]
        and not result["pending_tasks"]
        else 1
    )


if __name__ == "__main__":
    main()
