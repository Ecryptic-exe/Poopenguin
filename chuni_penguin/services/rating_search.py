"""Project C's BM25 index over one explicitly selected, pinned source catalog."""

from dataclasses import dataclass
from typing import Any

from chuni_penguin.providers.models import (
    CatalogRequest,
    CatalogSnapshot,
    ExternalChartRef,
    ExternalSongRef,
)
from chuni_penguin.providers.registry import ProviderRegistry
from chuni_penguin.services.rating_runtime import RatingRuntime, RatingUnavailable

DIFFICULTIES = ("BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA")
DIFF_CODES = dict(zip(("BAS", "ADV", "EXP", "MAS", "ULT"), DIFFICULTIES, strict=True))


@dataclass(frozen=True)
class RatingSong:
    ref: ExternalSongRef
    title: str
    difficulties: tuple[str, ...]

    @property
    def song_id(self) -> str:
        # Compatibility with the original C Catalog/Song interface; namespace
        # remains in ref and the enclosing pinned catalog, never inferred from ID.
        return self.ref.id


class RatingSearchService:
    def __init__(self, runtime: RatingRuntime, snapshot: CatalogSnapshot):
        self.runtime, self.snapshot = runtime, snapshot
        rows, refs = [], {}
        for song in snapshot.songs:
            if (
                song.ref in refs
                or song.ref.namespace != snapshot.provenance.source_namespace
                or any(chart.ref.song != song.ref for chart in song.charts)
            ):
                msg = "Conflicting source song identity."
                raise RatingUnavailable(msg)
            refs[song.ref] = song
            difficulties = tuple(
                name
                for code, name in DIFF_CODES.items()
                if any(
                    chart.ref.difficulty == code and chart.ref.variant == "default"
                    for chart in song.charts
                )
            )
            if not difficulties:
                continue
            if (
                not song.ref.id.isascii()
                or not song.ref.id.isdecimal()
                or len(song.ref.id) > 100
            ):
                msg = "This engine requires numeric source ID strings."
                raise RatingUnavailable(msg)
            rows.append(
                {
                    "song_id": song.ref.id,
                    "title": song.title,
                    "difficulties": list(difficulties),
                }
            )
        self.index = runtime.catalog.Catalog({"schema_version": 1, "songs": rows})
        self.songs = {
            row["song_id"]: RatingSong(
                ExternalSongRef(snapshot.provenance.source_namespace, row["song_id"]),
                row["title"],
                tuple(row["difficulties"]),
            )
            for row in rows
        }
        self._refs = refs

    @classmethod
    async def start(cls, runtime: RatingRuntime, providers: ProviderRegistry, key: str):
        descriptor = next(
            (
                descriptor
                for descriptor in providers.descriptors
                if descriptor.key == key
            ),
            None,
        )
        if descriptor is None or key not in {"chunilib_snapshot", "chunilib_public"}:
            msg = "Select a verified Chunilib catalog provider for rating search."
            raise RatingUnavailable(msg)
        snapshot = await providers.read_catalog(
            CatalogRequest(descriptor.scope, provider_key=key)
        )
        return cls(runtime, snapshot)

    def _song(self, value: Any) -> RatingSong:
        return self.songs[value.song_id]

    def exact_matches(
        self, query: str, difficulty: str | None = None
    ) -> list[RatingSong]:
        return [
            self._song(song) for song in self.index.exact_matches(query, difficulty)
        ]

    def search(
        self, query: str, difficulty: str | None = None, limit: int = 25
    ) -> list[dict[str, Any]]:
        return [
            {**row, "song": self._song(row["song"])}
            for row in self.index.search(query, difficulty, min(25, max(1, limit)))
        ]

    def chart_ref(self, song: RatingSong, difficulty: str) -> ExternalChartRef:
        code = next(
            (code for code, name in DIFF_CODES.items() if name == difficulty), None
        )
        source = self._refs.get(song.ref)
        if source is None or self.songs.get(song.song_id) != song or code is None:
            msg = "The selected source song is unavailable."
            raise RatingUnavailable(msg)
        matches = [
            chart.ref
            for chart in source.charts
            if chart.ref.difficulty == code and chart.ref.variant == "default"
        ]
        if len(matches) != 1:
            msg = "The selected source chart is missing or ambiguous."
            raise RatingUnavailable(msg)
        return matches[0]
