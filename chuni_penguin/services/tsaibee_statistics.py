"""Anonymous CHUNILIB song statistics; independent of player login sessions."""

import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from chuni_penguin.constants import ASSETS_DIR

API = "https://chuni-api.tsaibee.org/"
RANKS = ("MAX", "SSS+", "SSS", "SS+", "SS", "S")
DIFFICULTIES = {"EXP", "MAS", "ULT"}
MAX_BYTES = 4 * 1024 * 1024


class StatisticsUnavailable(Exception):
    pass


@dataclass(frozen=True)
class SongStatistics:
    players: int
    average: int
    counts: tuple[int, ...]
    aj: int | None = None
    fc: int | None = None
    best30: Decimal | None = None


@dataclass(frozen=True)
class StatisticsSource:
    rows: dict
    observed_at: str
    saved: bool = False
    stale: bool = False


@dataclass(frozen=True)
class ChartStatistics:
    overall: SongStatistics
    source: StatisticsSource
    bands: tuple[SongStatistics, ...] = ()
    band_source: StatisticsSource | None = None


def _row(values, *, bands=False):
    if not isinstance(values, list) or len(values) != (9 if bands else 11):
        raise ValueError("Unexpected statistics row")
    identity, players, average, *counts = values
    if (
        isinstance(identity, bool)
        or not isinstance(identity, (int, float))
        or not math.isfinite(identity)
    ):
        raise ValueError("Invalid statistics identity")
    if (
        type(players) is not int
        or players < 0
        or type(average) is not int
        or not 0 <= average <= 1_010_000
    ):
        raise ValueError("Invalid statistics sample")
    if any(type(value) is not int or not 0 <= value <= players for value in counts):
        raise ValueError("Invalid statistics counts")
    if counts[:6] != sorted(counts[:6]):
        raise ValueError("Statistics thresholds are not cumulative")
    if bands:
        bucket = Decimal(str(identity))
        if not Decimal("16.0") <= bucket <= Decimal("17.7") or bucket * 10 != int(
            bucket * 10
        ):
            raise ValueError("Invalid BEST30 band")
        return SongStatistics(players, average, tuple(counts), best30=bucket)
    if type(identity) is not int or identity <= 0 or counts[6] > counts[7]:
        raise ValueError("Invalid song identity or lamps")
    return SongStatistics(
        players, average, tuple(counts[:6]), aj=counts[6], fc=counts[7]
    )


def parse_statistics(raw: bytes, *, bands=False):
    data = json.loads(raw)
    if not isinstance(data, dict) or not data or not set(data) <= DIFFICULTIES:
        raise ValueError("Unexpected statistics response")
    result = {}
    for difficulty, rows in data.items():
        if bands:
            if not isinstance(rows, dict):
                raise ValueError("Unexpected statistics groups")
            for song_id, values in rows.items():
                if (
                    not isinstance(song_id, str)
                    or not song_id.isascii()
                    or not song_id.isdecimal()
                    or int(song_id) <= 0
                    or not isinstance(values, list)
                ):
                    raise ValueError("Invalid statistics song ID")
                parsed = tuple(_row(row, bands=True) for row in values)
                if len({row.best30 for row in parsed}) != len(parsed):
                    raise ValueError("Duplicate BEST30 band")
                result[difficulty, int(song_id)] = tuple(
                    sorted(parsed, key=lambda row: row.best30)
                )
        else:
            if not isinstance(rows, list):
                raise ValueError("Unexpected song statistics")
            for values in rows:
                parsed = _row(values)
                key = difficulty, values[0]
                if key in result:
                    raise ValueError("Duplicate song statistics")
                result[key] = parsed
    return result


class TsaibeeStatistics:
    def __init__(self, *, transport=None, fallback=None, ttl=3600, offline=False):
        self.transport = transport
        self.fallback = (
            Path(fallback) if fallback is not None else ASSETS_DIR / "tsaibee"
        )
        self.ttl = ttl
        self.offline = offline
        self._cache = {}
        self._expires = {}
        self._lock = asyncio.Lock()

    def _saved(self, endpoint):
        manifest = json.loads(
            (self.fallback / "statistics.json").read_text(encoding="utf-8")
        )
        name = endpoint + ".json"
        raw = (self.fallback / name).read_bytes()
        if (
            len(raw) > MAX_BYTES
            or hashlib.sha256(raw).hexdigest() != manifest["hashes"][name]
        ):
            raise ValueError("Invalid saved statistics")
        return StatisticsSource(
            parse_statistics(raw, bands=endpoint == "lib-stat-songr"),
            manifest["captured_on"],
            saved=True,
            stale=True,
        )

    async def _read(self, endpoint, *, difficulty=None):
        key = endpoint, difficulty
        now = time.monotonic()
        if now < self._expires.get(key, 0):
            return self._cache[key]
        try:
            if self.offline:
                raise ValueError("Offline statistics requested")
            async with httpx.AsyncClient(
                transport=self.transport, timeout=10, follow_redirects=False
            ) as client:
                payload = {"diff": difficulty} if difficulty else {}
                async with client.stream(
                    "POST",
                    API + endpoint,
                    json=payload,
                    headers={"Accept": "application/json"},
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        != "application/json"
                    ):
                        raise ValueError("Statistics request failed")
                    chunks, total = [], 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError("Statistics size limit")
                        chunks.append(chunk)
                parsed = await asyncio.to_thread(
                    parse_statistics,
                    b"".join(chunks),
                    bands=endpoint == "lib-stat-songr",
                )
                source = StatisticsSource(parsed, datetime.now(UTC).isoformat())
        except (httpx.HTTPError, ValueError):
            source = self._cache.get(key)
            if source is not None:
                source = replace(source, stale=True)
            else:
                try:
                    source = await asyncio.to_thread(self._saved, endpoint)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise StatisticsUnavailable(
                        "No public statistics available"
                    ) from exc
        self._cache[key] = source
        # Retry unavailable sources after five minutes, rather than once per user.
        self._expires[key] = now + (min(self.ttl, 300) if source.stale else self.ttl)
        return source

    async def chart(self, song_id: int, difficulty: str) -> ChartStatistics:
        if difficulty not in DIFFICULTIES or song_id <= 0:
            raise StatisticsUnavailable("Unsupported chart")
        async with self._lock:
            source = await self._read("lib-stat-song")
            overall = source.rows.get((difficulty, song_id))
            if overall is None or not overall.players:
                raise StatisticsUnavailable("No sample for this chart")
            bands, band_source = (), None
            if difficulty in {"MAS", "ULT"}:
                await asyncio.sleep(0.25)
                try:
                    band_source = await self._read(
                        "lib-stat-songr", difficulty=difficulty
                    )
                    bands = band_source.rows.get((difficulty, song_id), ())
                except StatisticsUnavailable:
                    pass  # Overall statistics remain useful without cohort data.
            return ChartStatistics(overall, source, bands, band_source)


def statistics_for(bot):
    service = getattr(bot, "tsaibee_statistics", None)
    if service is None:
        service = bot.tsaibee_statistics = TsaibeeStatistics(
            offline=getattr(bot, "offline", False)
        )
    return service
