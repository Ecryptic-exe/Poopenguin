"""Template resources belong to a Bot lifetime, independent of Cog reloads."""

from weakref import WeakSet

import discord

from chuni_penguin.repositories.copypasta import CopypastaRepository
from chuni_penguin.services.copypasta import CopypastaManager


class CopypastaService:
    def __init__(self, repository: CopypastaRepository):
        self.manager = CopypastaManager(repository)
        self.last_used: dict[tuple[int, str], int] = {}
        self.views: WeakSet[discord.ui.View | discord.ui.Modal] = WeakSet()
        self.closed = False

    def track_view(self, view: discord.ui.View | discord.ui.Modal) -> None:
        if self.closed:
            view.stop()
            msg = "Copypasta service is closed."
            raise RuntimeError(msg)
        self.views.add(view)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        errors: list[Exception] = []
        for view in tuple(self.views):
            try:
                view.stop()
            except Exception as exc:  # noqa: BLE001 - stop all other owned interactions
                errors.append(exc)
        self.views.clear()
        if errors:
            raise ExceptionGroup("Copypasta interaction cleanup failed.", errors)
