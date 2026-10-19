"""Check the reported song's actual player-facing version labels, read-only."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import joinedload

from chuni_penguin.database import Chart
from chuni_penguin.services.catalog import AliasService
from chuni_penguin.services.song_details import SongDetailsData
from chuni_penguin.services.song_discovery import SongDiscoveryService
from chuni_penguin.ui.song_details import SongDetailsView
from chuni_penguin.ui.song_search import SongCandidateSelect, candidate_description

ROOT = Path(__file__).resolve().parents[1]


async def main():
    engine = create_async_engine(
        URL.create(
            "sqlite+aiosqlite",
            database=(ROOT / "data/penguin.sqlite3").as_uri(),
            query={"mode": "ro", "uri": "true"},
        ),
        hide_parameters=True,
    )
    try:
        maker = async_sessionmaker(engine)
        database = SimpleNamespace(
            read_sessionmaker=maker, mapping_read_sessionmaker=maker
        )
        aliases = AliasService(database)
        await aliases.reload()
        result = await SongDiscoveryService(database, aliases).search("gengaozo")
        candidate = result.candidates[0]
        async with maker() as session:
            charts = list(
                await session.scalars(
                    select(Chart)
                    .where(Chart.song_id.in_([song.id for song in candidate.songs]))
                    .options(
                        joinedload(Chart.song), joinedload(Chart.sdvxin_chart_view)
                    )
                )
            )
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=11),
            bot=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
        )
        data = SongDetailsData(ctx)
        for song in candidate.songs:
            data._cache[("charts", song.id)] = tuple(
                chart for chart in charts if chart.song_id == song.id
            )
        view = SongDetailsView(ctx, candidate.songs, data=data, variant_charts=charts)
        try:
            kwargs = await view._before_start()
            overview = next(
                field.value
                for embed in kwargs["embeds"]
                for field in embed.fields
                if field.name == "譜面版本"
            )
            expected = (
                "一般譜面 · 收錄版本 STAR · 可遊玩",
                "WORLD'S END · 狂☆☆☆☆☆ · 收錄版本 STAR · 已下架",
                "WORLD'S END · 敷☆☆ · 收錄版本 AMAZON · 已下架",
                "WORLD'S END · 覚☆☆☆ · 收錄版本 PARADISE · 已下架",
            )
            assert all(label in overview for label in expected)
            assert all(str(song.id) not in overview for song in candidate.songs)
            chooser = next(
                item
                for item in view.children
                if getattr(item, "custom_id", "") == "song_details:difficulty"
            )
            assert len(chooser.options) == 7
            assert len(view.to_components()) <= 5
            assert sum(len(embed) for embed in kwargs["embeds"]) <= 6000
            assert all(
                len(option.label) <= 100 and len(option.description or "") <= 100
                for option in chooser.options
            )
            search_option = SongCandidateSelect(result).options[0]
            assert (
                "一般譜面" in search_option.description
                and "收錄版本 STAR" in search_option.description
            )
            assert "ID" not in search_option.description
            report = {
                "complete": True,
                "query": "gengaozo",
                "readonly_uri": True,
                "overview_labels": list(expected),
                "candidate_description": candidate_description(candidate),
                "difficulty_options": [
                    {"label": option.label, "description": option.description}
                    for option in chooser.options
                ],
                "current_footer": kwargs["embeds"][-1].footer.text,
                "raw_ids_not_primary_labels": True,
                "discord_component_limits": True,
                "matching_and_associations_changed": False,
            }
            (ROOT / "reports/song-version-labels-20261016.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            await view.deactivate()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
