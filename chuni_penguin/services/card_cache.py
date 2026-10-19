import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class CardDataCache:
    """Bounded reads and successful-result caching for one interactive message."""

    def __init__(self, *, timeout: float = 20):
        self.timeout = timeout
        self._cache: dict[tuple[Any, ...], Any] = {}
        self._pending: dict[tuple[Any, ...], asyncio.Task[Any]] = {}
        self._closed = False

    async def _cached[T](
        self, key: tuple[Any, ...], loader: Callable[[], Awaitable[T]]
    ) -> T:
        if self._closed:
            msg = "This information card has expired."
            raise RuntimeError(msg)
        if key in self._cache:
            return self._cache[key]

        async def bounded_load():
            async with asyncio.timeout(self.timeout):
                return await loader()

        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(bounded_load())
            self._pending[key] = task
        try:
            result = await asyncio.shield(task)
        finally:
            if task.done() and self._pending.get(key) is task:
                del self._pending[key]
        if not self._closed:
            self._cache[key] = result
        return result

    async def close(self):
        self._closed = True
        tasks = tuple(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._cache.clear()
