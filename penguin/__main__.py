import argparse
import asyncio
import json
from pathlib import Path


async def initialize(path):
    import logging

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import create_async_engine

    from chuni_penguin.database import Chart, Song
    from chuni_penguin.database.base import Base
    from chuni_penguin.logging import logger

    logging.getLogger("chuni_penguin").setLevel(logging.WARNING)
    from dbutils.seeds import SeedsRepository, load_seeds

    path = path.resolve()
    if path.exists():
        raise SystemExit(
            "Refusing to initialize an existing database. Back it up and use an explicit migration procedure."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve exclusively before SQLite opens it, to avoid overwriting another initializer.
    with path.open("xb"):
        pass
    engine = create_async_engine("sqlite+aiosqlite:///" + path.as_posix())
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await load_seeds(
            logger,
            engine,
            SeedsRepository(logger, Path("chuni_penguin/database/seeds")),
        )
        async with engine.connect() as conn:
            songs = await conn.scalar(select(func.count()).select_from(Song))
            charts = await conn.scalar(select(func.count()).select_from(Chart))
        print(
            json.dumps(
                {
                    "database": str(path),
                    "songs": songs,
                    "charts": charts,
                    "network": False,
                }
            )
        )
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="CHUNITHM Penguin v2 local tools")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init",
        help="Create a new local database from A's public seeds; refuses existing databases",
    )
    init.add_argument("--database", type=Path, default=Path("data/penguin.sqlite3"))
    commands.add_parser(
        "check",
        help="Validate startup in an isolated fixture; does not log into Discord",
    )
    live = commands.add_parser(
        "discord-check",
        help="Bounded official Discord reads with temporary data; never sends messages or registers commands",
    )
    live.add_argument("--env-file", type=Path, required=True)
    live.add_argument(
        "--gateway",
        action="store_true",
        help="Also connect until READY, then disconnect",
    )
    live.add_argument("--output", type=Path, default=Path("reports/discord-live.json"))
    source = commands.add_parser(
        "source-check", help="Read the explicit owner's Chunirec data without saving it"
    )
    source.add_argument("--env-file", type=Path, required=True)
    source.add_argument("--output", type=Path, default=Path("reports/source-live.json"))
    bind = commands.add_parser(
        "bind-manual", help="Save an explicitly operator-reviewed B annotation binding"
    )
    bind.add_argument("--row", type=int, required=True)
    bind.add_argument("--song-id", type=int, required=True)
    bind.add_argument(
        "--difficulty", choices=["BAS", "ADV", "EXP", "MAS", "ULT"], required=True
    )
    bind.add_argument("--reviewer", required=True)
    bind.add_argument("--evidence", required=True)
    annotations = commands.add_parser(
        "import-annotations",
        help="Validate community XLSX annotations; dry run unless --apply",
    )
    annotations.add_argument(
        "--database", type=Path, default=Path("data/penguin.sqlite3")
    )
    annotations.add_argument(
        "--workbook",
        type=Path,
        default=Path("outputs/chart-mapping-20261015/luna/ChunithmChart_reviewed.xlsx"),
    )
    annotations.add_argument(
        "--manifest",
        type=Path,
        default=Path("outputs/chart-mapping-20261015/bm25-matches.json"),
    )
    annotations.add_argument(
        "--review",
        type=Path,
        default=Path("outputs/chart-mapping-20261015/luna/review.json"),
    )
    annotations.add_argument(
        "--legacy", type=Path, default=Path("snapshots/manual.json")
    )
    annotations.add_argument("--apply", action="store_true")
    rollback = commands.add_parser(
        "rollback-annotations",
        help="Deactivate a specific annotation batch only; dry run unless --apply",
    )
    rollback.add_argument("--database", type=Path, default=Path("data/penguin.sqlite3"))
    rollback.add_argument("--batch-id", required=True)
    rollback.add_argument("--apply", action="store_true")
    reviews = commands.add_parser(
        "review-schema", help="Inspect additive review tables; dry run unless --apply"
    )
    reviews.add_argument("--database", type=Path, default=Path("data/penguin.sqlite3"))
    reviews.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "review-schema":
        import sqlite3

        from penguin.review_schema import install_review_schema

        try:
            result = install_review_schema(args.database, apply=args.apply)
        except ValueError as error:
            print(json.dumps({"complete": False, "failure": str(error)}))
            raise SystemExit(1) from None
        except (OSError, sqlite3.Error):
            print(
                json.dumps({"complete": False, "failure": "review_storage_unavailable"})
            )
            raise SystemExit(1) from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command in ("import-annotations", "rollback-annotations"):
        import sqlite3

        from penguin.annotation_import import import_annotations, rollback_annotations

        try:
            if args.command == "import-annotations":
                from penguin.annotation_source import load_source

                result = import_annotations(
                    args.database,
                    load_source(args.workbook, args.manifest, args.review, args.legacy),
                    apply=args.apply,
                )
            else:
                result = rollback_annotations(
                    args.database, args.batch_id, apply=args.apply
                )
        except ValueError as exc:
            print(json.dumps({"complete": False, "failure": str(exc)}))
            raise SystemExit(1) from None
        except (OSError, sqlite3.Error):
            print(
                json.dumps(
                    {"complete": False, "failure": "annotation_storage_unavailable"}
                )
            )
            raise SystemExit(1) from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command in ("discord-check", "source-check"):
        from chuni_penguin.application.credentials import (
            TokenConfigError,
            read_env_file,
            token_from_env,
        )
        from penguin.discord_check import (
            check_sources,
            run_discord_check,
            suppress_logs,
        )

        suppress_logs()
        try:
            values = read_env_file(args.env_file)
            if args.command == "discord-check":
                raw_guild = values.get("TEST_GUILD_ID")
                if raw_guild and (
                    not raw_guild.isdecimal() or not 0 < int(raw_guild) < 2**64
                ):
                    raise TokenConfigError("env_invalid_test_guild_id")
                result = asyncio.run(
                    run_discord_check(
                        token_from_env(values),
                        gateway=args.gateway,
                        guild_id=int(raw_guild) if raw_guild else None,
                    )
                )
            else:
                result = asyncio.run(check_sources(values))
        except TokenConfigError as exc:
            result = {"complete": False, "failure": str(exc)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        summary = {k: v for k, v in result.items() if k != "startup"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        passed = (
            result.get("complete")
            if args.command == "discord-check"
            else result.get("state") == "verified"
        )
        raise SystemExit(0 if passed else 1)
    elif args.command == "init":
        asyncio.run(initialize(args.database))
    elif args.command == "bind-manual":
        from chuni_penguin.services.phase1_charts import ROOT
        from penguin.manual_review import bind_manual

        try:
            bind_manual(
                ROOT,
                row=args.row,
                song_id=args.song_id,
                difficulty=args.difficulty,
                reviewer=args.reviewer,
                evidence=args.evidence,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print(
            "Archived review association saved. Production reads the database; use import-annotations to update it."
        )
    else:
        from chuni_penguin.application.offline import run_check

        result = asyncio.run(run_check())
        Path("reports").mkdir(exist_ok=True)
        Path("reports/startup.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed = [
            name
            for name, state in result["extensions"].items()
            if state["state"] not in ("loaded", "disabled")
        ]
        if failed or result["network_attempts"] or result["pending_tasks"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
