"""Update catalog constants from TSAIBEE without replacing canonical IDs."""

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from chuni_penguin.database import Chart, Song
from chuni_penguin.providers.chunilib_public import ChunilibPublicProvider
from chuni_penguin.providers.chunilib_snapshot import (
    BASE,
    ChunilibSnapshotProvider,
    _decode,
    _read,
)
from chuni_penguin.providers.registry import ProviderError

from .seeds import SEEDS_DIR


DEFAULT_CATALOG_DIR = Path(__file__).resolve().parent.parent / "assets/tsaibee/catalog"


def read_catalog_files(directory: Path, game_version: str):
    """Accept browser-downloaded JSONs using the existing snapshot validator."""
    provider = ChunilibSnapshotProvider(directory, game_version)
    constants = directory / f"{game_version}.json"
    if not constants.is_file():
        constants = directory / "constant.json"
    documents = {
        "idx.json": _read(directory / "idx.json"),
        "constant.json": _read(constants),
        "version.json": _read(directory / "version.json"),
    }
    version = _decode(documents["version.json"])
    if not isinstance(version, dict) or not isinstance(version.get("version"), str):
        raise ValueError("version.json must contain a version string.")
    hashes = {
        name: hashlib.sha256(data).hexdigest() for name, data in documents.items()
    }
    manifest = {
        "schema_version": 1,
        "region": provider.descriptor.scope.region,
        "game_version": game_version,
        "data_version": version["version"],
        "created_at": datetime.now(UTC).isoformat(),
        "hashes": hashes,
        "sources": [
            {
                "file": name,
                "sha256": digest,
                "url": BASE
                + (
                    f"song-const/{game_version}.json"
                    if name == "constant.json"
                    else name
                ),
                "mode": "offline_import",
            }
            for name, digest in hashes.items()
        ],
    }
    return provider.parse_documents(json.dumps(manifest).encode(), documents)


def _level(constant: Decimal) -> str:
    whole = int(constant)
    return str(whole) + (
        "+" if whole >= 7 and constant - whole >= Decimal("0.5") else ""
    )


async def sync_catalog(engine, snapshot, *, apply=False, seed_path=None):
    """Add/update matched catalog rows in one transaction; never delete rows.

    TSAIBEE's ID must also have the exact local title. New entries additionally
    require seed metadata: idx.json does not identify every canonical field.
    Existing chart IDs, note counts, aliases and all player tables are retained.
    """
    if not snapshot.songs:
        raise ValueError("The source catalog is empty; no changes were made.")
    seeds = _decode(_read(seed_path or SEEDS_DIR / "songs.json"))
    if not isinstance(seeds, list):
        raise ValueError("Invalid local song seeds.")
    seed_ids = Counter(row["id"] for row in seeds)
    by_seed = {row["id"]: row for row in seeds if seed_ids[row["id"]] == 1}
    report = {
        "source": "https://chuni.tsaibee.org/data/",
        "game_version": snapshot.provenance.scope.game_version,
        "region": snapshot.provenance.scope.region,
        "data_version": snapshot.provenance.data_version,
        "revision": snapshot.provenance.revision,
        "source_issues": [
            {"reason": issue.reason, "pointer": issue.source_pointer}
            for issue in snapshot.issues
        ],
        "applied": apply,
        "added_songs": 0,
        "added_charts": 0,
        "updated_charts": 0,
        "unchanged_charts": 0,
        "skipped": [],
        "changes": [],
    }
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        async with session.begin():
            rows = await session.scalars(
                select(Song).options(selectinload(Song.charts))
            )
            local = {song.id: song for song in rows}
            for source in snapshot.songs:
                if (
                    source.ref.namespace != "chunilib"
                    or not source.ref.id.isascii()
                    or not source.ref.id.isdecimal()
                ):
                    raise ValueError("Unexpected source song identity.")
                song_id = int(source.ref.id)
                song = local.get(song_id)
                seed = by_seed.get(song_id)
                metadata = song if song is not None else seed
                title = (
                    song.title
                    if song is not None
                    else seed.get("title")
                    if seed
                    else None
                )
                if metadata is None or title != source.title:
                    report["skipped"].append(
                        {
                            "song_id": song_id,
                            "title": source.title,
                            "reason": "missing_local_identity"
                            if metadata is None
                            else "title_mismatch",
                        }
                    )
                    continue
                constants = [
                    chart for chart in source.charts if chart.constant is not None
                ]
                if not constants:
                    continue
                if song is None:
                    values = {
                        column.name: seed[column.name]
                        for column in Song.__table__.columns
                        if column.name in seed
                    }
                    required = {
                        "id",
                        "title",
                        "artist",
                        "genre",
                        "chunithm_catcode",
                        "version",
                        "available",
                        "removed",
                    }
                    if not required <= values.keys():
                        report["skipped"].append(
                            {"song_id": song_id, "reason": "incomplete_seed_metadata"}
                        )
                        continue
                    song = Song(**values)
                    song.charts = []
                    local[song_id] = song
                    session.add(song)
                    report["added_songs"] += 1
                charts = {chart.difficulty: chart for chart in song.charts}
                for source_chart in constants:
                    difficulty = source_chart.ref.difficulty
                    chart = charts.get(difficulty)
                    if chart is None:
                        candidates = [
                            row
                            for row in (seed or {}).get("charts", [])
                            if row["difficulty"] == difficulty
                        ]
                        if (
                            seed is None
                            or seed["title"] != source.title
                            or len(candidates) != 1
                        ):
                            report["skipped"].append(
                                {
                                    "song_id": song_id,
                                    "difficulty": difficulty,
                                    "reason": "missing_chart_metadata",
                                }
                            )
                            continue
                        values = {
                            column.name: candidates[0][column.name]
                            for column in Chart.__table__.columns
                            if column.name in candidates[0] and column.name != "id"
                        }
                        chart = Chart(song_id=song_id, **values)
                        song.charts.append(chart)
                        charts[difficulty] = chart
                        report["added_charts"] += 1
                    constant = float(source_chart.constant)
                    level = source_chart.level or _level(source_chart.constant)
                    if chart.const == constant and chart.level == level:
                        report["unchanged_charts"] += 1
                        continue
                    report["changes"].append(
                        {
                            "song_id": song_id,
                            "difficulty": difficulty,
                            "old_constant": chart.const,
                            "constant": constant,
                            "old_level": chart.level,
                            "level": level,
                        }
                    )
                    chart.const, chart.level = constant, level
                    report["updated_charts"] += 1
            if not apply:
                await session.rollback()
    return report


def backup_sqlite(engine) -> Path:
    """Make a consistent local backup, including WAL, before an explicit apply."""
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database:
        raise ValueError("Automatic catalog backup requires a local SQLite database.")
    path = Path(engine.url.database).resolve()
    if not path.is_file():
        raise ValueError("The configured database does not exist.")
    directory = path.parent / "catalog-backups"
    directory.mkdir(exist_ok=True)
    backup = directory / (
        "before-tsaibee-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3"
    )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
    return backup


async def update_chunilib(
    logger, engine, *, game_version, catalog_dir=None, apply=False, report_path=None
):
    try:
        if catalog_dir is not None:
            snapshot = read_catalog_files(catalog_dir, game_version)
        else:
            provider = ChunilibPublicProvider(game_version)
            try:
                await provider.start()
                snapshot = await provider.read_catalog()
            finally:
                await provider.close()
        backup = backup_sqlite(engine) if apply else None
        report = await sync_catalog(engine, snapshot, apply=apply)
        if backup is not None:
            report["backup"] = str(backup)
    except (ProviderError, ValueError, OSError, SQLAlchemyError) as exc:
        # The public catalog never uses cookies or authentication. Keep diagnostics
        # limited to a safe error category, not arbitrary HTTP response contents.
        reason = exc.reason if isinstance(exc, ProviderError) else type(exc).__name__
        logger.error(
            "TSAIBEE catalog update failed; no catalog update committed", reason=reason
        )
        if isinstance(exc, ProviderError) and exc.reason in (
            "http_403",
            "unexpected_content_type",
        ):
            logger.error(
                "Download idx.json, version.json and the selected song-const JSON in a browser, then use --catalog-dir"
            )
        return False
    if report_path is not None:
        try:
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.warning(
                "TSAIBEE catalog operation finished but its report could not be saved",
                applied=apply,
            )
    logger.info(
        "TSAIBEE catalog update finished",
        applied=apply,
        game_version=game_version,
        added_songs=report["added_songs"],
        added_charts=report["added_charts"],
        updated_charts=report["updated_charts"],
        skipped=len(report["skipped"]),
    )
    return True
