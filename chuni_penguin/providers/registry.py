"""Small catalog-only registry. No guessed capabilities or implicit fallback."""

import asyncio
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from .models import (
    Capability,
    CatalogRequest,
    CatalogSnapshot,
    ProviderDescriptor,
)

REGISTRY_KEY = "registry"


class ProviderError(Exception):
    def __init__(self, key: str, reason: str):
        self.key = key
        self.reason = reason
        super().__init__(f"Provider {key}: {reason}")


class CatalogReader(Protocol):
    descriptor: ProviderDescriptor

    async def start(self) -> None: ...

    async def read_catalog(self) -> CatalogSnapshot: ...

    async def close(self) -> None: ...


def _validate_result(result: CatalogSnapshot, descriptor: ProviderDescriptor):
    origin = result.provenance
    if (
        origin.provider_key != descriptor.key
        or origin.source_namespace != descriptor.source_namespace
        or origin.scope != descriptor.scope
        or origin.observed_at.utcoffset() is None
        or any(
            song.ref.namespace != descriptor.source_namespace
            or any(chart.ref.song != song.ref for chart in song.charts)
            for song in result.songs
        )
    ):
        raise ProviderError(descriptor.key, "invalid_result")


@dataclass
class _Entry:
    reader: CatalogReader
    enabled: bool
    ready: bool = False
    state: str = "registered"
    reason: str = ""


class ProviderRegistry:
    def __init__(self, *, timeout: float = 10):
        if not math.isfinite(timeout) or timeout <= 0:
            msg = "Provider timeout must be finite and positive."
            raise ValueError(msg)
        self.timeout = timeout
        self._entries: dict[str, _Entry] = {}
        self._started = False
        self._closed = False
        self._consumers: set[asyncio.Task] = set()

    def register(self, reader: CatalogReader, *, enabled: bool = True):
        key = reader.descriptor.key
        if self._started or self._closed or not key or key in self._entries:
            raise ProviderError(key, "invalid_registration")
        self._entries[key] = _Entry(reader, enabled)

    @property
    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(entry.reader.descriptor for entry in self._entries.values())

    def status(self) -> dict[str, dict[str, str | bool]]:
        return {
            key: {
                "enabled": entry.enabled,
                "ready": entry.ready,
                "state": entry.state,
                "reason": entry.reason,
            }
            for key, entry in self._entries.items()
        }

    async def _start_one(self, entry: _Entry):
        if not entry.enabled:
            entry.state = "disabled"
            return
        try:
            async with asyncio.timeout(self.timeout):
                await entry.reader.start()
        except Exception as exc:  # noqa: BLE001 - isolate source, redact payload/path
            entry.state, entry.reason = "failed", type(exc).__name__
        else:
            entry.ready, entry.state, entry.reason = True, "ready", ""

    async def start(self):
        if self._closed:
            raise ProviderError(REGISTRY_KEY, "closed")
        if self._started:
            return
        self._started = True
        # One failing source cannot prevent a sibling from starting.
        await asyncio.gather(*(self._start_one(e) for e in self._entries.values()))

    async def read_catalog(self, request: CatalogRequest) -> CatalogSnapshot:
        task = asyncio.current_task()
        if task is not None:
            self._consumers.add(task)
        try:
            return await self._read_catalog(request)
        finally:
            if task is not None:
                self._consumers.discard(task)

    async def _read_catalog(self, request: CatalogRequest) -> CatalogSnapshot:
        if self._closed or not self._started:
            raise ProviderError(REGISTRY_KEY, "not_ready")
        if request.max_age is not None and request.max_age.total_seconds() < 0:
            raise ProviderError(REGISTRY_KEY, "invalid_max_age")
        key = (
            request.provider_key
            if request.provider_key is not None
            else request.preferred_provider
        )
        if key is None:
            # Registration order is the instance preference. Never cross scopes.
            key = next(
                (
                    key
                    for key, entry in self._entries.items()
                    if entry.enabled and entry.reader.descriptor.scope == request.scope
                ),
                None,
            )
        if key is None or key not in self._entries:
            raise ProviderError(key or "registry", "no_matching_provider")
        entry = self._entries[key]
        descriptor = entry.reader.descriptor
        if descriptor.scope != request.scope:
            raise ProviderError(key, "scope_mismatch")
        if Capability.CATALOG_READ not in descriptor.capabilities:
            raise ProviderError(key, "unsupported_capability")
        if not entry.enabled or not entry.ready:
            raise ProviderError(key, "unavailable")
        try:
            async with asyncio.timeout(self.timeout):
                result = await entry.reader.read_catalog()
            _validate_result(result, descriptor)
        except Exception as exc:  # noqa: BLE001
            entry.state, entry.reason = "degraded", type(exc).__name__
            reason = (
                exc.reason if isinstance(exc, ProviderError) else type(exc).__name__
            )
            raise ProviderError(key, reason) from None
        entry.state, entry.reason = "ready", ""
        if request.max_age is not None:
            age = datetime.now(UTC) - result.provenance.observed_at
            stale = result.stale or age > request.max_age or age.total_seconds() < 0
            if stale and not request.allow_stale:
                raise ProviderError(key, "stale")
            return replace(result, stale=stale)
        return result

    async def close(self):
        if self._closed:
            return
        self._closed = True
        consumers = [
            task for task in self._consumers if task is not asyncio.current_task()
        ]
        for task in consumers:
            task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        for entry in self._entries.values():
            try:
                async with asyncio.timeout(self.timeout):
                    await entry.reader.close()
            except Exception as exc:  # noqa: BLE001
                entry.reason = type(exc).__name__
            finally:
                entry.ready, entry.state = False, "closed"
