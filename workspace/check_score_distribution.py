"""Exercise actual non-bundled charts through the public source and C renderer."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Chart
from chuni_penguin.services.phase1_charts import Phase1Charts
from chuni_penguin.ui.song_details import SongDetailsView

ROOT = Path(__file__).resolve().parents[1]


async def main():
    output = ROOT / "outputs/distribution-check-20261019"
    output.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=(ROOT / "data/penguin.sqlite3").as_uri(),
            query={"mode": "ro", "uri": "true"},
        )
    )
    service = Phase1Charts(allow_network=True)
    report = []
    try:
        sessions = async_sessionmaker(engine)
        for song_id, difficulty, name in [
            (506, "MAS", "gengaozo-master"),
            (506, "EXP", "gengaozo-expert"),
            (317, "ULT", "air-ultima"),
        ]:
            async with sessions() as db:
                chart = await db.scalar(
                    select(Chart)
                    .options(joinedload(Chart.song))
                    .where(Chart.song_id == song_id, Chart.difficulty == difficulty)
                )
            assert chart is not None
            bundled = service.statistic_for(chart) is not None
            try:
                origin, data = await service.analysis(chart)
                assert data.startswith(b"\x89PNG\r\n\x1a\n")
                path = output / (name + ".png")
                path.write_bytes(data)
                ctx = SimpleNamespace(
                    author=SimpleNamespace(id=0),
                    bot=SimpleNamespace(phase1_charts=service),
                )
                view = SongDetailsView(
                    ctx,
                    [chart.song],
                    initial_tab="rating",
                    difficulty=difficulty,
                    data=SimpleNamespace(
                        charts=AsyncMock(return_value=[chart]), close=AsyncMock()
                    ),
                )
                with patch(
                    "chuni_penguin.services.phase1_charts.collect_source",
                    side_effect=AssertionError("A verified cache must not fetch again"),
                ) as collector:
                    rendered = await view._before_start()
                    collector.assert_not_called()
                attachment = rendered["files"][0]
                assert attachment.filename == "score-distribution.png"
                assert attachment.fp.read() == data
                assert (
                    rendered["embeds"][0].image.url
                    == "attachment://score-distribution.png"
                )
                assert len(view.to_components()) <= 5
                attachment.close()
                await view.deactivate()
                report.append(
                    {
                        "song_id": song_id,
                        "chart_id": chart.id,
                        "title": chart.song.title,
                        "difficulty": difficulty,
                        "bundled_snapshot_exists": bundled,
                        "source": origin,
                        "image_bytes": len(data),
                        "image": str(path),
                        "discord_png_attachment": True,
                        "rendered_canonical_chart_id": chart.id,
                        "cached_ui_render_performed_no_source_fetch": True,
                        "status": "passed",
                    }
                )
            except Exception as error:
                report.append(
                    {
                        "song_id": song_id,
                        "difficulty": difficulty,
                        "bundled_snapshot_exists": bundled,
                        "error_type": type(error).__name__,
                        "message": str(error),
                        "status": "failed",
                    }
                )
        (ROOT / "reports/distribution-live-20261019.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await service.close()
        await engine.dispose()
    return 0 if all(row["status"] == "passed" for row in report) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
