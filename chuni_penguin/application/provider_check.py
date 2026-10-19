"""Check a local Chunilib snapshot alongside a temporary A fixture, offline."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from chuni_penguin.application.offline import create_fixture_database, deny_network
from chuni_penguin.application.services import ApplicationServices
from chuni_penguin.config import Config
from chuni_penguin.providers.models import CatalogRequest
from chuni_penguin.providers.registry import ProviderError


async def run_check(snapshot: Path, game_version: str = "crossversex"):
    attempts: list[str] = []
    with TemporaryDirectory(prefix="chuni-provider-check-") as temporary:
        url = await create_fixture_database(Path(temporary) / "fixture.sqlite3")
        settings = Config.from_mapping(
            {
                "bot": {"db_connection_string": url},
                "providers": {
                    "enabled": "chunilib_snapshot, a_catalog",
                    "chunilib_snapshot_path": str(snapshot),
                    "chunilib_game_version": game_version,
                },
            }
        )
        services = ApplicationServices(
            settings, {}, AsyncMock(return_value=True), lambda: None, offline=True
        )
        results = {}
        with deny_network(attempts):
            try:
                await services.start({"providers"})
                for descriptor in services.providers.descriptors:
                    try:
                        result = await services.providers.read_catalog(
                            CatalogRequest(
                                descriptor.scope,
                                provider_key=descriptor.key,
                            )
                        )
                        results[descriptor.key] = {
                            "songs": len(result.songs),
                            "charts": sum(len(s.charts) for s in result.songs),
                            "issues": len(result.issues),
                            "provenance": asdict(result.provenance),
                        }
                    except ProviderError as exc:
                        results[descriptor.key] = {"error": exc.reason}
                states = services.providers.status()
            finally:
                await services.close()
        return {
            "complete": set(states) == {"a_catalog", "chunilib_snapshot"}
            and all(s["ready"] for s in states.values())
            and all("error" not in r for r in results.values()),
            "providers": states,
            "catalogs": results,
            "network_attempts": len(attempts),
            "pending_tasks": [
                t.get_name()
                for t in asyncio.all_tasks()
                if t is not asyncio.current_task() and not t.done()
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--game-version", default="crossversex")
    args = parser.parse_args()
    report = asyncio.run(run_check(args.snapshot, args.game_version))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
