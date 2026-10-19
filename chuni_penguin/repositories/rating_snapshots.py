"""Explicit reviewed analysis associations, separate from canonical ID mappings."""

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from chuni_penguin.identities import MappingError, ReviewEvidence, snapshot_identity
from chuni_penguin.providers.models import (
    CatalogSnapshot,
    ExternalChartRef,
    ExternalSongRef,
)
from chuni_penguin.services.mapping_review import object_keys, read_json
from chuni_penguin.services.rating_runtime import RatingUnavailable

SOURCE_FILES = (
    "manifest.json",
    "selected_stats.json",
    "embedded_stats.txt",
    "rating_score_table.csv",
    "rating_rank_counts.csv",
    "source.html",
    "music.js",
)


@dataclass(frozen=True)
class AnalysisSnapshot:
    ref: ExternalChartRef
    path: Path
    hashes: tuple[tuple[str, str], ...]
    evidence: ReviewEvidence


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = "Invalid analysis association."
        raise RatingUnavailable(msg)
    return value


class RatingSnapshotRepository:
    def __init__(self, manifest: Path, source_root: Path, catalog: CatalogSnapshot):
        self.root = source_root.resolve(strict=True)
        raw = read_json(manifest)
        obj = object_keys(raw, {"schema_version", "catalog", "charts"})
        origin = catalog.provenance
        source_id, payload = snapshot_identity(catalog)
        expected = {
            "provider_key": origin.provider_key,
            "namespace": origin.source_namespace,
            "region": origin.scope.region,
            "game_version": origin.scope.game_version,
            "revision": origin.revision,
            "source_id": source_id,
            "payload_sha256": payload,
        }
        if (
            type(obj["schema_version"]) is not int
            or obj["schema_version"] != 1
            or obj["catalog"] != expected
            or not isinstance(obj["charts"], list)
            or len(obj["charts"]) > 1000
        ):
            msg = "Analysis associations do not match the selected catalog."
            raise RatingUnavailable(msg)
        self.digest = hashlib.sha256(
            json.dumps(
                obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        self.entries: dict[ExternalChartRef, AnalysisSnapshot] = {}
        catalog_refs = {chart.ref for song in catalog.songs for chart in song.charts}
        for raw_entry in obj["charts"]:
            entry = object_keys(raw_entry, {"ref", "snapshot", "hashes", "evidence"})
            ref_obj = object_keys(
                entry["ref"], {"song_id", "difficulty", "variant", "chart_id"}
            )
            native = ref_obj["chart_id"]
            ref = ExternalChartRef(
                ExternalSongRef(origin.source_namespace, _string(ref_obj["song_id"])),
                _string(ref_obj["difficulty"]),
                _string(ref_obj["variant"]),
                None if native is None else _string(native),
            )
            if ref not in catalog_refs or ref in self.entries:
                msg = "Unknown or duplicate analysis chart identity."
                raise RatingUnavailable(msg)
            relative = Path(_string(entry["snapshot"]))
            path = (self.root / relative).resolve(strict=True)
            if (
                relative.is_absolute()
                or not path.is_relative_to(self.root)
                or not path.is_dir()
            ):
                msg = "Analysis snapshot must stay within its configured source directory."
                raise RatingUnavailable(msg)
            hashes = object_keys(entry["hashes"], set(SOURCE_FILES))
            if any(
                not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value)
                for value in hashes.values()
            ):
                msg = "Analysis snapshot hashes are required."
                raise RatingUnavailable(msg)
            review = object_keys(
                entry["evidence"], {"actor", "reason", "reference", "sha256"}
            )
            evidence = ReviewEvidence(**review)
            evidence.validate()
            if evidence.sha256 != hashes["manifest.json"]:
                msg = "Review evidence must pin the statistics manifest."
                raise RatingUnavailable(msg)
            self.entries[ref] = AnalysisSnapshot(
                ref, path, tuple(sorted(hashes.items())), evidence
            )

    def get(self, ref: ExternalChartRef) -> AnalysisSnapshot:
        try:
            return self.entries[ref]
        except KeyError:
            msg = "This source chart has no reviewed statistics snapshot."
            raise RatingUnavailable(msg) from None

    def materialize(
        self, entry: AnalysisSnapshot, destination: Path, *, title: str, difficulty: str
    ) -> Path:
        """Copy only hash-verified fixed filenames to an owned, pinned seed folder."""
        key = hashlib.sha256(json.dumps(entry.hashes).encode()).hexdigest()
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / key
        with TemporaryDirectory(prefix="prepare-", dir=destination) as folder:
            staging = Path(folder) / "snapshot"
            staging.mkdir()
            for name, digest in entry.hashes:
                source = (entry.path / name).resolve(strict=True)
                if (
                    not source.is_relative_to(self.root)
                    or source.stat().st_size > 16 * 1024 * 1024
                ):
                    msg = "Invalid analysis snapshot file."
                    raise RatingUnavailable(msg)
                output = staging / name
                shutil.copyfile(source, output)
                if hashlib.sha256(output.read_bytes()).hexdigest() != digest:
                    msg = "Analysis snapshot changed after review."
                    raise RatingUnavailable(msg)
            manifest = read_json(staging / "manifest.json")
            if (
                manifest.get("status") != "complete"
                or manifest.get("title") != title
                or manifest.get("difficulty") != difficulty
            ):
                msg = "Statistics title/difficulty does not match the reviewed source chart."
                raise RatingUnavailable(msg)
            url = urlsplit(_string(manifest.get("source_url")))
            if (
                url.scheme != "https"
                or url.netloc != "db.chunirec.net"
                or not url.path.startswith("/music/")
            ):
                msg = "Unsupported statistics source."
                raise RatingUnavailable(msg)
            if target.exists():
                if any(
                    hashlib.sha256((target / name).read_bytes()).hexdigest() != digest
                    for name, digest in entry.hashes
                ):
                    msg = "Pinned statistics cache is inconsistent."
                    raise RatingUnavailable(msg)
            else:
                # Folder contents are fixed, validated and private to this service.
                # Publish the complete folder at once; failed preparation leaves
                # no partial cache that could break subsequent requests.
                staging.rename(target)
        return target


def safe_association_error(exc: Exception) -> RatingUnavailable:
    # Mapping parser errors are reason codes; discard storage paths/raw JSON.
    if isinstance(exc, (MappingError, OSError, ValueError, TypeError)):
        return RatingUnavailable("Analysis association or snapshot is unavailable.")
    return RatingUnavailable("Rating analysis is unavailable.")
