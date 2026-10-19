"""Sanitized runtime check plus real, strictly read-only search/review reads."""

import asyncio
import contextlib
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Chart
from chuni_penguin.database.chart_reviews import REVIEW_TABLES
from chuni_penguin.services.catalog import AliasService
from chuni_penguin.services.chart_reviews import ChartReviewService
from chuni_penguin.services.song_discovery import SongDiscoveryService
from chuni_penguin.ui.chart_reviews import summary_embed
from workspace.check_annotation_runtime import main as annotation_check

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data/penguin.sqlite3"


async def verify_reader(database_path=LIVE):
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=database_path.as_uri(),
            query={"mode": "ro", "uri": "true"},
        ),
        hide_parameters=True,
    )
    try:
        maker = async_sessionmaker(engine)
        database = SimpleNamespace(
            mapping_read_sessionmaker=maker, read_sessionmaker=maker
        )
        async with maker() as session:
            chart = await session.scalar(
                select(Chart).options(joinedload(Chart.song)).limit(1)
            )
            assert chart is not None
        summary = await ChartReviewService(database).public_summary(chart.id)
        embed = summary_embed(chart, summary)
        assert len(embed) <= 6000
        assert "discord_id" not in str(embed.to_dict())
        aliases = AliasService(database)
        await aliases.reload()
        found = await SongDiscoveryService(database, aliases).search(chart.song.title)
        assert any(candidate.song.id == chart.song_id for candidate in found.candidates)
        return {
            "anonymous_review_reader": True,
            "song_discovery_reader": True,
            "readonly_sqlite_uri": True,
            "player_rows_written": False,
        }
    finally:
        await engine.dispose()


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        code = annotation_check()
    result = json.loads(
        (ROOT / "reports/annotation-runtime-20261016.json").read_text(encoding="utf-8")
    )
    with contextlib.closing(
        sqlite3.connect(LIVE.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        result["review_tables"] = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in REVIEW_TABLES
        }
    result.update(asyncio.run(verify_reader()))
    result["complete"] = (
        code == 0
        and result["anonymous_review_reader"]
        and result["song_discovery_reader"]
    )
    result["discord_visual_acceptance"] = "pending_user_testing"
    (ROOT / "reports/embed-features-runtime-20261016.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
