"""Offline C ChartService adapter; reviewed snapshots are the only input."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

from chuni_penguin.repositories.rating_snapshots import (
    RatingSnapshotRepository,
    safe_association_error,
)
from chuni_penguin.services.mapping_review import read_json
from chuni_penguin.services.rating_runtime import RatingUnavailable
from chuni_penguin.services.rating_search import RatingSearchService, RatingSong


class RatingAnalysisService:
    def __init__(
        self,
        search: RatingSearchService,
        repository: RatingSnapshotRepository,
        cache_dir: Path,
        *,
        options: dict[str, Any] | None = None,
    ):
        self.search, self.repository = search, repository
        self.runtime = search.runtime
        config, api = self.runtime.module("config"), self.runtime.module("service")
        self.error_type = api.ChartError
        # Never call Settings.load/ensure_catalog, construct RatingClient or
        # register_commands. The original worker is always offline.
        self.settings = config.Settings(
            root=self.runtime.root,
            token="",
            offline=True,
            cache_dir=cache_dir / repository.digest,
            **(options or {}),
        )
        self.legacy = api.ChartService(self.settings)
        self._closed = False

    async def get_chart(
        self, song: RatingSong, difficulty: str, *, detailed: bool = False
    ) -> dict[str, Any]:
        if self._closed:
            msg = "分析服務已關閉。"
            raise self.error_type(msg)
        try:
            self.runtime.assert_unchanged()
            ref = self.search.chart_ref(song, difficulty)
            entry = self.repository.get(ref)
            seed = self.repository.materialize(
                entry,
                self.settings.cache / "approved_sources",
                title=song.title,
                difficulty=difficulty,
            )
        except RatingUnavailable as exc:
            raise self.error_type(str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - external data boundary, no raw paths
            raise self.error_type(str(safe_association_error(exc))) from None
        legacy_song = self.runtime.catalog.Song(
            song.song_id, song.title, song.difficulties
        )
        # Reuse the original cross-view cache, jobs, queue, TTL and cancellation.
        existing = self.legacy.read_snapshot(legacy_song, difficulty)
        result = await self.legacy.get_chart(
            legacy_song, difficulty, None if existing else seed, detailed=detailed
        )
        statistics = read_json(seed / "manifest.json")
        return {
            **result,
            "catalog_source": asdict(self.search.snapshot.provenance),
            "source_chart_ref": asdict(ref),
            "analysis_review": asdict(entry.evidence),
            "statistics_namespace": "chunirec_db",
            "statistics_region": None,
            "statistics_game_version": None,
            "statistics_observed_at": statistics.get("completed_at"),
            "statistics_generated_at": statistics.get("statistics_generated_at"),
            "statistics_manifest_sha256": dict(entry.hashes)["manifest.json"],
        }

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self.legacy.close()
