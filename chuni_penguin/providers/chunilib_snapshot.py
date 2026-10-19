"""Read a verified, pinned Chunilib catalogue snapshot; no network or uploads."""

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Never

from .models import (
    CatalogChart,
    CatalogIssue,
    CatalogScope,
    CatalogSnapshot,
    CatalogSong,
    ExternalChartRef,
    ExternalSongRef,
    Provenance,
    ProviderDescriptor,
    SourceFile,
)
from .registry import ProviderError

VERSIONS = {"crossversex": "international", "mate": "japan"}
DIFFICULTIES = ("BAS", "ADV", "EXP", "MAS", "ULT")
BASE = "https://chuni.tsaibee.org/data/"
KEY = "chunilib_snapshot"


def _invalid(reason: str) -> Never:
    raise ProviderError(KEY, reason)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("duplicate_json_key")
        result[key] = value
    return result


def _read(path: Path, maximum: int = 16 * 1024 * 1024) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        _invalid("size_limit")
    return data


def _decode(data: bytes) -> Any:
    return json.loads(
        data.decode("utf-8-sig"),
        object_pairs_hook=_unique_object,
        parse_constant=lambda _: _invalid("nonfinite_json_number"),
    )


def _constant(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and 0 < number < 20 else None


class ChunilibSnapshotProvider:
    def __init__(self, path: Path, game_version: str = "crossversex"):
        self.path = path
        if game_version not in VERSIONS:
            _invalid("unsupported_game_version")
        self.descriptor = ProviderDescriptor(
            "chunilib_snapshot",
            "chunilib",
            CatalogScope(VERSIONS[game_version], game_version),
            (
                "Observed idx.json + song-const/<game_version>.json + version.json",
                "Snapshot manifest schema_version=1 with SHA-256 hashes",
            ),
        )
        self._snapshot: CatalogSnapshot | None = None
        self._closed = False

    def _load(self) -> CatalogSnapshot:
        return self.parse_documents(
            _read(self.path / "manifest.json", 512 * 1024),
            {
                name: _read(self.path / name)
                for name in ("idx.json", "constant.json", "version.json")
            },
        )

    def parse_documents(
        self,
        manifest_data: bytes,
        documents: Mapping[str, bytes],
        *,
        source_mode: Literal["offline_import", "public_http_get"] = "offline_import",
    ) -> CatalogSnapshot:
        """Shared validation for saved files and bounded public HTTP responses."""
        manifest = _decode(manifest_data)
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("region") != self.descriptor.scope.region
            or manifest.get("game_version") != self.descriptor.scope.game_version
            or not isinstance(manifest.get("hashes"), dict)
            or not isinstance(manifest.get("sources"), list)
        ):
            _invalid("invalid_manifest")
        observed = datetime.fromisoformat(manifest["created_at"])
        if observed.utcoffset() is None:
            _invalid("naive_observation_time")
        decoded, files = {}, []
        names = {
            "idx.json": "idx.json",
            "constant.json": f"song-const/{self.descriptor.scope.game_version}.json",
            "version.json": "version.json",
        }
        for name, endpoint in names.items():
            expected = manifest["hashes"].get(name)
            if not isinstance(expected, str) or not re.fullmatch(
                "[0-9a-f]{64}", expected
            ):
                _invalid("invalid_hash")
            data = documents[name]
            if hashlib.sha256(data).hexdigest() != expected:
                _invalid("hash_mismatch")
            sources = [
                s
                for s in manifest["sources"]
                if isinstance(s, dict) and s.get("file") == name
            ]
            if len(sources) != 1:
                _invalid("invalid_source_record")
            source = sources[0]
            if (
                source.get("sha256") != expected
                or source.get("url") != BASE + endpoint
                or source.get("mode") != source_mode
            ):
                _invalid("unsupported_source_record")
            files.append(SourceFile(name, expected, source["url"], source["mode"]))
            decoded[name] = _decode(data)
        version = decoded["version.json"]
        if (
            not isinstance(version, dict)
            or not isinstance(version.get("version"), str)
            or not version["version"]
            or version["version"] != manifest.get("data_version")
        ):
            _invalid("version_mismatch")
        index, constants = decoded["idx.json"], decoded["constant.json"]
        if not isinstance(index, list) or not isinstance(constants, dict):
            _invalid("unexpected_catalog_schema")
        for row in index:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"].isascii()
                or not row["id"].isdigit()
                or not isinstance(row.get("title"), str)
                or not row["title"]
            ):
                _invalid("invalid_index_row")
        ids, titles = (
            Counter(r["id"] for r in index),
            Counter(r["title"] for r in index),
        )
        songs, issues = [], []
        for position, row in enumerate(index):
            song_id, title = row["id"], row["title"]
            pointer = f"idx.json#/{position}"
            if ids[song_id] != 1:
                issues.append(CatalogIssue("duplicate_song_id", pointer, song_id))
                continue
            constant_row = constants.get(title)
            if titles[title] != 1:
                issues.append(CatalogIssue("ambiguous_title_join", pointer, song_id))
                constant_row = None
            elif not isinstance(constant_row, dict):
                issues.append(
                    CatalogIssue("missing_or_invalid_constants", pointer, song_id)
                )
                constant_row = None
            ref = ExternalSongRef("chunilib", song_id)
            charts = []
            for difficulty in DIFFICULTIES:
                raw = constant_row.get(difficulty) if constant_row is not None else None
                number = _constant(raw)
                # Observed metadata or a published field establishes this chart;
                # missing constants alone never create all five difficulties.
                exists = (
                    number is not None
                    or bool(row.get(difficulty.lower()))
                    or bool(row.get(difficulty.lower() + "_chart"))
                )
                if not exists and raw is None:
                    continue
                if number is None:
                    issues.append(
                        CatalogIssue("unknown_constant", pointer, song_id, difficulty)
                    )
                charts.append(CatalogChart(ExternalChartRef(ref, difficulty), number))
            artist = row.get("artist")
            songs.append(
                CatalogSong(
                    ref,
                    title,
                    artist if isinstance(artist, str) else None,
                    tuple(charts),
                )
            )
        for title in sorted(set(constants) - set(titles)):
            escaped = title.replace("~", "~0").replace("/", "~1")
            issues.append(
                CatalogIssue("constant_without_index", f"constant.json#/{escaped}")
            )
        revision = hashlib.sha256(
            json.dumps(
                [
                    self.descriptor.scope.region,
                    self.descriptor.scope.game_version,
                    [[f.file, f.sha256] for f in files],
                ],
            ).encode()
        ).hexdigest()
        return CatalogSnapshot(
            tuple(songs),
            Provenance(
                self.descriptor.key,
                "chunilib",
                self.descriptor.scope,
                version["version"],
                revision,
                observed,
                "local_snapshot_import",
                files=tuple(files),
            ),
            tuple(issues),
        )

    async def start(self):
        if self._closed:
            _invalid("closed")
        if self._snapshot is None:
            self._snapshot = await asyncio.to_thread(self._load)

    async def read_catalog(self) -> CatalogSnapshot:
        if self._snapshot is None or self._closed:
            _invalid("not_ready")
        assert self._snapshot is not None
        return self._snapshot

    async def close(self):
        self._closed = True
        self._snapshot = None
