"""Verified public catalog GETs used by Chunilib's actual fetchSongData()."""

import asyncio
import hashlib
import json
import math
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import cast

import httpx

from .chunilib_snapshot import BASE, ChunilibSnapshotProvider, _decode
from .models import CatalogSnapshot, ProviderDescriptor
from .registry import ProviderError

KEY = "chunilib_public"
MAX_BYTES = 16 * 1024 * 1024
REQUEST_INTERVAL = 0.25


class ChunilibPublicProvider:
    def __init__(
        self,
        game_version: str = "crossversex",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        # Only this version's public frontend requests were verified.
        if game_version != "crossversex":
            raise ProviderError(KEY, "unsupported_game_version")
        self.decoder = ChunilibSnapshotProvider(Path(), game_version)
        self.descriptor = ProviderDescriptor(
            KEY,
            "chunilib",
            self.decoder.descriptor.scope,
            (
                "2026-10-12 public GET idx.json, song-const/crossversex.json, version.json returned 200",
                "CHUNILIB helps-group-BaIqOEJk.js fetchSongData uses these exact URLs",
            ),
            auth_mode="public_unauthenticated_get",
        )
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._snapshot: CatalogSnapshot | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._next_request = 0.0
        self._cooldown = 0.0

    async def start(self):
        if self._closed:
            raise ProviderError(KEY, "closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=5,
                follow_redirects=False,
                headers={"Accept": "application/json"},
            )

    @staticmethod
    def _retry_after(value: str | None) -> float:
        try:
            seconds = float(value or "60")
            # A malformed/non-finite value uses our conservative local cooldown.
            if not math.isfinite(seconds) or seconds < 0:
                return 60.0
            return max(1.0, seconds)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value or "")
                if parsed.utcoffset() is None:
                    return 60.0
                return max(1.0, (parsed - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return 60.0

    async def _get(self, endpoint: str) -> bytes:
        assert self._client is not None
        for attempt in range(2):
            if time.monotonic() < self._cooldown:
                raise ProviderError(KEY, "rate_limited")
            await asyncio.sleep(max(0.0, self._next_request - time.monotonic()))
            self._next_request = time.monotonic() + REQUEST_INTERVAL
            self._client.cookies.clear()
            try:
                async with self._client.stream("GET", BASE + endpoint) as response:
                    if response.status_code == 429:
                        self._cooldown = time.monotonic() + self._retry_after(
                            response.headers.get("Retry-After")
                        )
                        raise ProviderError(KEY, "rate_limited")
                    if response.status_code in {502, 503, 504} and attempt == 0:
                        continue
                    if response.status_code != 200:
                        raise ProviderError(KEY, f"http_{response.status_code}")
                    if (
                        response.headers.get("content-type", "").split(";")[0].strip()
                        != "application/json"
                    ):
                        raise ProviderError(KEY, "unexpected_content_type")
                    chunks, total = [], 0
                    # HTTPX annotates AsyncIterator but implements an async
                    # generator. Close it before the response on early failure.
                    async with aclosing(
                        cast(AsyncGenerator[bytes, None], response.aiter_bytes())
                    ) as stream:
                        async for chunk in stream:
                            total += len(chunk)
                            if total > MAX_BYTES:
                                raise ProviderError(KEY, "size_limit")
                            chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.TransportError:
                if attempt == 1:
                    raise ProviderError(KEY, "transport_failure") from None
        raise ProviderError(KEY, "transport_failure")

    async def read_catalog(self) -> CatalogSnapshot:
        if self._client is None or self._closed:
            raise ProviderError(KEY, "not_ready")
        async with self._lock:
            if self._closed:
                raise ProviderError(KEY, "closed")
            if self._snapshot is None:
                version = await self._get("version.json")
                documents = {
                    "version.json": version,
                    "idx.json": await self._get("idx.json"),
                    "constant.json": await self._get("song-const/crossversex.json"),
                }
                if version != await self._get("version.json"):
                    raise ProviderError(KEY, "version_changed_during_read")
                try:
                    version_data = _decode(version)
                    hashes = {
                        name: hashlib.sha256(raw).hexdigest()
                        for name, raw in documents.items()
                    }
                    manifest = {
                        "schema_version": 1,
                        "region": self.descriptor.scope.region,
                        "game_version": self.descriptor.scope.game_version,
                        "data_version": version_data["version"],
                        "created_at": datetime.now(UTC).isoformat(),
                        "hashes": hashes,
                        "sources": [
                            {
                                "file": name,
                                "sha256": digest,
                                "url": BASE
                                + (
                                    "song-const/crossversex.json"
                                    if name == "constant.json"
                                    else name
                                ),
                                "mode": "public_http_get",
                            }
                            for name, digest in hashes.items()
                        ],
                    }
                    snapshot = self.decoder.parse_documents(
                        json.dumps(manifest).encode(),
                        documents,
                        source_mode="public_http_get",
                    )
                    self._snapshot = replace(
                        snapshot,
                        provenance=replace(
                            snapshot.provenance,
                            provider_key=KEY,
                            observation_kind="public_http_get",
                        ),
                    )
                except ProviderError as exc:
                    raise ProviderError(KEY, exc.reason) from None
                except (ValueError, TypeError, KeyError, UnicodeError):
                    raise ProviderError(KEY, "invalid_public_catalog") from None
            return self._snapshot

    async def close(self):
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            if self._client is not None:
                await self._client.aclose()
            self._snapshot = None
