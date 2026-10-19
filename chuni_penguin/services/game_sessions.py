"""Bot-owned game state, independent of command Cog lifetimes."""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Protocol

import discord
from discord.ext import commands, songbird

from chuni_penguin.utils import AsyncRWLockMapping

if TYPE_CHECKING:
    from discord.abc import MessageableChannel


class GameSession(Protocol):
    volume: int

    @property
    def ctx(self) -> commands.Context: ...

    @property
    def channel(self) -> "MessageableChannel": ...

    @property
    def voice_client(self) -> songbird.SongbirdClient | None: ...

    async def skip(self) -> None: ...

    async def stop(
        self, stopped_by: discord.User | discord.Member | discord.ClientUser | None
    ) -> None: ...


class GameSessionService:
    def __init__(self):
        self.game_tasks: set[asyncio.Task] = set()
        self.game_sessions: AsyncRWLockMapping[int, GameSession] = AsyncRWLockMapping()
        self.shutting_down = False
        self.enabled = False
        self._close_count = 0

    async def clear_state(self, channel_id: int):
        async with self.game_sessions.write() as sessions:
            with contextlib.suppress(KeyError):
                del sessions[channel_id]

    async def shutdown(
        self,
        actor: discord.ClientUser | None,
        *,
        grace_seconds: float = 300,
    ):
        """Preserve A's warning, five-minute grace, stop and task drain policy."""
        self._close_count += 1
        self.enabled = False
        if self._close_count <= 1:
            self.shutting_down = True
            warning = discord.Embed(
                color=discord.Color.yellow(),
                title="Warning",
                description="I'll be going down for an update soon. Please finish your game in five minutes.",
            )
            async with self.game_sessions.read() as sessions:
                await asyncio.gather(
                    *(
                        session.channel.send(embed=warning)
                        for session in set(sessions.values())
                    )
                )
        pending = set(self.game_tasks)
        if pending and self._close_count <= 1:
            _, pending = await asyncio.wait(pending, timeout=grace_seconds)
        async with self.game_sessions.read() as sessions:
            # One session is indexed by text channel and possibly voice channel.
            for session in set(sessions.values()):
                await session.stop(actor)
        if pending:
            await asyncio.wait(pending)

    async def close(self, actor: discord.ClientUser | None):
        # The Bot already requested the user-visible grace. Direct service users
        # still drain sessions, without introducing a five-minute service wait.
        if self.game_tasks:
            await self.shutdown(actor, grace_seconds=0)
        self.enabled = False
        self.shutting_down = True
        async with self.game_sessions.write() as sessions:
            sessions.clear()
