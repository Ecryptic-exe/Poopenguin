"""B annotations and canonical-ID public statistics rendered by C's worker."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

from chuni_penguin.services.distribution_source import (
    DistributionUnavailable,
    collect_source,
    source_id,
    validate_snapshot,
)
from chuni_penguin.services.rating_runtime import RatingRuntime

ROOT = Path(__file__).resolve().parents[2]
DIFFICULTIES = {
    "BAS": "BASIC",
    "ADV": "ADVANCED",
    "EXP": "EXPERT",
    "MAS": "MASTER",
    "ULT": "ULTIMA",
}


class Phase1Charts:
    def __init__(
        self,
        root=ROOT,
        *,
        annotations=None,
        legacy_annotations=False,
        allow_network=False,
    ):
        self.root = root
        self.issues = []
        self.annotations = annotations
        # Archived bindings remain available only for explicit offline review tools/tests.
        # Production never falls back to JSON if the database has no annotations.
        self.manual = (
            self._read("snapshots/manual.json", {"charts": []})
            if legacy_annotations
            else {"charts": []}
        )
        if not isinstance(self.manual.get("charts"), list) or any(
            not isinstance(row, dict) or not isinstance(row.get("tags", []), list)
            for row in self.manual.get("charts", [])
        ):
            self.issues.append("manual source: invalid schema")
            self.manual = {"charts": []}
        self.manual_digest = (
            hashlib.sha256((root / "snapshots/manual.json").read_bytes()).hexdigest()
            if legacy_annotations and (root / "snapshots/manual.json").exists()
            else None
        )
        self.bindings = (
            self._read("snapshots/manual_bindings.json", [])
            if legacy_annotations
            else []
        )
        self.semantics = self._read("snapshots/semantic_bindings.json", [])
        self.statistics = self._read("snapshots/statistics/index.json", [])
        self.bindings = [row for row in self.bindings if isinstance(row, dict)]
        self.semantics = [row for row in self.semantics if isinstance(row, dict)]
        self.statistics = [row for row in self.statistics if isinstance(row, dict)]
        self.runtime = None
        self.worker = None
        self.allow_network = allow_network
        self._inflight = {}
        self._failures = {}
        self._semaphore = asyncio.Semaphore(1)
        self._closed = False

    def _read(self, relative, default):
        try:
            value = json.loads((self.root / relative).read_text(encoding="utf-8"))
            if not isinstance(value, type(default)):
                raise ValueError
            return value
        except (OSError, ValueError):
            self.issues.append(f"{relative}: unavailable")
            return default

    def manual_for(self, chart):
        rows = []
        for binding in self.bindings:
            if (
                binding.get("song_id") != chart.song_id
                or binding.get("difficulty") != chart.difficulty
            ):
                continue
            if (
                binding.get("source_sha256") != self.manual_digest
                or not binding.get("evidence")
                or not binding.get("reviewer")
            ):
                continue
            idx = binding.get("row")
            if type(idx) is int and 0 <= idx < len(self.manual["charts"]):
                row = self.manual["charts"][idx]
                expected = DIFFICULTIES.get(chart.difficulty, "").casefold()
                if str(row.get("difficulty", "")).casefold() == expected:
                    rows.append((row, binding))
        return rows

    def semantic_for(self, chart):
        return [
            row
            for row in self.semantics
            if row.get("song_id") == chart.song_id
            and row.get("difficulty") == chart.difficulty
            and row.get("evidence")
            and row.get("source")
            and row.get("reviewer")
        ]

    async def tag_matches(self, tag):
        if self.annotations is None:
            return []
        return [(row["source_row"], row) for row in await self.annotations.search(tag)]

    async def annotations_for(self, chart):
        return (
            [] if self.annotations is None else await self.annotations.for_chart(chart)
        )

    async def tagged_chart_ids(self, tag):
        return (
            set() if self.annotations is None else await self.annotations.chart_ids(tag)
        )

    def tags_for(self, chart):
        return {
            str(tag) for row, _ in self.manual_for(chart) for tag in row.get("tags", [])
        }

    def statistic_for(self, chart):
        rows = [
            row
            for row in self.statistics
            if row.get("song_id") == chart.song_id
            and row.get("difficulty") == chart.difficulty
            and row.get("chunirec_id") == chart.song.chunirec_id
        ]
        return rows[0] if len(rows) == 1 else None

    async def analysis(self, chart):
        if self._closed:
            raise DistributionUnavailable("統計服務已關閉，請重新開啟歌曲介面。")
        native_id = source_id(chart)
        key = (chart.song_id, chart.difficulty, native_id)
        failed = self._failures.get(key)
        if failed and time.monotonic() < failed[0]:
            raise DistributionUnavailable(failed[1])
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= 8:
                raise DistributionUnavailable("成績分佈的處理佇列已滿，請稍後重試。")
            task = asyncio.create_task(self._analyze_one(chart, native_id))
            self._inflight[key] = task

            def cleanup(completed):
                self._inflight.pop(key, None)
                if not completed.cancelled():
                    error = completed.exception()
                    if isinstance(error, DistributionUnavailable):
                        if len(self._failures) >= 128:
                            self._failures.pop(next(iter(self._failures)))
                        self._failures[key] = (time.monotonic() + 30, str(error))

            task.add_done_callback(cleanup)
        return await asyncio.shield(task)

    def _bundled_snapshot(self, chart):
        entry = self.statistic_for(chart)
        if entry is None:
            return None
        path = (self.root / "snapshots/statistics" / entry["folder"]).resolve()
        if not path.is_relative_to((self.root / "snapshots/statistics").resolve()):
            raise ValueError("Invalid statistics path")
        for name, digest in entry["hashes"].items():
            candidate = (path / name).resolve()
            if (
                not candidate.is_relative_to(path)
                or hashlib.sha256(candidate.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("Statistics snapshot changed; review required")
        return path

    async def _analyze_one(self, chart, native_id):
        async with self._semaphore:
            return await self._produce_analysis(chart, native_id)

    async def _produce_analysis(self, chart, native_id):
        if self.worker is None:
            self.runtime = RatingRuntime(self.root / "vendor/rating_bot")
            settings = self.runtime.module("config").Settings(
                root=self.runtime.root,
                token="",
                offline=True,
                cache_dir=self.root / "data/cache/statistics",
                max_workers=1,
                queue_limit=8,
            )
            self.worker = self.runtime.module("service").ChartService(settings)
        self.runtime.assert_unchanged()
        difficulty = DIFFICULTIES[chart.difficulty]
        song = self.runtime.catalog.Song(
            str(chart.song_id), chart.song.title, (difficulty,)
        )
        existing = self.worker.read_snapshot(song, difficulty)
        snapshot = existing["path"] if existing else None
        kind = "cache"
        try:
            if snapshot is not None:
                validate_snapshot(
                    snapshot,
                    title=song.title,
                    difficulty=difficulty,
                    native_id=native_id,
                )
        except (OSError, ValueError, KeyError, DistributionUnavailable):
            snapshot = None
        seed = None
        if snapshot is None:
            if self.allow_network:
                try:
                    snapshot = await collect_source(
                        self.root,
                        title=song.title,
                        difficulty=difficulty,
                        native_id=native_id,
                    )
                    kind = "live"
                except (DistributionUnavailable, TimeoutError):
                    snapshot = self._bundled_snapshot(chart)
                    kind = "bundled"
                    if snapshot is None:
                        raise DistributionUnavailable(
                            "Chunirec 統計暫時無法取得，請稍後重新選擇「成績分佈」重試。"
                        ) from None
            else:
                snapshot = self._bundled_snapshot(chart)
                kind = "bundled"
            if snapshot is None:
                raise DistributionUnavailable("離線模式沒有這張譜面的統計快照。")
            seed = snapshot
        origin = validate_snapshot(
            snapshot, title=song.title, difficulty=difficulty, native_id=native_id
        )
        bundled = self.statistic_for(chart)
        if kind == "cache" and bundled and origin["hashes"] == bundled["hashes"]:
            kind = "bundled"
        origin["source_kind"] = kind
        try:
            result = await self.worker.get_chart(song, difficulty, seed, detailed=True)
        except self.runtime.module("service").ChartError as error:
            if "門檻" in str(error) or "統計" in str(error):
                message = (
                    "此難度的有效統計樣本不足（C 模型每組至少 100 人），請切換難度。"
                )
            elif "公式模型" in str(error):
                message = "此難度的定數不在 C 模型支援範圍，請切換難度。"
            else:
                message = "成績分佈產圖失敗，請稍後重新選擇此功能重試。"
            raise DistributionUnavailable(message) from error
        image = Path(result["image"]).resolve()
        if not image.is_relative_to((self.root / "data/cache/statistics").resolve()):
            raise ValueError("Unexpected analysis output")
        return origin, await asyncio.to_thread(image.read_bytes)

    async def close(self):
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.worker is not None:
            await self.worker.close()
