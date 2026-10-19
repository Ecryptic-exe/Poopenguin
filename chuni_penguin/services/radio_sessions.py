"""Bot-owned radio observations; no Cog or transport ownership."""

import contextlib
import random
from collections import deque
from pathlib import Path

import discord

from chuni_penguin.constants import ASSETS_DIR
from chuni_penguin.context import PenguinGuildContext
from chuni_penguin.utils import AsyncRWLockMapping


class RadioState:
    __slots__ = (
        "allowed_song_ids",
        "available_tracks",
        "ctx",
        "genres",
        "inactive_tracks",
        "levels",
        "stopped_by",
        "total_tracks",
        "versions",
        "volume",
    )

    def __init__(
        self,
        ctx: PenguinGuildContext,
        allowed_song_ids: set[int] | None = None,
        volume: int = 15,
    ):
        if volume < 0 or volume > 100:
            msg = "volume must be between 0 and 100"
            raise ValueError(msg)

        self.ctx = ctx
        self.allowed_song_ids = allowed_song_ids
        self.stopped_by: discord.User | discord.Member | None = None
        self.volume = volume
        self.inactive_tracks = 0
        self.load_tracks()

    @property
    def remaining_tracks(self):
        return len(self.available_tracks)

    def load_tracks(self):
        self.available_tracks = deque(
            p
            for p in (ASSETS_DIR / "audio").iterdir()
            if p.is_file()
            and p.stem.isdigit()
            and (self.allowed_song_ids is None or int(p.stem) in self.allowed_song_ids)
        )
        self.total_tracks = len(self.available_tracks)
        random.shuffle(self.available_tracks)

    def next_track(self) -> Path | None:
        try:
            return self.available_tracks.popleft()
        except IndexError:
            self.load_tracks()
            return self.available_tracks.popleft() if self.available_tracks else None


class RadioSessionService:
    def __init__(self):
        self.states: AsyncRWLockMapping[int, RadioState] = AsyncRWLockMapping()
        self.closed = False

    async def clear_state(self, channel_id: int):
        async with self.states.write() as states:
            with contextlib.suppress(KeyError):
                del states[channel_id]

    async def close(self):
        self.closed = True
        async with self.states.write() as states:
            states.clear()
