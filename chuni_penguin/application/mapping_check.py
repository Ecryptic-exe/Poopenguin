"""Read-only identity candidate report against an explicitly supplied SQLite copy."""

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chuni_penguin.application.offline import deny_network
from chuni_penguin.providers.chunilib_snapshot import ChunilibSnapshotProvider
from chuni_penguin.providers.local_catalog import LocalCatalogProvider
from chuni_penguin.providers.models import CatalogRequest
from chuni_penguin.providers.registry import ProviderRegistry
from chuni_penguin.services.identity_candidates import plan_candidates


@dataclass
class _ReadOnlyDatabase:
    read_sessionmaker: async_sessionmaker[AsyncSession]


async def run_check(
    snapshot: Path, catalog_db: Path, game_version: str = "crossversex"
):
    if not catalog_db.is_file():
        raise FileNotFoundError(catalog_db)
    url = URL.create(
        "sqlite+aiosqlite",
        database=catalog_db.resolve().as_uri(),
        query={"mode": "ro", "uri": "true"},
    )
    engine = create_async_engine(url, hide_parameters=True)
    registry = ProviderRegistry()
    registry.register(
        LocalCatalogProvider(_ReadOnlyDatabase(async_sessionmaker(engine)))
    )
    registry.register(ChunilibSnapshotProvider(snapshot, game_version))
    attempts: list[str] = []
    with deny_network(attempts):
        try:
            await registry.start()
            local, source = await asyncio.gather(
                *(
                    registry.read_catalog(CatalogRequest(d.scope, provider_key=d.key))
                    for d in registry.descriptors
                )
            )
            report = plan_candidates(source, local)
            return {
                "schema_version": 1,
                "mode": "read_only_candidates",
                "canonical_ids_belong_to_supplied_database_only": True,
                "source_provenance": asdict(source.provenance),
                "local_provenance": asdict(local.provenance),
                "source_issues": [asdict(i) for i in source.issues],
                "summary": {
                    "songs": dict(Counter(s.status for s in report.songs)),
                    "charts": dict(Counter(c.status for c in report.charts)),
                    "automatically_confirmed": 0,
                },
                "candidates": asdict(report),
                "network_attempts": len(attempts),
            }
        finally:
            await registry.close()
            await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--catalog-db",
        type=Path,
        required=True,
        help="Explicit SQLite catalog copy, opened mode=ro.",
    )
    parser.add_argument("--game-version", default="crossversex")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run_check(args.snapshot, args.catalog_db, args.game_version))
    serialized = json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        # Refuse to overwrite the source database, snapshot or an existing report.
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    else:
        print(serialized)


if __name__ == "__main__":
    main()
