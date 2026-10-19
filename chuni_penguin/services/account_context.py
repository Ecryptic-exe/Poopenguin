"""Entered account resources that can outlive the task invoking a command."""

import asyncio
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import TYPE_CHECKING, TypeVar

from chuni_penguin.adapters.base import NetworkAdapter

if TYPE_CHECKING:
    from chuni_penguin.services.accounts import AccountService

ClientT = TypeVar("ClientT", bound=NetworkAdapter, covariant=True)


class AccountContext(AbstractAsyncContextManager[ClientT]):
    def __init__(
        self,
        owner: "AccountService",
        manager: AbstractAsyncContextManager[ClientT],
    ):
        self._owner = owner
        self._manager = manager
        self._entered = False
        self._closed = False
        self._exit_lock = asyncio.Lock()

    async def __aenter__(self) -> ClientT:
        if self._entered or self._closed:
            msg = "Account context cannot be re-entered."
            raise RuntimeError(msg)
        if self._owner._closing:
            msg = "Account service is closing."
            raise RuntimeError(msg)
        self._entered = True
        try:
            client = await self._manager.__aenter__()
        except BaseException:
            self._closed = True
            raise
        self._owner._contexts.add(self)
        return client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> bool | None:
        # Always acquire: another caller may still be saving cookies/closing the
        # adapter, even after _closed was set. Shutdown must wait for completion.
        async with self._exit_lock:
            if self._closed:
                return False
            if not self._entered:
                msg = "Account context has not been entered."
                raise RuntimeError(msg)
            self._closed = True
            try:
                return await self._manager.__aexit__(exc_type, exc_value, traceback)
            finally:
                self._owner._contexts.discard(self)
